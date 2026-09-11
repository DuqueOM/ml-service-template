"""Contract — the config-is-read gate catches the defect it was written for.

Why this exists
---------------
The first version of this gate **would not have caught the bug it was written
for.** It grepped the payload for each field name, and
``promote_to_mlflow.py`` has a local variable called ``tracking_uri``, so
``MLflowConfig.tracking_uri`` read as consumed while MLflow was never told
about it. Run against the pre-fix tree it reported the identical
"38 read, 13 unwired" — a control agreeing with a codebase it existed to fail.

That was found by running it against ``main`` instead of trusting it on a fixed
tree, and it is the reason this file exists. The detection is now attribute
access via AST, and the assertion below is the one that matters: **against the
tree where the defect is present, the gate must name it.**

The unwired ratchet
-------------------
``UNWIRED`` records fields that are declared, settable and consumed by nothing
— 24 of 51 at the time of writing, the worst being
``data.categorical_features``, which the adopter is told to fill in and which
``model.py`` overrides with a module-level constant. Those are product gaps, so
the entries stay and the count is pinned: it may shrink, never grow.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / "scripts" / "check_config_is_read.py"
CONFIG = REPO_ROOT / "templates" / "service" / "src" / "{@ service_slug @}" / "config.py"
TRAIN = REPO_ROOT / "templates" / "service" / "src" / "{@ service_slug @}" / "training" / "train.py"

# Measured 2026-09-10. Lowering it is the point; raising it is a decision.
UNWIRED_CEILING = 24


def _sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(GATE, root / "scripts" / GATE.name)
    shutil.copytree(
        REPO_ROOT / "templates" / "service",
        root / "templates" / "service",
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


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    code, out = _run(_sandbox(tmp_path))
    assert code == 0, out
    assert "declared field(s)" in out


def test_the_unwired_list_does_not_grow() -> None:
    """A ratchet on the number that matters.

    The gate's own scope floor counts *declared* fields, which grows when the
    config grows. What must not grow is the number of fields that are declared
    and ignored.
    """
    source = GATE.read_text(encoding="utf-8")
    block = source.split("UNWIRED: dict[str, str] = {", 1)[1].split("\n}", 1)[0]
    entries = re.findall(r'^\s*"([A-Za-z]+\.[A-Za-z_]+)":', block, re.M)
    assert entries, "no UNWIRED entries parsed — this assertion would be vacuous"
    assert len(entries) <= UNWIRED_CEILING, (
        f"{len(entries)} fields are declared and read by nothing, up from {UNWIRED_CEILING}. "
        f"Every entry here is a value an adopter can set with no effect. Wire the new one "
        f"up, or raise this ceiling deliberately and say why in ADR-050."
    )
    assert len(set(entries)) == len(entries), "duplicate UNWIRED entries"


def test_removing_the_mlflow_wiring_is_detected(tmp_path: Path) -> None:
    """The original defect, reintroduced: the config is loaded and not consumed.

    This is the assertion the grep-based version failed. It passed on the
    broken tree because a local variable elsewhere shared the field's name.
    """
    root = _sandbox(tmp_path)
    slug_dir = root / "templates" / "service" / "src" / "{@ service_slug @}"

    # Undo the fix: the trainer stops reading the config object.
    train = slug_dir / "training" / "train.py"
    text = train.read_text(encoding="utf-8")
    text = text.replace("tracking_uri = self.mlflow_config.resolve_tracking_uri()", 'tracking_uri = "file:./mlruns"')
    text = text.replace("experiment = self.mlflow_config.experiment_name", "experiment = EXPERIMENT_NAME")
    text = text.replace("if not self.mlflow_config.enabled:", "if False:")
    train.write_text(text, encoding="utf-8")

    # And the resolver goes with it, since it is the only other consumer.
    config = slug_dir / "config.py"
    cfg_text = config.read_text(encoding="utf-8")
    cfg_text = cfg_text.replace(
        'return os.getenv("MLFLOW_TRACKING_URI") or self.tracking_uri',
        'return os.getenv("MLFLOW_TRACKING_URI") or "file:./mlruns"',
    )
    config.write_text(cfg_text, encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"the gate accepted a config block that nothing reads:\n{out}"
    assert "tracking_uri" in out, f"the failure must name the field:\n{out}"


def test_a_stale_unwired_entry_is_rejected(tmp_path: Path) -> None:
    """An allowlist entry for a field that IS read must be removed.

    A stale entry is how the next unread field hides: the list stops describing
    the tree, and nobody can tell which half is wrong.
    """
    root = _sandbox(tmp_path)
    gate = root / "scripts" / GATE.name
    text = gate.read_text(encoding="utf-8")
    text = text.replace(
        "UNWIRED: dict[str, str] = {",
        'UNWIRED: dict[str, str] = {\n    "MLflowConfig.tracking_uri": "stale — this field is read",',
        1,
    )
    gate.write_text(text, encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"a stale allowlist entry was accepted:\n{out}"
    assert "now read somewhere" in out


def test_a_new_unaccounted_field_is_rejected(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    config = root / "templates" / "service" / "src" / "{@ service_slug @}" / "config.py"
    text = config.read_text(encoding="utf-8")
    # Anchored on the class declaration, not on a field's default value. The
    # first version anchored on `tracking_uri: str = "file:./mlruns"` and broke
    # the moment ADR-047 changed that default to sqlite — a test that fails
    # because a value it does not care about moved is a test people learn to
    # edit rather than read.
    anchor = "class MLflowConfig(BaseModel):"
    assert anchor in text, "MLflowConfig is gone; this control is stale"
    text = text.replace(
        anchor,
        anchor + '\n    invented_and_ignored: str = "nobody reads this"',
        1,
    )
    config.write_text(text, encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"a brand-new unread field was accepted:\n{out}"
    assert "invented_and_ignored" in out


@pytest.mark.parametrize("field", ["categorical_features", "numerical_features", "resampling_strategy"])
def test_the_yaml_warns_about_the_worst_ones(field: str) -> None:
    """The fields an adopter is most likely to edit must say they do nothing.

    A gate that counts them protects the repository. A banner in the file the
    adopter edits protects the adopter, who never runs the gate.
    """
    yaml_text = (REPO_ROOT / "templates" / "service" / "configs" / "config.yaml").read_text(encoding="utf-8")
    assert field in yaml_text, f"{field} is no longer in config.yaml — this test is stale"
    # The banner precedes the block; assert both appear and the warning is above.
    assert "NOT YET WIRED" in yaml_text, "config.yaml lost its unwired banners"
    warning_positions = [m.start() for m in re.finditer(r"NOT YET WIRED", yaml_text)]
    field_position = yaml_text.index(field)
    assert any(pos < field_position for pos in warning_positions), (
        f"'{field}' appears in config.yaml with no NOT YET WIRED banner above it. "
        f"An adopter reading top to bottom would set it and expect it to work."
    )
