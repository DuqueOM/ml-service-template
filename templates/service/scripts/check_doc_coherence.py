#!/usr/bin/env python3
"""Cross-document coherence gate for the template's enterprise documentation.

Rule 16 (`agentic/rules/16-doc-coherence.md`) + ADR-031 declare a single
source of truth for every fact that is restated across more than one
document, and forbid those restatements from drifting apart. This script is
the deterministic enforcement layer for that contract — the agentic
`doc-coherence` skill is the productivity multiplier that *fixes* drift;
this gate is what *fails CI* when drift exists (README §"agentic surface is
not load-bearing": agents accelerate, tests/CI enforce).

It is intentionally a sibling of the existing ``check_*_drift.py`` family
(``check_common_utils_drift.py``, ``check_cicd_template_drift.py``,
``check_dashboard_inventory.py``): no third-party deps, repo-root relative,
``main() -> int`` with 0/1 exit codes, one ``[doc-coherence]`` print prefix.

Checks
------
C1  Version single source of truth — ``VERSION`` must equal the latest
    *released* (dated, non-``[Unreleased]``) heading in ``CHANGELOG.md``.
C2  llms.txt version coherence — the ``> Version:`` line must reference the
    current ``VERSION`` (or carry an explicit ``legacy-snapshot`` marker).
C3  Anti-pattern count coherence — the highest ``D-NN`` defined in
    ``AGENTS.md`` must match the ``N anti-patterns`` count claimed in
    ``README.md`` and the ``(D-01 to D-NN)`` range printed in ``llms.txt``.
C4  Agentic surface counts — the live count of ``agentic/rules/*.md``,
    ``agentic/skills/*/SKILL.md`` and ``agentic/workflows/*.md`` must match
    the ``N rules + N skills + N workflows`` line in ``CLAUDE.md``.
C5  ADR traceability — every integer in ``[1 .. max ADR]`` must have a file
    in ``docs/decisions/``; a missing number is only allowed if a tombstone
    file documents it (``Status: Withdrawn``). Catches silently dropped ADRs
    and undocumented numbering gaps.
C6  Release note existence — the current ``VERSION`` must have a matching
    ``releases/vX.Y.Z.md``. ``docs/RELEASING.md`` requires one per release,
    and ``.github/workflows/release-on-tag.yml`` prefers it as the source
    for the GitHub Release title + body; without it, a tag push falls back
    to a generic, unpolished release body (the exact failure this check
    exists to catch before it ships — see that workflow's file header for
    the 2026-07-01 incident this closes).
C7  Documentation language + private-reference guard (D-37) — every file under
    ``docs/`` and every root-level ``*.md`` must be English-only and must
    never name a known private/personal repo. AUDIT R10 (2026-07-02) found
    four ``docs/audit/*.md`` files fully in Spanish and a private repo
    named in six public-repo files, once as a live clickable URL that 404s
    for any public reader. Both are the same failure mode — something true
    only in an interactive/private authoring context leaking into the
    public tree — so this check is the deterministic backstop that keeps
    it from recurring silently.

Exit codes
----------
- 0: all coherence checks pass.
- 1: at least one document has drifted from its single source of truth.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

VERSION_FILE = REPO_ROOT / "VERSION"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
LLMS = REPO_ROOT / "llms.txt"
README = REPO_ROOT / "README.md"
AGENTS = REPO_ROOT / "AGENTS.md"
CLAUDE = REPO_ROOT / "CLAUDE.md"
RULES_DIR = REPO_ROOT / "agentic" / "rules"
SKILLS_DIR = REPO_ROOT / "agentic" / "skills"
WORKFLOWS_DIR = REPO_ROOT / "agentic" / "workflows"
ADR_DIR = REPO_ROOT / "docs" / "decisions"
RELEASES_DIR = REPO_ROOT / "releases"
DOCS_DIR = REPO_ROOT / "docs"

# Case-insensitive, whole-word Spanish markers that essentially never appear
# in legitimate English technical prose. Deliberately a word list rather than
# a raw accented-character scan: this repo's own agentic/ canon legitimately
# cites accented proper nouns (e.g. "Diátaxis" the doc-taxonomy framework,
# "Cramér's V" the statistics measure) that a character scan would misflag.
#
# The accent is REQUIRED, never an optional fallback: "decisión"/"revisión"/
# "conclusión" minus their accent spell exactly the English words
# "decision"/"revision"/"conclusion" (unlike "-ción" words, which keep a
# trailing "n" where English has "-tion"). An early draft of this pattern
# made the accent optional per letter and matched nearly every English ADR
# in the repo on the word "decision" alone — caught in local testing before
# this shipped, not left as a lesson for CI to teach the hard way.
_SPANISH_MARKERS = re.compile(
    r"\b(aunque|también|además|cuáles?|cuándo|dónde|"
    r"sin embargo|por lo tanto|así como|deberían?|realiza|actualiza|"
    r"hallazgo|auditoría|alcance|fecha|integración|ingeniería|"
    r"según|entrevista|corrección(?:es)?|revisión|preguntas?|"
    r"respuesta|veredicto|decisión(?:es)?|información|"
    r"configuración|documentación|implementación|validación|"
    r"verificación|generación|resumen|conclusión|introducción|"
    r"adopción|credibilidad|infraestructura|estratégicos?|"
    r"desbalance|sobre-ingeniería)\b",
    re.IGNORECASE,
)

# Private/personal repos that must never be named in this public repo's
# documentation (AUDIT R10, 2026-07-02 — see ADR-040 for the incident this
# closes). Extend this tuple if another private companion repo is ever
# referenced. Safe to keep as a literal here: this file is Python, not
# Markdown, so it is never itself in C7's own scan scope (see
# _doc_scan_files below).
_FORBIDDEN_REPO_REFS = ("guia_mlops",)


def _norm_version(raw: str) -> str:
    """Strip a leading ``v`` and surrounding whitespace for comparison."""
    return raw.strip().lstrip("vV").strip()


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def check_version_sot() -> list[str]:
    """C1 — VERSION must equal the latest released CHANGELOG heading.

    Context-adaptive (the byte-identical vendored copy also runs inside a
    scaffolded service that tracks neither file): a repo that has no version
    artefacts has no version coherence to enforce, so it is skipped. The one
    asymmetry we still flag is a CHANGELOG that records releases with no
    VERSION file — that means the single source of truth was deleted.
    """
    problems: list[str] = []
    version_raw = _read(VERSION_FILE)
    changelog = _read(CHANGELOG)
    if version_raw is None and changelog is None:
        return []  # nothing versioned here → nothing to keep coherent
    if version_raw is None:
        released = re.findall(r"^##\s*\[(v?\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        if released:
            return [
                f"CHANGELOG.md records releases ({released[0]}) but no VERSION file exists — "
                f"the version single source of truth is missing."
            ]
        return []
    if changelog is None:
        return []  # a VERSION with no CHANGELOG to anchor against → soft skip

    version = _norm_version(version_raw)
    # Latest *released* heading: "## [vX.Y.Z] — DATE" (skip "[Unreleased]").
    released = re.findall(r"^##\s*\[(v?\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    if not released:
        return ["CHANGELOG.md has no released `## [vX.Y.Z]` heading to anchor VERSION."]
    latest = _norm_version(released[0])
    if version != latest:
        problems.append(
            f"VERSION ({version}) != latest released CHANGELOG entry ({latest}). "
            f"VERSION is the single source of truth — bump it on release or fix the heading."
        )
    return problems


def check_llms_version() -> list[str]:
    """C2 — llms.txt version line must track VERSION (or be marked legacy)."""
    version_raw = _read(VERSION_FILE)
    llms = _read(LLMS)
    if version_raw is None or llms is None:
        return []  # absence handled elsewhere
    version = _norm_version(version_raw)
    m = re.search(r"^>\s*Version:\s*([^\s|]+)", llms, re.MULTILINE)
    if not m:
        return ["llms.txt has no `> Version:` line to keep in sync with VERSION."]
    if "legacy-snapshot" in llms.lower():
        return []  # explicitly opted out of release-version tracking
    declared = _norm_version(m.group(1))
    if declared != version:
        return [
            f"llms.txt Version ({declared}) != VERSION ({version}). "
            f"Update llms.txt or mark it `legacy-snapshot` if intentional."
        ]
    return []


def _max_anti_pattern(text: str) -> int:
    nums = [int(n) for n in re.findall(r"\bD-(\d{2})\b", text)]
    return max(nums) if nums else 0


def check_anti_pattern_count() -> list[str]:
    """C3 — AGENTS max D-NN == README count == llms.txt range."""
    problems: list[str] = []
    agents = _read(AGENTS)
    readme = _read(README)
    llms = _read(LLMS)
    if agents is None:
        return []  # no canonical catalogue here → nothing to mirror
    canonical = _max_anti_pattern(agents)
    if canonical == 0:
        return []  # AGENTS.md present but defines no catalogue → nothing to check

    if readme is not None:
        m = re.search(r"(\d+)\s+anti-patterns", readme)
        if m and int(m.group(1)) != canonical:
            problems.append(
                f"README.md claims {m.group(1)} anti-patterns; AGENTS.md defines {canonical}. AGENTS.md is canonical."
            )
    if llms is not None:
        m = re.search(r"\(D-01\s*to\s*D-(\d{2})\)", llms)
        if m and int(m.group(1)) != canonical:
            problems.append(f"llms.txt prints range D-01 to D-{m.group(1)}; AGENTS.md defines D-{canonical:02d}.")
    return problems


_SURFACE_CLAIM = re.compile(r"(\d+)\s*rules\s*\+\s*(\d+)\s*skills\s*\+\s*(\d+)\s*workflows")


# Directories whose files are records of a past state, not claims about the
# current one. An ADR quotes the drift it was written to fix — ADR-031 still
# reads "15 rules + 16 skills + 12 workflows" because that is what the repo
# looked like when the coherence system was proposed, and rewriting it to
# today's numbers would falsify the record. Incident and audit write-ups are
# dated the same way. Everything else under `docs/` and at the repository
# root describes the tree as it is now, and is reconciled.
_RECORD_DIRS = ("decisions", "audit", "incidents")

# The same reasoning at the repository root. These three are append-only
# histories: a CHANGELOG entry describes the release it belongs to, and the
# v0.24.0 entry legitimately still reads "18 rules + 26 skills + 18
# workflows" because it is reporting the drift that release fixed.
_RECORD_FILES = ("CHANGELOG.md", "MIGRATION.md", "VALIDATION_LOG.md")


def _states_current_surface(path: Path, root: Path) -> bool:
    """True when `path` is a live document rather than a dated record."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if len(rel.parts) == 1:
        return rel.name not in _RECORD_FILES
    return not (rel.parts[0] == "docs" and rel.parts[1] in _RECORD_DIRS)


