"""Contract — the dependency scan resolves on the Python the service image runs.

Why this exists
---------------
``scripts/resolve_python_dependencies.py`` asks pip what it would install, and
the answer depends on the interpreter: environment markers in pandas 3 and
scikit-learn 1.9 require numpy 2 from Python 3.13 on. The Self-audit job ran
it on the runner's system ``python3``. When GitHub moved ``ubuntu-latest`` to
ubuntu-26.04 that interpreter changed, and the scan failed on every run —
resolving, and scanning, a set no generated image would ever install.

So the job reads the Python from ``templates/service/Dockerfile`` and pins
``actions/setup-python`` to it before resolving, as ``eda-heavy-smoke.yml``
already did. This holds both halves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate-templates.yml"


def _self_audit_steps() -> list[dict[str, Any]]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    job = next(job for job in jobs.values() if str(job.get("name", "")).startswith("Self-audit"))
    return list(job["steps"])


def _index(steps: list[dict[str, Any]], predicate: Any, what: str) -> int:
    for position, step in enumerate(steps):
        if predicate(step):
            return position
    raise AssertionError(f"the Self-audit job has no step that {what}")


def test_the_resolver_runs_on_the_python_read_from_the_service_dockerfile() -> None:
    steps = _self_audit_steps()
    read = _index(
        steps,
        lambda step: "templates/service/Dockerfile" in str(step.get("run", "")) and step.get("id") == "image",
        "reads the image's Python from templates/service/Dockerfile",
    )
    setup = _index(
        steps,
        lambda step: (
            str(step.get("uses", "")).startswith("actions/setup-python@")
            and "steps.image.outputs.python" in str((step.get("with") or {}).get("python-version", ""))
        ),
        "pins setup-python to the image's Python",
    )
    resolve = _index(
        steps,
        lambda step: "resolve_python_dependencies.py" in str(step.get("run", "")),
        "resolves the requirements files",
    )
    assert read < setup < resolve, (
        f"the resolver must run after the image's Python is read and installed: read {read}, "
        f"setup {setup}, resolve {resolve}"
    )
