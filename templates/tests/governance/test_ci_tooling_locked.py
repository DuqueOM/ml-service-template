"""Contract — the template's CI installs its own tooling from uv.lock, not from PyPI's latest.

Why this exists
---------------
A dozen jobs ran ``pip install "copier>=9.0.0" jsonschema`` or ``pip install
pyyaml``: no upper bound, no hashes, a different answer on every run. That is
the failure ``ci-examples.yml`` documents — ``pyarrow`` resolved to a release
that crossed the numpy boundary and every open PR went red at once with nothing
wrong in any of them. ADR-053 locked that tooling in ``uv.lock`` and left the
workflows alone; ADR-054 moves them onto it. This file holds the move in place,
so the next job someone copies from an old one does not bring the float back.

Three halves
------------
* **No floating tooling.** Outside the lanes whose purpose is to prove an
  adopter's own ``pip`` route (``ALLOWED_PIP_LANES``, each with its reason), no
  workflow ``pip install``s a package that ``uv.lock`` already owns.
* **The installer is ordered and pinned.** Every job that installs the locked
  tooling runs ``astral-sh/setup-uv`` (SHA-pinned, one uv version everywhere)
  and ``actions/setup-python`` first.
* **The installer cannot succeed by installing nothing** (hashes required,
  lock never re-resolved, export written before it is read).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
INSTALLER = REPO_ROOT / "scripts" / "install_ci_tooling.sh"

# Packages the root `dev` group (uv.lock) owns, plus the transitive ones the CI
# installed by name. A `pip install` of any of them is the float this file forbids.
LOCKED_TOOLING = ("copier", "jsonschema", "pyyaml", "pre-commit", "jinja2", "pytest")

# Workflows that MAY pip-install that tooling, and why. Each is a decision, not an
# oversight — adding a line here is the review ADR-054 asks for.
ALLOWED_PIP_LANES = {
    "pr-smoke-lane.yml": (
        "the per-PR scaffold smoke is the lane that proves the README quick start: it installs "
        'exactly `pip install "copier>=9.0.0"`, the adopter\'s own route, and the README standard '
        "admits that range only because a workflow installs it (check_readme.py)"
    ),
    "template-context-tests.yml": (
        "copier is co-installed with the generated service's dev stack in one environment; locking "
        "it there would collide with the service's `~=` pins (ADR-048). Moving it is a separate "
        "decision, tracked in ADR-054"
    ),
}

UV_ACTION = re.compile(r"^astral-sh/setup-uv@([0-9a-f]{40})$")
PIP_INSTALL = re.compile(r"\bpip3?\s+install\b")


def _workflows() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        yield path.name, yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps() -> Iterator[tuple[str, str, int, dict[str, Any]]]:
    for name, workflow in _workflows():
        for job_id, job in (workflow.get("jobs") or {}).items():
            for position, step in enumerate(job.get("steps") or []):
                yield name, job_id, position, step


def test_no_workflow_pip_installs_tooling_that_uv_lock_owns_outside_the_documented_lanes() -> None:
    offenders: list[str] = []
    for name, job_id, _position, step in _steps():
        if name in ALLOWED_PIP_LANES:
            continue
        for line in str(step.get("run", "")).splitlines():
            if not PIP_INSTALL.search(line) or line.lstrip().startswith("#"):
                continue
            for package in LOCKED_TOOLING:
                if re.search(rf"(?<![\w./-]){re.escape(package)}(?![\w-])", line):
                    offenders.append(f"{name}::{job_id}: {line.strip()}")
    assert not offenders, (
        "These steps install tooling that uv.lock already pins, from PyPI's latest. Run "
        "`bash scripts/install_ci_tooling.sh` after setup-uv + setup-python (ADR-054), or — only if the "
        "step proves an adopter's own pip route — add the workflow to ALLOWED_PIP_LANES with the reason:\n  "
        + "\n  ".join(offenders)
    )


def test_every_allowed_pip_lane_still_exists_and_still_pip_installs_tooling() -> None:
    """An allowlist entry that no longer applies is a hole waiting for the next copy-paste."""
    # Only the commands count: a comment explaining the exception (this repo writes
    # many) must not keep the exception alive after the command is gone.
    commands: dict[str, list[str]] = {}
    for name, _job, _pos, step in _steps():
        for line in str(step.get("run", "")).splitlines():
            if not line.lstrip().startswith("#"):
                commands.setdefault(name, []).append(line)
    for name in ALLOWED_PIP_LANES:
        assert name in commands, f"{name} is allowlisted but no longer exists"
        installs_tooling = any(
            PIP_INSTALL.search(line)
            and any(re.search(rf"(?<![\w./-]){re.escape(p)}(?![\w-])", line) for p in LOCKED_TOOLING)
            for line in commands[name]
        )
        assert installs_tooling, f"{name} no longer pip-installs locked tooling; drop it from ALLOWED_PIP_LANES"


def _jobs_running_the_installer() -> list[tuple[str, str, list[dict[str, Any]], int]]:
    found = []
    for name, workflow in _workflows():
        for job_id, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or []
            for position, step in enumerate(steps):
                if "scripts/install_ci_tooling.sh" in str(step.get("run", "")):
                    found.append((name, job_id, steps, position))
    return found


def test_the_installer_is_used_somewhere() -> None:
    assert _jobs_running_the_installer(), "no workflow runs scripts/install_ci_tooling.sh any more"


@pytest.mark.parametrize(
    ("name", "job_id", "steps", "position"),
    _jobs_running_the_installer(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_the_installer_runs_after_setup_uv_and_setup_python(
    name: str, job_id: str, steps: list[dict[str, Any]], position: int
) -> None:
    before = steps[:position]
    uv = [i for i, s in enumerate(before) if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    py = [i for i, s in enumerate(before) if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert uv, f"{name}::{job_id}: scripts/install_ci_tooling.sh runs before (or without) astral-sh/setup-uv"
    assert py, f"{name}::{job_id}: scripts/install_ci_tooling.sh runs before (or without) actions/setup-python"


def test_setup_uv_is_pinned_by_sha_and_to_one_uv_version_everywhere() -> None:
    versions: dict[str, set[str]] = {}
    uses = 0
    for name, job_id, _pos, step in _steps():
        action = str(step.get("uses", ""))
        if not action.startswith("astral-sh/setup-uv@"):
            continue
        uses += 1
        assert UV_ACTION.match(action), (
            f"{name}::{job_id}: setup-uv must be pinned by a 40-hex commit SHA, got {action}"
        )
        version = str((step.get("with") or {}).get("version", ""))
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
            f"{name}::{job_id}: setup-uv needs an exact `version:` "
            f"(a floating uv is the float this removes), got {version!r}"
        )
        versions.setdefault(version, set()).add(f"{name}::{job_id}")
    assert uses, "no workflow uses astral-sh/setup-uv"
    assert len(versions) == 1, f"jobs disagree on the uv version: { {v: sorted(j) for v, j in versions.items()} }"


def test_every_job_that_calls_uv_installs_it_first() -> None:
    for name, workflow in _workflows():
        for job_id, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or []
            for position, step in enumerate(steps):
                if not re.search(r"(^|\s)uv\s+(run|sync|export|pip|lock)\b", str(step.get("run", ""))):
                    continue
                assert any(str(s.get("uses", "")).startswith("astral-sh/setup-uv@") for s in steps[:position]), (
                    f"{name}::{job_id}: step {position} calls uv but no earlier step installs it"
                )


def test_the_installer_cannot_succeed_by_installing_nothing() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "set -euo pipefail" in code
    assert "--frozen" in code, "without --frozen a stale lock would be re-resolved instead of failing"
    assert "--only-group dev" in code
    assert "--require-hashes" in code, "without hashes the lock pins versions but not the bytes"
    assert "--strict" in code
    assert "--output-file" in code and "--hash=sha256:" in code, (
        "the export must be written to a file and checked for hashes before it is installed"
    )
    assert not re.search(r"uv export[^\n]*\|", code), "piping uv export into the installer hides a failed export"