def _surface_claim_docs(root: Path) -> list[Path]:
    """Every live document under `root` that could restate the surface counts."""
    candidates = sorted(root.glob("*.md")) + sorted((root / "docs").rglob("*.md"))
    return [p for p in candidates if p.is_file() and _states_current_surface(p, root)]


def _reconcile_surface(doc_path: Path, rules_dir: Path, label: str) -> list[str]:
    """Compare every surface claim in one document against the tree beside it.

    The claim is reconciled wherever it appears, not only the first time:
    a document that states the counts in a summary table and again in prose
    can otherwise keep a stale copy below a corrected one.
    """
    doc = _read(doc_path)
    if doc is None or not rules_dir.is_dir():
        return []  # no agentic surface / no document here → nothing to reconcile

    skills_dir = rules_dir.parent / "skills"
    workflows_dir = rules_dir.parent / "workflows"
    actual = (
        len(list(rules_dir.glob("*.md"))),
        len(list(skills_dir.glob("*/SKILL.md"))) if skills_dir.is_dir() else 0,
        len(list(workflows_dir.glob("*.md"))) if workflows_dir.is_dir() else 0,
    )

    problems = []
    for m in _SURFACE_CLAIM.finditer(doc):
        claimed = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if claimed != actual:
            line = doc.count("\n", 0, m.start()) + 1
            problems.append(
                f"{label}:{line} claims {claimed[0]} rules + {claimed[1]} skills + "
                f"{claimed[2]} workflows; the surface beside it has "
                f"{actual[0]} rules + {actual[1]} skills + {actual[2]} workflows."
            )
    return problems


