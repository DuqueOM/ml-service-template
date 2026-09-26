#!/usr/bin/env python3
"""Contract: a repo-relative path named in living documentation — or in a
code comment — must resolve.

Why this script exists
----------------------
The Copier migration (ADR-030, commit ``fe89e92``) relocated the whole
scaffolder payload from ``templates/{cicd,k8s,monitoring,docs,eda,infra,
common_utils}`` to ``templates/service/*``. It updated every reference
that *executes* — workflows, ``Makefile``, ``CODEOWNERS``, kustomizations,
tests, and the drift-gate script itself, 14 files in all — because those
break loudly when a path is wrong.

It updated none of the prose. Thirty documents kept pointing at
directories that no longer existed, and nothing noticed, because no check
in this repo verified that a path named in a document exists. Two audit
rounds later, commit ``f219895`` copied the dead ``templates/cicd/`` path
*into the audit tooling itself* — the glob scope of
``agentic/rules/18-audit-quality.md`` and the Q-01 unpinned-action sweep
in ``agentic/workflows/audit-quality.md`` — where it propagated across
three adapter surfaces and sat there, inert and green.

The lesson generalises past that one migration: **documentation that
names a path is making a checkable claim, and an unchecked claim rots.**
This script checks it.

The dual-perspective model
--------------------------
A path reference is valid if it resolves from EITHER of two roots:

1. the repository root — the perspective of the template repo's own docs;
2. ``templates/service/`` — the perspective of a *generated service*, for
   documents that ship into one.

The second root matters because ``agentic/**`` is mirrored byte-for-byte
into ``templates/service/agentic/`` (enforced by
``check_vendored_runtime_drift.py``), and several root documents
(``AGENTS.md``, ``AGENT_CONTEXT.md``) are vendored verbatim as well. One
text has to serve both readers, so ``scripts/refresh_contract.py`` is
correct prose in a rule that executes inside a scaffolded service even
though this repo only has that file at
``templates/service/scripts/refresh_contract.py``.

Files that ship into a service are resolved against both roots. Files
that never leave the template repo are resolved against the repo root
only, which is the stricter rule.

What is NOT checked
-------------------
- Frozen records: ``docs/decisions/``, ``docs/audit/``, ``releases/``,
  ``CHANGELOG.md``, ``VALIDATION_LOG.md``. An ADR describing the state of
  the tree in May 2026 is *supposed* to name paths that have since moved;
  rewriting them would falsify the record.
- Non-literal tokens: anything carrying a placeholder (``<id>``,
  ``{service_slug}``), a glob, a brace set, shell arguments, a ``file:line``
  suffix, a URL fragment, or an ellipsis (``...`` or ``…``). Those are illustrative, not
  claims about the tree.
- Paths outside the repo's own top-level directories. A reference to
  ``src/main.py`` inside a runbook describes the adopter's tree, not ours.

Baseline
--------
``.doc-path-baseline.yml`` carries the references that do not resolve but
are not defects. Every entry declares a ``kind:``, and the two kinds are
verified differently because they are not the same claim.

``unimplemented`` — the documentation promises something never built. The
claim is "we intend to fix this", and the only honest check is a deadline,
so these carry an ``expiry:`` and fail once past it.

``runtime-artifact`` — the file really is created while a generated service
runs. The claim is "this resolves at runtime, and X is what creates it",
which is **checkable now**, so these carry a ``created-by:`` instead of a
date. The gate asserts the named creator still exists and still references
the path. A date here would have been ceremony: the condition never
changes, so an expiry could only ever be bumped, which trains reviewers to
bump dates without reading them — and that degrades the mechanism for the
entries where the deadline is the whole point.

New dead paths are never baselined: they fail on the PR that introduces
them, which is the failure mode that would have caught ``f219895``.

Exit codes
----------
- 0: every reference resolves, or is baselined and still justified.
- 1: an unresolved reference is not baselined; an ``unimplemented`` entry
     has expired; a ``runtime-artifact`` entry's creator is gone or no
     longer names the path; or any entry now resolves.
- 2: setup error (unreadable or malformed baseline).

Usage
-----
::

    python3 scripts/check_doc_path_refs.py
    python3 scripts/check_doc_path_refs.py --list      # print unresolved refs
    python3 scripts/check_doc_path_refs.py --as-of 2027-01-01
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = REPO_ROOT / "templates" / "service"
BASELINE_FILE = REPO_ROOT / ".doc-path-baseline.yml"

# Frozen records — a point-in-time document must keep its point-in-time paths.
FROZEN_PREFIXES = (
    "docs/decisions/",
    "docs/audit/",
    "releases/",
    "templates/service/docs/decisions/",
    "templates/service/docs/audit/",
)
FROZEN_FILES = {"CHANGELOG.md", "VALIDATION_LOG.md"}

# Surfaces that ship into a generated service, and so read correctly from
# either root. `.devin/`, `.cursor/`, `.claude/`, `.codex/` and `.agents/`
# (the skills Cursor and Codex share, ADR-027 §9) are generated from
# `agentic/` by sync_agentic_adapters.py and inherit its perspective.
DUAL_PREFIXES = (
    "agentic/",
    ".agents/",
    ".devin/",
    ".cursor/",
    ".claude/",
    ".codex/",
    "templates/service/",
)
# Root documents vendored verbatim into templates/service/ (byte-identical
# per check_vendored_runtime_drift.py), so they read from both roots too.
DUAL_FILES = {"AGENTS.md", "AGENT_CONTEXT.md"}

# Only these prefixes name paths inside THIS repo. Everything else in a
# runbook (`src/`, `data/`, `artifacts/`) describes the adopter's tree.
REPO_PREFIXES = (
    "templates/",
    "scripts/",
    "docs/",
    "agentic/",
    ".github/",
    "examples/",
    ".security-baselines/",
)

# Characters that mark a token as illustrative rather than a literal path.
_NON_LITERAL = set(" <>{}*,()|$#\\:!?\"'")

_BACKTICKED = re.compile(r"`([^`\n]+)`")

# Markdown link targets. Unlike a code span, a link target is resolved
# RELATIVE TO THE FILE THAT CONTAINS IT — the distinction that produced the
# only broken link this repo had (`templates/service/README.md` pointing at
# `templates/service/docs/CCDS_MAPPING.md`, which from inside that directory
# means `templates/service/templates/service/docs/...`).
#
# The `Link Check` job also validates these, but only on files changed by the
# PR, and only when the PR touches a `.md` at all. A link breaks when its
# TARGET moves, not when the linking file changes — so that case is invisible
# at PR time and surfaces up to a week later in the Monday scan, on main.
# External URLs stay with Link Check, where network latency is tolerable
# because it does not gate a merge.
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

# Code files carry the same claims in comments. The scan was `.md`/`.txt`
# only at first, which left `.security-baselines/tfsec.yml` justifying three
# HIGH suppressions against a directory ADR-030 had deleted, and a handful of
# test comments describing a layout that no longer existed. Measured before
# widening: 15 unresolved paths across 309 code files — small enough for a
# hard gate, unlike a naive scan of every string literal.
_CODE_SUFFIXES = (".py", ".yml", ".yaml", ".sh")
_CODE_FILENAMES = ("Makefile", "makefile", "GNUmakefile")
# A path-shaped token in a comment. Anchored on the comment marker so code
# that legitimately builds a path at runtime is not mistaken for a claim.
# The negative lookahead drops tokens that continue into a glob or brace set
# (`deploy-*.yml`, `deploy-{gcp,aws}.yml`): the character class stops at the
# metacharacter, and without the lookahead the truncated prefix
# `.../workflows/deploy-` would be reported as a dead path.
_COMMENT_PATH = re.compile(
    r"(?:^|\s|\(|`)((?:templates|scripts|docs|agentic|examples|\.github)/[A-Za-z0-9_./-]+)(?![A-Za-z0-9_./-]*[{*?\[])"
)

# Placeholders that survive `_is_literal_path` because they contain no
# rejected character: `ADR-XXX.md`, `v0.NN.0`. They are stand-ins, not claims.
_UPPER_PLACEHOLDER = re.compile(r"(?:^|[-_/.])(?:XXX+|NNN?|YYYY|MM|DD)(?:[-_/.]|$)")


def _tracked_docs() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p.endswith((".md", ".txt"))]


def _tracked_code() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p and (p.endswith(_CODE_SUFFIXES) or Path(p).name in _CODE_FILENAMES)]


def _is_frozen(path: str) -> bool:
    return path.startswith(FROZEN_PREFIXES) or path in FROZEN_FILES


def _is_dual(path: str) -> bool:
    return path.startswith(DUAL_PREFIXES) or path in DUAL_FILES


def _is_literal_path(token: str) -> bool:
    if not token.startswith(REPO_PREFIXES):
        return False
    if "..." in token or "\u2026" in token or _UPPER_PLACEHOLDER.search(token):
        return False
    # `*.local.*` files are gitignored by contract — a comment naming one is
    # describing something that must NOT exist, not claiming that it does.
    if ".local." in token:
        return False
    return not (_NON_LITERAL & set(token))


def _resolves(token: str, dual: bool) -> bool:
    if (REPO_ROOT / token).exists():
        return True
    return bool(dual and (SERVICE_ROOT / token).exists())


def collect_unresolved() -> dict[str, set[str]]:
    """Return ``{unresolved_path: {document, ...}}`` across living docs."""
    found: dict[str, set[str]] = {}
    for doc in _tracked_docs():
        if _is_frozen(doc):
            continue
        try:
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # A code span may contain link-shaped text as an example
        # (`[text](path)`), which is documentation about links, not a link.
        text_outside_code = _BACKTICKED.sub(" ", text)
        for match in _MD_LINK.finditer(text_outside_code):
            target = match.group(1).split("#", 1)[0]
            if not target or target.startswith(("http", "mailto:", "/")):
                continue  # external, or site-root — Link Check's territory
            if any(c in target for c in "<>{}*"):
                continue  # placeholder, not a claim
            if (REPO_ROOT / doc).parent.joinpath(target).exists():
                continue
            found.setdefault(f"{doc} -> {target}", set()).add(doc)

        dual = _is_dual(doc)
        for match in _BACKTICKED.finditer(text):
            token = match.group(1).strip().rstrip(",.;").rstrip("/")
            if not _is_literal_path(token) or _resolves(token, dual):
                continue
            found.setdefault(token, set()).add(doc)

    for src in _tracked_code():
        if _is_frozen(src):
            continue
        path = REPO_ROOT / src
        if not path.is_file():  # Copier-token filenames are not on disk
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        dual = _is_dual(src)
        for line in text.splitlines():
            stripped = line.lstrip()
            comment = None
            if stripped.startswith(("#", "//")):
                comment = stripped
            elif " # " in line:
                comment = line.split(" # ", 1)[1]
            if comment is None:
                continue
            for match in _COMMENT_PATH.finditer(comment):
                raw = match.group(1).strip()
                token = raw if raw.endswith("...") else raw.rstrip(",.;:").rstrip("/")
                if not _is_literal_path(token) or _resolves(token, dual):
                    continue
                found.setdefault(token, set()).add(src)
    return found


class BaselineEntry:
    """One tolerated non-resolving reference, and how its claim is verified."""

    __slots__ = ("path", "kind", "reason", "expiry", "created_by")

    def __init__(
        self,
        path: str,
        kind: str,
        reason: str,
        expiry: _dt.date | None,
        created_by: str | None,
    ) -> None:
        self.path = path
        self.kind = kind
        self.reason = reason
        self.expiry = expiry
        self.created_by = created_by


def _parse_baseline() -> tuple[dict[str, BaselineEntry], list[str]]:
    """Parse ``.doc-path-baseline.yml`` without a yaml dependency.

    Entry shapes::

        - path: docs/concept_drift_log.md
          kind: runtime-artifact
          created-by: agentic/skills/concept-drift-analysis/SKILL.md
          reason: appended by that skill inside a generated service

        - path: scripts/something.py
          kind: unimplemented
          expiry: 2026-12-01
          reason: the release-checklist workflow instructs running it
    """
    if not BASELINE_FILE.exists():
        return {}, []
    entries: dict[str, BaselineEntry] = {}
    errors: list[str] = []
    field: dict[str, str] = {}

    def flush() -> None:
        if not field.get("path"):
            field.clear()
            return
        path, kind = field["path"], field.get("kind", "")
        reason, expiry, created_by = field.get("reason"), field.get("expiry"), field.get("created-by")

        if kind not in ("unimplemented", "runtime-artifact"):
            errors.append(f"baseline entry '{path}': kind must be unimplemented or runtime-artifact, got '{kind}'")
        elif not reason:
            errors.append(f"baseline entry '{path}' has no reason:")
        elif kind == "unimplemented":
            if not expiry:
                errors.append(f"baseline entry '{path}' is unimplemented and has no expiry:")
            else:
                try:
                    entries[path] = BaselineEntry(path, kind, reason, _dt.date.fromisoformat(expiry), None)
                except ValueError:
                    errors.append(f"baseline entry '{path}' has malformed expiry '{expiry}'")
        else:  # runtime-artifact
            if not created_by:
                errors.append(
                    f"baseline entry '{path}' is a runtime-artifact and has no created-by: — "
                    "the claim that something creates it at runtime has to name what does, "
                    "or it cannot be verified"
                )
            elif expiry:
                errors.append(
                    f"baseline entry '{path}' is a runtime-artifact and carries an expiry: — "
                    "its claim is verified against created-by, not against a date"
                )
            else:
                entries[path] = BaselineEntry(path, kind, reason, None, created_by)
        field.clear()

    for raw in BASELINE_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- path:"):
            flush()
            field["path"] = line.split(":", 1)[1].strip()
        elif ":" in line and not line.startswith("-"):
            key, value = line.split(":", 1)
            if key in ("kind", "reason", "expiry", "created-by"):
                field[key] = value.strip()
    flush()
    return entries, errors


def _verify_runtime_artifact(entry: BaselineEntry) -> str | None:
    """A runtime-artifact claim names its creator; check the claim still holds."""
    assert entry.created_by is not None
    creator = REPO_ROOT / entry.created_by
    if not creator.is_file():
        return f"baseline entry '{entry.path}' names created-by '{entry.created_by}', which does not exist"
    try:
        text = creator.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"baseline entry '{entry.path}': created-by '{entry.created_by}' is unreadable"
    if entry.path not in text:
        return (
            f"baseline entry '{entry.path}': its declared creator "
            f"'{entry.created_by}' no longer references that path, so nothing "
            "is producing it at runtime any more"
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print unresolved refs and exit 0")
    parser.add_argument("--as-of", default=None, help="evaluate expiries against YYYY-MM-DD")
    args = parser.parse_args()

    as_of = _dt.date.fromisoformat(args.as_of) if args.as_of else _dt.date.today()
    unresolved = collect_unresolved()

    if args.list:
        for token, docs in sorted(unresolved.items()):
            print(f"{token}  <- {', '.join(sorted(docs))}")
        return 0

    baseline, errors = _parse_baseline()
    if errors:
        for err in errors:
            sys.stderr.write(f"::error::{err}\n")
        return 2

    new = {t: d for t, d in unresolved.items() if t not in baseline}
    expired = sorted(t for t, e in baseline.items() if t in unresolved and e.expiry is not None and e.expiry < as_of)
    obsolete = sorted(t for t in baseline if t not in unresolved)
    unverified = [
        msg
        for t, e in sorted(baseline.items())
        if t in unresolved and e.kind == "runtime-artifact"
        for msg in [_verify_runtime_artifact(e)]
        if msg is not None
    ]

    if new:
        sys.stderr.write(
            "FAIL: documentation names repo paths that do not exist.\n"
            "A path in prose is a checkable claim; these do not resolve from the\n"
            "repo root, nor from templates/service/ for docs that ship into a\n"
            "generated service.\n\n"
        )
        for token, docs in sorted(new.items()):
            sys.stderr.write(f"  - {token}\n")
            for doc in sorted(docs):
                sys.stderr.write(f"      referenced by {doc}\n")
        sys.stderr.write(
            "\nFix: point the reference at the real path. If the target is created\n"
            "at runtime or is deliberately aspirational, add it to\n"
            ".doc-path-baseline.yml with a reason and an expiry.\n"
        )

    for token in expired:
        expiry = baseline[token].expiry
        assert expiry is not None
        sys.stderr.write(
            f"::error::baseline entry '{token}' expired on {expiry.isoformat()} "
            "— fix the reference or re-justify with a new expiry\n"
        )
    for message in unverified:
        sys.stderr.write(f"::error::{message}\n")
    for token in obsolete:
        sys.stderr.write(
            f"::error::baseline entry '{token}' now resolves — remove it from "
            ".doc-path-baseline.yml so the baseline stays honest\n"
        )

    if new or expired or obsolete or unverified:
        return 1

    docs_n, code_n = len(_tracked_docs()), len(_tracked_code())
    print(
        f"[doc-path-refs] OK — {docs_n} documents + {code_n} code files scanned, "
        f"every repo path reference resolves ({len(baseline)} baselined, all justified)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
