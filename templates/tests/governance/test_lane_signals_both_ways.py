"""Contract — every unattended verification lane can raise its alarm AND retract it.

Why this exists
---------------
``golden-path.yml`` runs weekly and on demand, never on a pull request, so its
failures appear in no PR's checks. It was red for eighteen consecutive weekly
runs while ``README.md`` offered L1+L2+L3 to adopters as their contract, and
nobody was told. The fix was ``notify-on-failure``: open one issue, comment on
it rather than duplicating.

That fix was half a control. It opened issue #177 on 2026-09-12 and nothing
closed it. The lane went green on the next two runs, and the issue kept telling
every reader that L3 was failing — three days later it was the only open signal
in the repository, and it was wrong. An alarm that cannot be retracted is
indistinguishable from a stale one, so a reader learns to ignore both.

The closed-loop lane had the opposite half missing: no notifier at all. It had
never once been green before v0.28.0, and the only reason anyone found out was
a manual read of the Actions tab.

What this checks
----------------
For every golden-path workflow: a job that opens or comments on an issue when
the lane fails, a job that closes it when the lane passes, both keyed on the
**same** label, both holding ``issues: write``, and each guarded by the
``needs.*.result`` condition for its direction. The label set is read from the
workflows, so a new lane with a new label is covered without editing this file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
LANES = sorted(WORKFLOWS.glob("golden-path*.yml"))


def _jobs(path: Path) -> dict[str, dict]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document["jobs"]


def _script(job: dict) -> str:
    return "\n".join(
        str(step.get("with", {}).get("script", "")) for step in job.get("steps", []) if isinstance(step, dict)
    )


def _labels(script: str) -> set[str]:
    return set(re.findall(r"const label = '([^']+)'", script))


def test_there_are_lanes_to_check() -> None:
    """A contract over an empty set passes, which is the failure this repo keeps finding."""
    assert LANES, f"no golden-path*.yml under {WORKFLOWS}"


@pytest.mark.parametrize("lane", LANES, ids=[p.name for p in LANES])
def test_the_lane_raises_and_retracts_its_own_alarm(lane: Path) -> None:
    jobs = _jobs(lane)
    opens = {name: job for name, job in jobs.items() if "issues.create" in _script(job)}
    closes = {name: job for name, job in jobs.items() if "issues.update" in _script(job)}

    assert opens, (
        f"{lane.name} runs unattended (workflow_dispatch + schedule, never on a PR). "
        "A failure here reaches nobody unless the lane opens an issue."
    )
    assert closes, (
        f"{lane.name} can open an issue but never closes one. The red-only version of this "
        "control left issue #177 asserting a failure that two green runs had already "
        "contradicted; a signal that cannot be retracted is a stale signal."
    )

    for direction, found, expected in (("open", opens, "failure"), ("close", closes, "success")):
        for name, job in found.items():
            permissions = job.get("permissions") or {}
            assert permissions.get("issues") == "write", f"{lane.name}: job {name} lacks `issues: write`"
            condition = str(job.get("if", ""))
            assert "needs.*.result" in condition, (
                f"{lane.name}: job {name} does not gate on needs.*.result, so it would run in both directions"
            )
            if direction == "open":
                assert "contains(needs.*.result, 'failure')" in condition
            else:
                assert "!contains(needs.*.result, 'failure')" in condition, (
                    f"{lane.name}: job {name} would close the issue even when the lane failed"
                )
            assert expected  # documents the direction under test

    open_labels = {label for job in opens.values() for label in _labels(_script(job))}
    close_labels = {label for job in closes.values() for label in _labels(_script(job))}
    assert open_labels and open_labels == close_labels, (
        f"{lane.name}: opens issues labelled {sorted(open_labels)} but closes {sorted(close_labels)}. "
        "Two different labels means the alarm is never retracted."
    )


def test_the_lanes_do_not_share_one_label() -> None:
    """Each lane fails for its own reasons; one mixed thread tells a reader neither."""
    per_lane = {}
    for lane in LANES:
        labels = {label for job in _jobs(lane).values() for label in _labels(_script(job))}
        if labels:
            per_lane[lane.name] = labels
    seen: dict[str, str] = {}
    for name, labels in per_lane.items():
        for label in labels:
            assert label not in seen, f"{name} and {seen[label]} both use the label {label!r}"
            seen[label] = name