def _count_surface_units(directory: Path) -> int:
    """How many rules / skills / workflows an adapter directory holds.

    Adapters do not share a shape: `.claude/skills/` and `.devin/skills/` use
    one directory per skill, `.cursor/skills/` and `.codex/skills/` use one
    file each, and two of the four also carry an `INDEX.md` that is not a
    skill. Counting entries naively gives 28 for one adapter and 27 for the
    next, which is how the numbers in AGENTS.md drifted apart in the first
    place.
    """
    if not directory.is_dir():
        return -1
    subdirs = [d for d in directory.iterdir() if d.is_dir() and not d.name.startswith("__")]
    if subdirs:
        return len(subdirs)
    return len([f for f in directory.iterdir() if f.is_file() and f.name != "INDEX.md"])


# A line of the adapter-surface tree in AGENTS.md:
#   `.claude/skills/        # generated skill pointers + INDEX.md: 26 skills as …`
_ADAPTER_LINE = re.compile(r"^(?P<path>\.[a-z]+/[a-z]+/)\s+#[^:]*:\s*(?P<count>\d+)\s")


def _reconcile_adapter_block(doc_path: Path, root: Path, label: str) -> list[str]:
    """Every `N <unit>` claim in the adapter-surface tree must match the tree.

    AGENTS.md documents the generated surfaces with a count per directory.
    All nine were stale — claiming 18 rules / 26 skills / 18 commands against
    a live 19 / 27 / 20 — because C4 only ever reconciled CLAUDE.md. This is
    the same gap C4's own docstring describes closing for the service copy of
    CLAUDE.md, one document over.
    """
    text = _read(doc_path)
    if text is None:
        return []
    problems: list[str] = []
    for line in text.splitlines():
        m = _ADAPTER_LINE.match(line)
        if not m:
            continue
        target = root / m.group("path")
        actual = _count_surface_units(target)
        if actual < 0:
            continue  # the adapter is not present in this tree
        claimed = int(m.group("count"))
        if claimed != actual:
            problems.append(f"{label} says `{m.group('path')}` holds {claimed}; it holds {actual}.")
    return problems


