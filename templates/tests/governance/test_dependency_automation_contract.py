"""Contract — the automation may not merge what a human is supposed to decide.

Why this exists
---------------
ADR-052 moves dependency review from per-PR to per-boundary: patch, minor and
digest updates merge when the required checks pass, and a MAJOR waits for a
person. That policy lives in two YAML files, and YAML has no tests. The
failure mode is silent in the worst way: widen one `if:` and the next numpy
major merges itself on green, against a boundary D-05 draws for a measured
reason (numpy 2.x silently corrupts joblib-serialised models).

Three more things this pins, each of which was a real defect first:

* **Both trees, one PR.** `check_cicd_template_drift.py` requires the runtime
  workflows and their payload copies to pin identical action versions. The
  Dependabot entry used a singular `directory:` per tree, so every action bump
  arrived as half a change and was red on arrival — #186, #187, #197, #199.
* **Pinned by commit.** An auto-merge workflow running a mutable tag is a
  workflow that can change what "merge on green" means without a diff.
* **Paths that exist.** `directory:` is resolved literally and Dependabot does
  not warn about a directory that is not there; the entry simply watches
  nothing, which is the shape of every blind spot this repository has found.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PAYLOAD = REPO_ROOT / "templates" / "service"
WORKFLOWS = (
    REPO_ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml",
    PAYLOAD / ".github" / "workflows" / "dependabot-auto-merge.yml",
)
CONFIGS = (REPO_ROOT / ".github" / "dependabot.yml", PAYLOAD / ".github" / "dependabot.yml")

MAJOR = "version-update:semver-major"


def _load(path: Path) -> dict:
    assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


@pytest.mark.parametrize("path", WORKFLOWS, ids=[p.parent.parent.parent.name for p in WORKFLOWS])
def test_the_merge_step_excludes_majors(path: Path) -> None:
    steps = _steps(_load(path))
    merging = [s for s in steps if "gh pr merge" in str(s.get("run", ""))]
    assert merging, f"{path.name} queues no merge; the workflow does nothing"
    for step in merging:
        condition = str(step.get("if", ""))
        assert f"!= '{MAJOR}'" in condition or f'!= "{MAJOR}"' in condition, (
            f"{path.name}: the merge step runs under `if: {condition or 'always'}`, which does not "
            f"exclude {MAJOR}. A major would merge itself on green, and a major is the one case "
            "ADR-052 reserves for a person."
        )
        assert "--auto" in str(step["run"]), (
            f"{path.name}: merging without `--auto` bypasses the required checks entirely"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=[p.parent.parent.parent.name for p in WORKFLOWS])
def test_it_only_acts_on_dependabot_pull_requests(path: Path) -> None:
    workflow = _load(path)
    for name, job in workflow["jobs"].items():
        condition = str(job.get("if", ""))
        assert "pull_request.user.login == 'dependabot[bot]'" in condition, (
            f"{path.name}: job {name} is not restricted to Dependabot's own PRs, so a human branch "
            "named dependabot/* would inherit auto-merge"
        )
        permissions = job.get("permissions") or workflow.get("permissions") or {}
        assert permissions.get("contents") == "write" and permissions.get("pull-requests") == "write", (
            f"{path.name}: job {name} cannot queue a merge without contents+pull-requests write"
        )


def test_both_trees_pin_the_same_metadata_action() -> None:
    pins = {}
    for path in WORKFLOWS:
        uses = re.findall(r"uses:\s*(dependabot/fetch-metadata@\S+)", path.read_text(encoding="utf-8"))
        assert len(uses) == 1, f"{path.name} reads Dependabot metadata {len(uses)} times"
        pins[path] = uses[0]
    assert len(set(pins.values())) == 1, f"the two trees pin different versions: {set(pins.values())}"
    (pin,) = set(pins.values())
    assert re.fullmatch(r"dependabot/fetch-metadata@[0-9a-f]{40}", pin), (
        f"{pin} is not pinned to a commit; a mutable tag can change what 'merge on green' means without a diff"
    )


def test_an_action_bump_can_satisfy_the_drift_gate_in_one_pr() -> None:
    """The drift gate compares both trees, so one PR has to be able to touch both."""
    entries = [u for u in _load(CONFIGS[0])["updates"] if u["package-ecosystem"] == "github-actions"]
    assert len(entries) == 1, (
        f"{len(entries)} github-actions entries: one per tree means one PR per tree, and each is "
        "half a change that check_cicd_template_drift.py rejects (#186, #187, #197, #199)"
    )
    directories = entries[0].get("directories") or [entries[0].get("directory")]
    assert "/" in directories and "/templates/service" in directories, (
        f"the github-actions entry covers {directories}; it must cover the runtime workflows and "
        "their payload copies together"
    )


@pytest.mark.parametrize("config", CONFIGS, ids=[p.parent.parent.name for p in CONFIGS])
def test_every_watched_directory_exists(config: Path) -> None:
    root = REPO_ROOT if config == CONFIGS[0] else PAYLOAD
    missing = []
    for update in _load(config)["updates"]:
        for directory in update.get("directories") or [update["directory"]]:
            if not (root / directory.lstrip("/")).is_dir():
                missing.append(f"{update['package-ecosystem']} -> {directory}")
    assert not missing, (
        f"{config.relative_to(REPO_ROOT)} watches directories that do not exist, so those "
        f"ecosystems are watched by nothing: {missing}"
    )


@pytest.mark.parametrize("config", CONFIGS, ids=[p.parent.parent.name for p in CONFIGS])
def test_the_correctness_boundaries_travel_with_the_payload(config: Path) -> None:
    """numpy 1.x is a measured boundary (D-05); a generated service inherits it or it is lost."""
    ignored = {entry["dependency-name"] for update in _load(config)["updates"] for entry in update.get("ignore", [])}
    assert {"numpy", "shap"} <= ignored, (
        f"{config.relative_to(REPO_ROOT)} does not carry the numpy/shap boundary: {sorted(ignored)}. "
        "Without it the generated service's first dependency PR proposes crossing D-05."
    )
