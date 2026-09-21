"""Contract — the pin-shape gate rejects the shapes that look pinned and are not.

Why this exists
---------------
D-05 requires ``~=`` for every dependency, and until now that was policy,
prose and a Dependabot setting. Dependabot opened

    dependabot/pip/templates/service/pytest-gte-8.3-and-lt-9.2

which is the shape it proposes when it widens a constraint: ``pytest>=8.3,<9.2``
in place of ``pytest ~= 9.1.1``. Nothing in the repository would have caught
that. The coherence gate compares the two lanes' pins to each other and a
widened range present in both lanes agrees with itself; the partition gate
looks at which distributions appear, not how they are bounded.

The negative controls below are the three shapes that pass as "pinned" to a
reader: a range, an equality pin that belongs in a lockfile, and a bare name.
The positive control is the current tree, which must keep passing — a gate that
fires on the repository it guards is a gate that gets disabled.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / "scripts" / "check_pin_shape.py"
TARGET = Path("templates/service/requirements.txt")


def _sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(GATE, root / "scripts" / GATE.name)
    for rel in (Path("templates/service"), Path("examples/minimal")):
        source = REPO_ROOT / rel
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", ".git", "data", "models", "reports", "results"),
        )
    return root


def _run(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / GATE.name)], cwd=root, capture_output=True, text=True, timeout=120
    )
    return proc.returncode, proc.stdout + proc.stderr


def _rewrite(root: Path, dependency: str, replacement: str) -> None:
    path = root / TARGET
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(dependency):
            lines[index] = replacement
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise AssertionError(f"{dependency} is no longer declared in {TARGET}; this control is stale")


def test_the_current_tree_passes(tmp_path: Path) -> None:
    """The positive control. A gate that fires on its own repository gets disabled."""
    code, out = _run(_sandbox(tmp_path))
    assert code == 0, out
    assert "compatible-release" in out


def test_a_widened_range_is_rejected(tmp_path: Path) -> None:
    """The exact shape Dependabot proposed: pytest>=8.3,<9.2 instead of ~= 9.1.1."""
    root = _sandbox(tmp_path)
    _rewrite(root, "pandas", "pandas>=2.3.3,<3.1")
    code, out = _run(root)
    assert code == 1, f"a widened range was accepted:\n{out}"
    assert ">=2.3.3,<3.1" in out and "pandas" in out and "D-05" in out


def test_an_equality_pin_is_rejected(tmp_path: Path) -> None:
    """`==` belongs in a lockfile; in a declaration it freezes security patches out."""
    root = _sandbox(tmp_path)
    _rewrite(root, "pandas", "pandas==2.3.3")
    code, out = _run(root)
    assert code == 1, f"an equality pin was accepted:\n{out}"
    assert "lockfile" in out


def test_an_unpinned_name_is_rejected(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    _rewrite(root, "pandas", "pandas")
    code, out = _run(root)
    assert code == 1, f"an unpinned dependency was accepted:\n{out}"
    assert "(no specifier)" in out


def test_the_gate_refuses_to_pass_on_an_empty_scan(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    for path in (root / "templates" / "service").rglob("requirements*.txt"):
        path.unlink()
    (root / "examples" / "minimal" / "requirements.txt").unlink()
    (root / "templates" / "service" / "pyproject.toml").unlink()
    code, out = _run(root)
    assert code == 1, f"the gate examined nothing and reported success:\n{out}"
    assert "no dependency declarations found" in out


def test_the_scope_is_reported_for_the_ratchet() -> None:
    proc = subprocess.run([sys.executable, str(GATE)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    match = re.search(r"OK — (\d+) pin\(s\)", proc.stdout)
    assert match, f"no scope count in the success line:\n{proc.stdout}"
    assert int(match.group(1)) >= 74, f"only {match.group(1)} pins checked"