def check_surface_counts() -> list[str]:
    """C4 — live agentic surface counts must match every document that states them.

    This check has been widened twice by the same failure. It started as a
    single reconciliation of the root ``CLAUDE.md``. Then
    ``templates/service/CLAUDE.md`` — which ships into every scaffolded
    service — was found sitting at "18 rules + 26 skills + 18 workflows"
    against a live 19/27/20, because inside a generated service this script
    reconciles it correctly but by then it has already shipped. Then
    ``README.md`` was found stale for the same reason: nothing looked at it.

    Naming the files one at a time is what produced both misses, so the
    check no longer does. It sweeps every live document at the repository
    root and under ``docs/`` — in this repository and in the payload — and
    reconciles each surface claim it finds. A new document that states the
    counts is covered the day it is written, without anyone remembering to
    add it here. Dated records are excluded and the reason is in
    ``_RECORD_DIRS``.
    """
    service_root = REPO_ROOT / "templates" / "service"
    service_rules = service_root / "agentic" / "rules"

    problems: list[str] = []
    for doc in _surface_claim_docs(REPO_ROOT):
        problems += _reconcile_surface(doc, RULES_DIR, str(doc.relative_to(REPO_ROOT)))
    for doc in _surface_claim_docs(service_root):
        problems += _reconcile_surface(doc, service_rules, str(doc.relative_to(REPO_ROOT)))

    # AGENTS.md documents the same surfaces as a directory tree with a count
    # per adapter, and nothing checked those: all nine were stale.
    problems += _reconcile_adapter_block(REPO_ROOT / "AGENTS.md", REPO_ROOT, "AGENTS.md")
    problems += _reconcile_adapter_block(service_root / "AGENTS.md", service_root, "templates/service/AGENTS.md")
    return problems


