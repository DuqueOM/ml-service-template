#!/usr/bin/env python3
"""Verify every variable a shipped deploy workflow reads is documented, in the right channel.

Why this exists
---------------
A deploy workflow that reads a value nobody was told to set fails on the first
real deployment, with a message about the consumer rather than the cause. And
GitHub does **not** fall back from ``secrets.X`` to ``vars.X``: a value put in
the wrong place arrives as an empty string, so the wrong *channel* fails exactly
as silently as a missing entry.

Both happened, and neither was detectable because nothing compared what the
workflows read against what the runbooks tell an adopter to configure.

* ``AWS_BUILD_ROLE_ARN`` is required by ``deploy-aws.yml``'s build job to push
  to ECR. It appeared in **no** runbook. An adopter following
  ``aws-irsa-setup.md`` to completion would have set up the deploy role, started
  their first deploy, and had the build job fail on a secret they had never
  heard of.
* ``AWS_ROLE_ARN`` is read from ``secrets``, and ``aws-irsa-setup.md`` §A.4 said
  *"Add repository variables (NOT secrets — these are not sensitive)"* and
  listed it among them. The reasoning is sound and it did not match the code.
  Following the runbook exactly produced an empty ``role-to-assume``.

The GCP side was consistent — its runbook says variables and its workflow reads
``vars`` — which is what makes the AWS mismatch a drift rather than a design.

What this checks
----------------
For every ``vars.NAME`` and ``secrets.NAME`` referenced by a deploy workflow in
the Copier payload:

1. the name is documented somewhere an adopter reads (``docs/runbooks/``,
   ``docs/*.md``, or the payload's own docs), and
2. the documentation puts it in the **same channel** the workflow reads it
   from — a name that appears under a "Secrets" heading but is read from
   ``vars`` is as broken as one that is missing.

Check 2 is **table-scoped and exact**, not a proximity heuristic. A markdown
table whose first header cell is ``Secret`` or ``Variable`` declares the channel
for every name in its first column; nothing else counts. Prose mentions are
ignored entirely.

That precision was not the first attempt. Looking for the nearest preceding
heading that said "secret" or "variable" produced **eleven false positives** on
a tree with exactly one real mismatch — any document that used the word
"secret" in a sentence before listing a variable was flagged. A gate that
reports eleven wrong answers and one right one sends its reader to edit
documentation that was already correct, and the pressure lands on the useful
half. The table header is the one place the channel is stated as data rather
than as prose.

Exit codes
----------
- 0: every referenced name is documented in the channel it is read from.
- 1: a name is undocumented, or documented under the other channel.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / "templates" / "service" / ".github" / "workflows"
DOC_GLOBS = (
    "docs/runbooks/*.md",
    "docs/runbooks/**/*.md",
    "docs/*.md",
    "templates/service/docs/*.md",
    "templates/service/docs/**/*.md",
)

# Only the lanes that deploy. A CI workflow reading a token is not an adopter
# setup step, and folding those in would make the gate loud enough to ignore.
DEPLOY_WORKFLOWS = ("deploy-*.yml",)

_REF = re.compile(r"\b(?P<channel>vars|secrets)\.(?P<name>[A-Z][A-Z0-9_]{2,})\b")

# Names GitHub provides, or that the workflow_call contract forwards rather
# than an adopter configuring. Each needs a reason, not just an entry.
_PROVIDED: dict[str, str] = {
    "GITHUB_TOKEN": "provided by GitHub Actions on every run",
}

# A markdown table row, and the header cells that declare a channel.
_ROW = re.compile(r"^\s*\|(?P<cells>.+)\|\s*$")
_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_CHANNEL_HEADER = {"secret": "secrets", "secrets": "secrets", "variable": "vars", "variables": "vars"}


def _referenced() -> dict[str, set[str]]:
    """name -> {channels it is read from} across the deploy workflows."""
    found: dict[str, set[str]] = {}
    for pattern in DEPLOY_WORKFLOWS:
        for path in sorted(WORKFLOWS.glob(pattern)):
            for match in _REF.finditer(path.read_text(encoding="utf-8")):
                name = match.group("name")
                if name in _PROVIDED:
                    continue
                found.setdefault(name, set()).add(match.group("channel"))
    return found


def _docs() -> list[tuple[Path, str]]:
    seen: dict[Path, str] = {}
    for pattern in DOC_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if path.is_file():
                seen.setdefault(path, path.read_text(encoding="utf-8", errors="ignore"))
    return sorted(seen.items())


def _declared_channels(text: str) -> dict[str, set[str]]:
    """name -> channels, from tables whose first header cell names a channel.

    Only the first column of such a table counts. A name mentioned in prose, in
    a "Read by" column, or in a table with any other header is not a
    declaration of where to put it.
    """
    declared: dict[str, set[str]] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        header = _ROW.match(lines[index])
        if not header or index + 1 >= len(lines) or not _SEPARATOR.match(lines[index + 1]):
            index += 1
            continue
        first_cell = header.group("cells").split("|")[0].strip().strip("*`").lower()
        channel = _CHANNEL_HEADER.get(first_cell)
        index += 2
        while index < len(lines):
            row = _ROW.match(lines[index])
            if not row:
                break
            if channel:
                cell = row.group("cells").split("|")[0]
                for name in re.findall(r"[A-Z][A-Z0-9_]{2,}", cell):
                    declared.setdefault(name, set()).add(channel)
            index += 1
    return declared


def main() -> int:
    referenced = _referenced()
    if not referenced:
        print(f"FAIL: no vars/secrets references found under {WORKFLOWS.relative_to(REPO_ROOT)}.")
        print("  Either the deploy workflows moved or the pattern stopped matching —")
        print("  and a gate that checks nothing reports success, which is the defect")
        print("  this one exists to prevent.")
        return 1

    docs = _docs()
    if not docs:
        print("FAIL: no adopter documentation found to check against.")
        return 1

    undocumented: list[str] = []
    mismatched: list[tuple[str, str, str, str]] = []

    for name, channels in sorted(referenced.items()):
        mentions = [(path, text) for path, text in docs if re.search(rf"\b{re.escape(name)}\b", text)]
        if not mentions:
            undocumented.append(name)
            continue
        if len(channels) != 1:
            continue  # read from both channels somewhere; not this gate's call
        (read_from,) = channels
        for path, text in mentions:
            documented = _declared_channels(text).get(name)
            if documented and read_from not in documented:
                mismatched.append((name, read_from, sorted(documented)[0], path.relative_to(REPO_ROOT).as_posix()))
                break

    if undocumented or mismatched:
        print("FAIL: the deploy contract does not match what an adopter is told to configure.")
        print()
        for name in undocumented:
            channels = "/".join(sorted(referenced[name]))
            print(f"  - {name} is read from `{channels}` and documented nowhere.")
            print("      An adopter completes the runbook, starts their first deploy, and it")
            print("      fails on a value they have never heard of.")
        for name, read_from, documented, where in mismatched:
            print(f"  - {name} is read from `{read_from}.{name}` but documented under {documented}")
            print(f"      in {where}.")
            print("      GitHub does NOT fall back between the two: the value arrives empty.")
        print()
        print("  Fix the documentation, or change the workflow — but they have to agree.")
        return 1

    total_refs = sum(len(c) for c in referenced.values())
    print(
        f"[deploy-contract] OK — {len(referenced)} name(s) across {total_refs} reference(s) "
        f"in the deploy workflows; each documented in the channel it is read from."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
