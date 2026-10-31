"""Contract — the test-environment installer reads its pins, and refuses a partial set.

Why this exists
---------------
``scripts/install_test_env.py`` replaced inline shell in ``ci-examples.yml``
(ADR-053). The shell carried a count check for a reason Audit Medium-2 wrote
down: with pyarrow and pytest-asyncio missing, tests skipped silently while
coverage looked clean. Moving the logic into Python must not lose that check,
and must not start carrying versions of its own — the installer's value is
that every pin it installs has exactly one owner somewhere else.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "install_test_env.py"


def _load():
    spec = importlib.util.spec_from_file_location("install_test_env", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_four_service_pins_are_read_from_their_owners() -> None:
    """The positive control, on the real tree."""
    module = _load()
    pins = module.service_pins()
    names = {pin.split("~", 1)[0].split("=", 1)[0].strip().lower() for pin in pins}
    assert names == set(module.SERVICE_PIN_NAMES), pins
    assert all("~=" in pin for pin in pins), f"a service pin lost its compatible-release shape: {pins}"


def test_a_missing_service_pin_fails_loudly(tmp_path: Path) -> None:
    """The count check the shell had: a partial set must stop the install, not shrink it."""
    module = _load()
    for rel in module.SERVICE_REQUIREMENTS:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)
    owner = tmp_path / module.SERVICE_REQUIREMENTS[0]
    owner.write_text(
        "\n".join(line for line in owner.read_text(encoding="utf-8").splitlines() if not line.startswith("pyarrow"))
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        module.service_pins(tmp_path)
    assert "expected 4 service pins" in str(excinfo.value)


def test_the_installer_carries_no_version_of_its_own() -> None:
    """One owner per pin: a version literal here would be a second owner nobody bumps."""
    code = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2]
    literal = re.search(r"(~=|==|>=|<=)\s*\d", code)
    assert not literal, f"install_test_env.py declares a version itself: {literal.group(0) if literal else ''}"


def test_the_dry_run_plans_one_resolution_with_every_source() -> None:
    """One resolver run, so a later install cannot silently upgrade an earlier one (ADR-048)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--installer", "pip", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    install_lines = [line for line in proc.stdout.splitlines() if "pip install" in line]
    assert len(install_lines) == 1, f"expected a single install command:\n{proc.stdout}"
    assert "examples/minimal/requirements.txt" in install_lines[0]
    assert "pytest-asyncio" in install_lines[0]