def check_adr_traceability() -> list[str]:
    """C5 — no silent ADR gaps; every number ≤ max has a file or a tombstone."""
    if not ADR_DIR.is_dir():
        return []  # no ADR set here → nothing to keep traceable
    by_num: dict[int, Path] = {}
    for p in ADR_DIR.glob("ADR-*.md"):
        m = re.match(r"ADR-(\d{3})", p.name)
        if m:
            by_num[int(m.group(1))] = p
    if not by_num:
        return []  # directory present but empty → nothing to check
    if 1 not in by_num:
        # No ADR-001 ⇒ this is a vendored/curated subset (e.g. a scaffolded
        # service ships only the ADRs its runtime references), not the
        # canonical register that owns contiguous numbering. Gaps are expected
        # there, so the no-silent-gap rule does not apply.
        return []

    problems: list[str] = []
    for n in range(1, max(by_num) + 1):
        if n not in by_num:
            problems.append(
                f"ADR-{n:03d} is missing with no tombstone. Numbering is immutable: "
                f"add a `Status: Withdrawn` tombstone file documenting the gap (see ADR-012)."
            )
    return problems


def check_release_note_exists() -> list[str]:
    """C6 — the current VERSION has a matching releases/vX.Y.Z.md.

    Context-adaptive like the other checks: a repo with no VERSION or no
    releases/ directory (e.g. a scaffolded service) has nothing to verify
    here.
    """
    version_raw = _read(VERSION_FILE)
    if version_raw is None or not RELEASES_DIR.is_dir():
        return []
    version = _norm_version(version_raw)
    note = RELEASES_DIR / f"v{version}.md"
    if not note.is_file():
        return [
            f"releases/v{version}.md is missing for the current VERSION ({version}). "
            f"docs/RELEASING.md requires a release note per release; "
            f"release-on-tag.yml falls back to a generic body without it."
        ]
    return []


