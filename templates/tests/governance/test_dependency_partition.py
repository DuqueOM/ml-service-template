"""Contract — the partition gate catches all three shapes it was written for.

Why this exists
---------------
``scripts/check_dependency_partition.py`` passes on a clean tree.
``test_gate_scope_ratchet.py`` asserts that much, and passing on a clean tree
is exactly what this repository's *broken* gates also did. The question a
control has to answer is the other one.

Each test here reintroduces one real shape of the defect and asserts the gate
rejects it, naming the offending file so the failure is actionable.

The three shapes, all of which actually happened:

1. **An unguarded import.** ``requirements.txt`` is what the Dockerfile
   installs. It carried mlflow, optuna, pytest, locust and httpx — 20 of the
   24 fixable CRITICAL/HIGH advisories a generated service reported, all 7
   CRITICAL among them, in packages no code in the image imports. One
   ``import mlflow`` on a path the CronJobs reach puts the whole tree back.

2. **A guarded import must still pass.** ``common_utils`` already reaches
   boto3, google-cloud and opentelemetry inside ``try/except ImportError``.
   A gate that rejected those would be wrong, and the pressure to silence it
   would land on the useful half.

3. **A lane that installs the runtime set and then runs a dev tool.** Before
   the split, ``requirements.txt`` carried pytest, so any lane could install
   one file and run the suite. Two were doing exactly that. The first was found
   by reading; the second — ``scripts/test_scaffold.sh`` — was found by CI,
   *after* the fix for the first had already shipped, and it reported

       ModuleNotFoundError: No module named 'numpy'

   blaming a package that was installed, because ``pytest`` had fallen through
   to an interpreter outside the venv. That second miss is why the check is
   file-wide instead of one fix per consumer.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / "scripts" / "check_dependency_partition.py"
RUNTIME_MODULE = REPO_ROOT / "templates" / "service" / "src"


def _sandbox(tmp_path: Path) -> Path:
    """A copy of the parts of the tree the gate reads.

    A real copy rather than a synthetic fixture: the gate parses the actual
    Dockerfile for its entrypoints and walks the actual import graph, so a
    hand-built stand-in would be testing a different program.
    """
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(GATE, root / "scripts" / GATE.name)

    service_src = REPO_ROOT / "templates" / "service"
    service_dst = root / "templates" / "service"
    service_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        service_src,
        service_dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
    )
    return root


def _run(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / GATE.name)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _slug_dir(root: Path) -> Path:
    src = root / "templates" / "service" / "src"
    packages = [d for d in src.iterdir() if d.is_dir() and not d.name.startswith("__")]
    assert len(packages) == 1, f"expected one package under src/, found {packages}"
    return packages[0]


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    """The control. Without it every assertion below could be a false alarm."""
    code, out = _run(_sandbox(tmp_path))
    assert code == 0, out
    assert "runtime module(s) reachable" in out
    assert "training/dev-only package(s)" in out


@pytest.mark.parametrize(
    ("module", "package"),
    [
        ("monitoring/drift_detection.py", "mlflow"),
        ("monitoring/performance_monitor.py", "optuna"),
        ("training/features.py", "mlflow"),
    ],
)
def test_an_unguarded_training_import_is_rejected(tmp_path: Path, module: str, package: str) -> None:
    root = _sandbox(tmp_path)
    target = _slug_dir(root) / module
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace("import logging", f"import logging\nimport {package}", 1), encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"the gate accepted `import {package}` in {module}, which the image does not install:\n{out}"
    assert module.rsplit("/", 1)[-1] in out, f"failure must name the file:\n{out}"
    assert package in out


def test_a_guarded_training_import_is_accepted(tmp_path: Path) -> None:
    """Optional dependencies are a documented pattern, not a violation."""
    root = _sandbox(tmp_path)
    target = _slug_dir(root) / "monitoring" / "drift_detection.py"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            "import logging",
            "import logging\n\ntry:\n    import optuna\nexcept ImportError:\n    optuna = None",
            1,
        ),
        encoding="utf-8",
    )

    code, out = _run(root)
    assert code == 0, f"a guarded import was rejected; boto3/google-cloud/opentelemetry use this same shape:\n{out}"


def test_losing_every_entrypoint_is_an_error_not_an_empty_pass(tmp_path: Path) -> None:
    """The seeds are parsed out of the Dockerfile so they cannot drift silently.

    That only helps if finding none is an error rather than an empty walk
    reporting success.
    """
    root = _sandbox(tmp_path)
    dockerfile = root / "templates" / "service" / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    text = text.replace('python -c "import', 'python -c "IMPORT')
    text = text.replace('CMD ["uvicorn", "app.main:app"', 'CMD ["sleep", "infinity"')
    dockerfile.write_text(text, encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"the gate walked nothing and reported success:\n{out}"
    assert "no runtime entrypoints found" in out


def test_partial_narrowing_is_caught_by_the_ratchet(tmp_path: Path) -> None:
    """Losing SOME entrypoints still passes the gate — and must fail the ratchet.

    Breaking only the smoke-import steps leaves the uvicorn CMD, so the walk
    resolves 11 modules from 1 entrypoint instead of 16 from 7 and the gate
    still prints OK. That is correct division of labour: the gate answers "does
    anything runtime import a training package", and
    ``test_gate_scope_ratchet.py`` answers "did the gate quietly start looking
    at less". This test pins the second half, because a floor nobody exercises
    is a floor nobody notices.
    """
    root = _sandbox(tmp_path)
    dockerfile = root / "templates" / "service" / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    dockerfile.write_text(text.replace('python -c "import', 'python -c "IMPORT'), encoding="utf-8")

    code, out = _run(root)
    assert code == 0, out  # the gate alone does not object

    match = re.search(r"OK — (\d+) runtime module", out)
    assert match, f"the gate stopped reporting its scope:\n{out}"
    narrowed = int(match.group(1))

    floor = _ratchet_floor("check_dependency_partition")
    assert narrowed < floor, (
        f"narrowing the Dockerfile to one entrypoint still reports {narrowed} modules, "
        f"at or above the recorded floor of {floor}. Either the floor is too low to "
        f"detect this, or the walk no longer depends on the smoke-import steps."
    )


def _ratchet_floor(gate: str) -> int:
    """The recorded floor, read from the ratchet rather than retyped."""
    ratchet = (REPO_ROOT / "templates" / "tests" / "governance" / "test_gate_scope_ratchet.py").read_text(
        encoding="utf-8"
    )
    # The tuple's first element is itself a regex containing ")", so a
    # "everything up to the first paren" pattern stops inside it. Anchor on the
    # closing quote of that string instead.
    match = re.search(rf'"{gate}":\s*\(\s*r?"(?:[^"\\]|\\.)*"\s*,\s*(\d+)', ratchet)
    assert match, f"{gate} has no floor in test_gate_scope_ratchet.py"
    return int(match.group(1))


@pytest.mark.parametrize(
    ("lane", "old", "new"),
    [
        # The failure CI found, after the fix for the other one had shipped.
        (
            "scripts/test_scaffold.sh",
            "pip install --quiet -r requirements-dev.txt",
            "pip install --quiet -r requirements.txt",
        ),
        # The failure found by reading, before the split was pushed.
        (
            ".github/workflows/template-context-tests.yml",
            "pip install -r templates/service/requirements-dev.txt",
            "pip install -r templates/service/requirements.txt",
        ),
    ],
    ids=["test_scaffold.sh", "template-context-tests.yml"],
)
def test_a_lane_that_installs_runtime_and_runs_dev_tools_is_rejected(
    tmp_path: Path, lane: str, old: str, new: str
) -> None:
    root = _sandbox(tmp_path)
    source = REPO_ROOT / lane
    target = root / lane
    target.parent.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8")
    assert old in text, f"{lane} no longer contains {old!r}; this control is stale"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"the gate accepted {lane} installing the runtime set and running a dev tool:\n{out}"
    assert Path(lane).name in out
    assert "runs a dev tool" in out


def test_a_lane_in_another_group_is_not_flagged(tmp_path: Path) -> None:
    """`examples/minimal` has its own venv and its own pytest pin (ADR-048).

    A gate that flagged it would be wrong, and this is the assertion that keeps
    the runtime-only pattern from being widened into a false positive.
    """
    root = _sandbox(tmp_path)
    lane = root / ".github" / "workflows" / "ci-examples.yml"
    lane.parent.mkdir(parents=True, exist_ok=True)
    lane.write_text(
        "jobs:\n"
        "  demo:\n"
        "    steps:\n"
        "      - run: pip install -r examples/minimal/requirements.txt\n"
        "      - run: pytest examples/minimal\n",
        encoding="utf-8",
    )

    code, out = _run(root)
    assert code == 0, f"a lane installing examples/minimal/requirements.txt was flagged:\n{out}"
