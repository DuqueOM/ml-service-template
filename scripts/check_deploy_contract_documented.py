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
For every ``vars.NAME`` and ``secrets.NAME`` referenced by ANY workflow in the
Copier payload:

1. the name is documented somewhere an adopter reads (``docs/runbooks/``,
   ``docs/*.md``, or the payload's own docs), and
2. the documentation puts it in the **same channel** the workflow reads it
   from — a name that appears under a "Secrets" heading but is read from
   ``vars`` is as broken as one that is missing, and
3. a name documented as **environment**-scoped is only read by jobs that
   declare an ``environment:`` — or that call a reusable workflow whose jobs
   do, which is how an environment secret legitimately reaches
   ``deploy-common.yml``.

Check 3 is the same silent failure one level up. GitHub exposes an environment
secret or variable only to a job that declares that environment; everywhere
else it is an empty string. Three were live when this check was added: the
nightly Terraform plan, drift detection and retrain all read ``AWS_ROLE_ARN`` —
documented, correctly, as per-environment — from jobs with no environment, and
``environment-promotion.md`` placed ``GCP_PROJECT_ID`` and the cluster names at
environment scope while the jobs reading them declare none. The scan used to
cover only ``deploy-*.yml``, which is why none of it was visible.

Checks 2 and 3 are **table-scoped and exact**, not proximity heuristics. A
markdown table whose first header cell is ``Secret`` or ``Variable`` declares
the channel for every name in its first column; a ``Scope`` column in that
table declares ``environment`` or ``repository``. Nothing else counts. Prose
mentions are ignored entirely.

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
- 1: a name is undocumented, documented under the other channel, or documented
  as environment-scoped and read by a job outside any environment.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
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

# Every workflow a generated service ships. Scoping this to deploy-*.yml is how
# three scheduled workflows read an environment-scoped role from outside any
# environment without this gate seeing them.
WORKFLOW_GLOBS = ("*.yml", "*.yaml")

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

_JOB = re.compile(r"^  (?P<job>[A-Za-z0-9_-]+):\s*$")
_JOB_ENVIRONMENT = re.compile(r"^    environment:", re.M)
_JOB_USES = re.compile(r"^    uses:\s*\./\.github/workflows/(?P<file>[^\s@]+)", re.M)


@dataclass(frozen=True)
class Read:
    workflow: str
    job: str
    channel: str
    name: str
    in_environment: bool


def _workflows() -> list[Path]:
    return sorted({p for pattern in WORKFLOW_GLOBS for p in WORKFLOWS.glob(pattern)})


WORKFLOW_LEVEL = "(workflow-level env)"


def _workflow_level(text: str) -> str:
    """Everything before ``jobs:``, comments removed — where a top-level ``env:`` lives.

    A top-level ``env:`` is evaluated outside any job, so it can never see an
    environment-scoped value. ``deploy-gcp.yml`` builds its registry URL from
    ``vars.GCP_PROJECT_ID`` exactly there.
    """
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines) if line.rstrip() == "jobs:"), len(lines))
    return "\n".join(line for line in lines[:end] if not line.lstrip().startswith("#"))


def _jobs(text: str) -> dict[str, str]:
    """job id -> its body, comment lines removed. Text-based: payload YAML carries Jinja tokens."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:
        return {}
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start + 1 :]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        match = _JOB.match(line)
        if match:
            current = match.group("job")
            jobs[current] = []
        elif current is not None and not line.lstrip().startswith("#"):
            jobs[current].append(line)
    return {job: "\n".join(body) for job, body in jobs.items()}


def _environment_workflows() -> set[str]:
    """Workflow files whose every job declares an environment."""
    result = set()
    for path in _workflows():
        jobs = _jobs(path.read_text(encoding="utf-8"))
        if jobs and all(_JOB_ENVIRONMENT.search(body) for body in jobs.values()):
            result.add(path.name)
    return result


def _referenced() -> dict[str, set[str]]:
    """name -> {channels it is read from} across every workflow, comments included."""
    found: dict[str, set[str]] = {}
    for path in _workflows():
        for match in _REF.finditer(path.read_text(encoding="utf-8")):
            name = match.group("name")
            if name in _PROVIDED:
                continue
            found.setdefault(name, set()).add(match.group("channel"))
    return found


def _job_reads() -> list[Read]:
    """Every vars/secrets read inside a job, with whether that job runs in an environment."""
    environment_workflows = _environment_workflows()
    reads: list[Read] = []
    for path in _workflows():
        text = path.read_text(encoding="utf-8")
        for match in _REF.finditer(_workflow_level(text)):
            if match.group("name") not in _PROVIDED:
                reads.append(Read(path.name, WORKFLOW_LEVEL, match.group("channel"), match.group("name"), False))
        for job, body in _jobs(text).items():
            uses = _JOB_USES.search(body)
            in_environment = bool(_JOB_ENVIRONMENT.search(body)) or bool(
                uses and Path(uses.group("file")).name in environment_workflows
            )
            for match in _REF.finditer(body):
                if match.group("name") not in _PROVIDED:
                    reads.append(Read(path.name, job, match.group("channel"), match.group("name"), in_environment))
    return reads


def _docs() -> list[tuple[Path, str]]:
    seen: dict[Path, str] = {}
    for pattern in DOC_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if path.is_file():
                seen.setdefault(path, path.read_text(encoding="utf-8", errors="ignore"))
    return sorted(seen.items())


def _scope(cell: str) -> str | None:
    text = cell.lower()
    if "environment" in text and "repository" not in text:
        return "environment"
    if "repository" in text:
        return "repository"
    return None


def _declarations(text: str) -> dict[str, set[tuple[str, str | None]]]:
    """name -> {(channel, scope)}, from tables whose first header cell names a channel.

    Only the first column of such a table counts, and scope only from a column
    headed ``Scope``. A name mentioned in prose, in a "Read by" column, or in a
    table with any other header is not a declaration of where to put it.
    """
    declared: dict[str, set[tuple[str, str | None]]] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        header = _ROW.match(lines[index])
        if not header or index + 1 >= len(lines) or not _SEPARATOR.match(lines[index + 1]):
            index += 1
            continue
        header_cells = [c.strip().strip("*`").lower() for c in header.group("cells").split("|")]
        channel = _CHANNEL_HEADER.get(header_cells[0])
        scope_column = header_cells.index("scope") if "scope" in header_cells else None
        index += 2
        while index < len(lines):
            row = _ROW.match(lines[index])
            if not row:
                break
            if channel:
                cells = row.group("cells").split("|")
                scope = _scope(cells[scope_column]) if scope_column is not None and scope_column < len(cells) else None
                for name in re.findall(r"[A-Z][A-Z0-9_]{2,}", cells[0]):
                    declared.setdefault(name, set()).add((channel, scope))
            index += 1
    return declared


def main() -> int:
    referenced = _referenced()
    if not referenced:
        print(f"FAIL: no vars/secrets references found under {WORKFLOWS.relative_to(REPO_ROOT)}.")
        print("  Either the workflows moved or the pattern stopped matching —")
        print("  and a gate that checks nothing reports success, which is the defect")
        print("  this one exists to prevent.")
        return 1

    docs = _docs()
    if not docs:
        print("FAIL: no adopter documentation found to check against.")
        return 1

    undocumented: list[str] = []
    mismatched: list[tuple[str, str, str, str]] = []
    scopes: dict[str, set[str]] = {}

    for name, channels in sorted(referenced.items()):
        mentions = [(path, text) for path, text in docs if re.search(rf"\b{re.escape(name)}\b", text)]
        if not mentions:
            undocumented.append(name)
            continue
        for path, text in mentions:
            for _, scope in _declarations(text).get(name, set()):
                if scope:
                    scopes.setdefault(name, set()).add(scope)
        if len(channels) != 1:
            continue  # read from both channels somewhere; not this gate's call
        (read_from,) = channels
        for path, text in mentions:
            documented = {channel for channel, _ in _declarations(text).get(name, set())}
            if documented and read_from not in documented:
                mismatched.append((name, read_from, sorted(documented)[0], path.relative_to(REPO_ROOT).as_posix()))
                break

    contradictory = sorted(name for name, found in scopes.items() if len(found) > 1)
    out_of_environment = sorted(
        {
            (read.name, read.workflow, read.job)
            for read in _job_reads()
            if scopes.get(read.name) == {"environment"} and not read.in_environment
        }
    )

    if undocumented or mismatched or contradictory or out_of_environment:
        print("FAIL: the workflow contract does not match what an adopter is told to configure.")
        print()
        for name in undocumented:
            read_channels = "/".join(sorted(referenced[name]))
            print(f"  - {name} is read from `{read_channels}` and documented nowhere.")
            print("      An adopter completes the runbook, starts their first deploy, and it")
            print("      fails on a value they have never heard of.")
        for name, read_from, doc_channel, where in mismatched:
            print(f"  - {name} is read from `{read_from}.{name}` but documented under {doc_channel}")
            print(f"      in {where}.")
            print("      GitHub does NOT fall back between the two: the value arrives empty.")
        for name in contradictory:
            print(f"  - {name} is documented as both repository- and environment-scoped.")
        for name, workflow, job in out_of_environment:
            where = f"`{workflow}` {job}" if job == WORKFLOW_LEVEL else f"`{workflow}` job `{job}`"
            print(f"  - {name} is documented as environment-scoped, but {where}")
            print("      reads it without declaring `environment:`. GitHub exposes environment")
            print("      values only to jobs in that environment: here it arrives empty.")
        print()
        print("  Fix the documentation, or change the workflow — but they have to agree.")
        return 1

    total_refs = sum(len(c) for c in referenced.values())
    print(
        f"[deploy-contract] OK — {len(referenced)} name(s) across {total_refs} reference(s) "
        f"in {len(_workflows())} workflow(s); each documented in the channel it is read from, "
        "and no environment-scoped value is read outside an environment."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