def _doc_scan_files() -> list[Path]:
    """``docs/**/*.md`` plus root-level ``*.md`` — the repo's actual prose.

    Deliberately excludes ``agentic/`` and the generated adapter surfaces
    (``.devin/``, ``.claude/``, ``.cursor/``, ``.codex/``, the
    ``templates/service/`` vendored mirror): those are operational specs for
    agents, not reader-facing documentation, and this repo's own canon
    legitimately cites accented proper nouns there (see the word-list
    comment above). Scanning only the source of truth also avoids
    triple-reporting the same finding once per mirror.

    Uses ``git ls-files`` rather than a filesystem walk so a gitignored,
    intentionally-private local file (e.g. a personal working doc kept out
    of the repo on purpose) can never be scanned — deleted branches don't
    resurrect it, working-tree scratch files don't false-positive it. Falls
    back to a plain glob if ``git`` is unavailable (e.g. a source tarball).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "*.md"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        tracked = {REPO_ROOT / line for line in out.splitlines() if line}
    except (OSError, subprocess.CalledProcessError):
        tracked = (
            set(DOCS_DIR.rglob("*.md")) | set(REPO_ROOT.glob("*.md"))
            if DOCS_DIR.is_dir()
            else set(REPO_ROOT.glob("*.md"))
        )

    def _in_scope(p: Path) -> bool:
        try:
            rel = p.relative_to(REPO_ROOT)
        except ValueError:
            return False
        return rel.parts[0] == "docs" or len(rel.parts) == 1

    return sorted(p for p in tracked if p.is_file() and _in_scope(p))


def check_doc_language_and_privacy() -> list[str]:
    """C7 — docs/ and root docs must be English-only, and name no private repo.

    Two independent guards share one check because both are the same class
    of drift: a fact true only in an interactive/private authoring context
    (a doc drafted in Spanish; a private companion repo) leaking into the
    public tree. AUDIT R10 found both at once, in the same four documents.
    """
    problems: list[str] = []
    for path in _doc_scan_files():
        text = _read(path)
        if text is None:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()

        spanish_hits = sorted({m.group(1).lower() for m in _SPANISH_MARKERS.finditer(text)})
        if spanish_hits:
            shown = ", ".join(spanish_hits[:5])
            more = f" (+{len(spanish_hits) - 5} more)" if len(spanish_hits) > 5 else ""
            problems.append(
                f"{rel} contains Spanish word(s): {shown}{more}. This repo's documentation is English-only (AUDIT R10)."
            )

        lowered = text.lower()
        for forbidden in _FORBIDDEN_REPO_REFS:
            if forbidden in lowered:
                problems.append(
                    f"{rel} references '{forbidden}', a private/personal repo that must "
                    f"never be named in this public repo's documentation (AUDIT R10)."
                )
    return problems


def check_overlay_count() -> list[str]:
    """C8 — no living document may state an overlay count that is not the real one.

    The number six was asserted against this directory in fifteen living
    places while the tree held seven.
    ``batch-only`` was consequently absent from three CI lanes, from
    ``test_scaffold.sh``, and from every test in the repository — it shipped a
    real NetworkPolicy that nothing verified. The number was not merely stale
    documentation; it was the shape of the blind spot.

    The loops that used it now discover the directory instead, so this check
    guards the remaining surface: prose. It scans living documents only —
    frozen records (CHANGELOG, VALIDATION_LOG, releases/, docs/audit/,
    docs/decisions/, MIGRATION.md) correctly describe the count at the time
    they were written, and rewriting them would make them worse records.
    """
    overlay_dir = REPO_ROOT / "templates" / "service" / "k8s" / "overlays"
    if not overlay_dir.is_dir():
        return []
    actual = sum(1 for d in overlay_dir.iterdir() if d.is_dir())
    if actual == 0:
        return [f"no overlays found under {overlay_dir} — the count cannot be checked"]

    words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
    }
    # `overlay` as well as `overlays`. README described the smoke lane in the
    # singular ("… overlay renders …"), which the plural-only pattern walked
    # straight past — the check added to stop this count drifting missed an
    # instance of it on the day it shipped.
    claim = re.compile(
        r"\b(\d+|" + "|".join(words.values()) + r")\s+(?:kustomize\s+|environment\s+)?overlays?\b",
        re.IGNORECASE,
    )
    frozen = (
        "CHANGELOG.md",
        "VALIDATION_LOG.md",
        "MIGRATION.md",
        "releases/",
        "docs/audit/",
        "docs/decisions/",
    )
    # Tracked files only. An untracked or ignored file — private scratch
    # notes, a virtualenv — is not a claim this repository makes.
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "*.md", "*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return ["could not list tracked files (`git ls-files`) to check overlay counts"]

    problems: list[str] = []
    for rel in sorted(tracked):
        if rel.startswith(frozen):
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            match = claim.search(line)
            if not match:
                continue
            token = match.group(1).lower()
            stated = int(token) if token.isdigit() else next(n for n, w in words.items() if w == token)
            if stated != actual:
                problems.append(
                    f"{rel}:{lineno}: says {match.group(0)!r}, but "
                    f"templates/service/k8s/overlays/ holds {actual}. "
                    f"A wrong count here is how `batch-only` stayed invisible to CI."
                )
    return problems


CHECKS = [
    ("C1 version-sot", check_version_sot),
    ("C2 llms-version", check_llms_version),
    ("C3 anti-pattern-count", check_anti_pattern_count),
    ("C4 surface-counts", check_surface_counts),
    ("C5 adr-traceability", check_adr_traceability),
    ("C6 release-note-exists", check_release_note_exists),
    ("C7 doc-language-privacy", check_doc_language_and_privacy),
    ("C8 overlay-count", check_overlay_count),
]


def main() -> int:
    all_problems: list[tuple[str, str]] = []
    for label, fn in CHECKS:
        for problem in fn():
            all_problems.append((label, problem))

    if not all_problems:
        # Derived, not written: this line said "all 7" while CHECKS held 8,
        # in the script whose job is catching exactly that.
        print(f"[doc-coherence] OK — all {len(CHECKS)} cross-document checks pass.")
        return 0

    print(f"[doc-coherence] {len(all_problems)} coherence violation(s):")
    for label, problem in all_problems:
        print(f"  - [{label}] {problem}")
    print("[doc-coherence] Fix with the `doc-coherence` skill or by hand, then re-run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
