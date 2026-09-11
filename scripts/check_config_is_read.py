#!/usr/bin/env python3
"""Verify that every field a generated service's config declares is actually read.

Why this exists
---------------
``MLflowConfig`` declared ``tracking_uri``, ``experiment_name`` and
``enabled``. All three were loaded from ``configs/config.yaml``, validated by
pydantic, and **read by nothing**. ``Trainer._log_to_mlflow`` called
``mlflow.set_experiment()`` and never ``mlflow.set_tracking_uri()``, so every
run went to MLflow's own default whatever the file said. Measured before the
fix::

    config.yaml says:            'sqlite:///mlflow.db'
    config.mlflow.tracking_uri = 'sqlite:///mlflow.db'   <- parsed fine
    mlflow.get_tracking_uri()  = 'file:///.../mlruns'    <- MLflow never saw it

The ``staging`` and ``prod`` profiles name an in-cluster tracking server, so
those were decorative too; retraining in CI worked only because the workflow
exports ``MLFLOW_TRACKING_URI``, which MLflow reads on its own.

**A config that is parsed, validated and ignored is worse than no config.** It
answers "where do my runs go?" with a value that is not the answer, and the
adopter has no way to tell the difference from the outside.

Fixing the MLflow case surfaced the class: **24 of 51 declared fields are read
by nothing**, most of them settable in ``configs/*.yaml``. The worst is not
MLflow. ``model.py`` carries module-level ``NUMERIC_FEATURES`` and
``CATEGORICAL_FEATURES`` constants marked *"TODO: List your categorical feature
column names"*, while ``config.yaml`` carries ``data.categorical_features``
marked *"TODO: Replace with your actual column names"* — two places to declare
the same thing, and only the Python one is consulted. Feature selection is the
first thing an adopter customises.

How this gate behaves
---------------------
Fields that are not read must be listed in ``UNWIRED`` with a reason. That is a
ratchet, not an amnesty: ``test_gate_scope_ratchet.py`` pins the count, so it
can shrink but not grow. Wiring one up means deleting a line here; adding a new
unread field means adding one, in a diff someone reviews.

Deliberately not a "delete the field" gate. Several of these are real product
gaps — the pipeline *should* honour ``data.categorical_features`` — and
deleting the declaration would resolve the inconsistency in the wrong
direction, by removing the promise instead of keeping it.

Exit codes
----------
- 0: every declared field is either read somewhere, or declared unwired.
- 1: a field is neither, or an ``UNWIRED`` entry no longer corresponds to an
  unread field (a stale allowlist hides the next one).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE = REPO_ROOT / "templates" / "service"
CONFIG = SERVICE / "src" / "{@ service_slug @}" / "config.py"

# Declared, settable, and not consumed by any code path. Each entry is a
# statement about the template, with the reason it is still declared.
#
# Ordered worst-first: the top three are the fields the adopter is told to fill
# in before anything else.
UNWIRED: dict[str, str] = {
    "DataConfig.categorical_features": (
        "model.py hardcodes CATEGORICAL_FEATURES at module level; the pipeline never "
        "reads this. Two places to declare one thing, only one consulted."
    ),
    "DataConfig.numerical_features": ("same as categorical_features — model.py's NUMERIC_FEATURES constant wins."),
    "DataConfig.drop_columns": (
        "the ColumnTransformer uses remainder='drop' against an explicit column list, "
        "so unlisted columns are already dropped and this is never consulted."
    ),
    "ModelConfig.resampling_strategy": (
        "models.py implements ResampleClassifier with a `strategy` argument, but "
        "build_pipeline never constructs it — the SMOTE/undersample toggle does nothing."
    ),
    "ModelConfig.logistic_regression": "no code selects a model type from config; build_pipeline is hardcoded.",
    "ModelConfig.random_forest": "same — the RandomForest branch is unreachable from config.",
    "RandomForestConfig.min_samples_split": "nested under an unreachable model branch.",
    "RandomForestConfig.min_samples_leaf": "nested under an unreachable model branch.",
    "RandomForestConfig.n_jobs": "nested under an unreachable model branch.",
    "EnsembleConfig.weights": "no ensemble is built from config; declared for a stacking path not yet shipped.",
    "AdvancedModelConfig.xgboost_params": "xgboost is an opt-in commented dependency; params are a placeholder.",
    "AdvancedModelConfig.lightgbm_params": "lightgbm is an opt-in commented dependency; params are a placeholder.",
    "AdvancedModelConfig.neural_network_params": "no neural backend ships; placeholder for ADR-032's alternative.",
    # Hyperparameters nested under the two model branches build_pipeline never
    # reaches. Listed individually rather than as their parent block, so wiring
    # one branch does not silently amnesty the other's fields.
    "LogisticRegressionConfig.C": "unreachable: build_pipeline hardcodes GradientBoostingClassifier.",
    "LogisticRegressionConfig.max_iter": "unreachable: same branch.",
    "LogisticRegressionConfig.solver": "unreachable: same branch.",
    "LogisticRegressionConfig.class_weight": "unreachable: same branch.",
    "RandomForestConfig.n_estimators": "unreachable: build_pipeline hardcodes GradientBoostingClassifier.",
    "RandomForestConfig.max_depth": "unreachable: same branch.",
    "RandomForestConfig.class_weight": "unreachable: same branch.",
    "ModelConfig.advanced": "gates the xgboost/lightgbm/neural blocks, none of which ship.",
    "QualityGatesConfig.latency_sla_ms": (
        "read only by a unit test asserting its default. Its own description claimed "
        "'Read by the load-test target' and tests/load_test.py does not read it — the "
        "claim is now corrected in config.py."
    ),
    "QualityGatesConfig.promotion_threshold": (
        "promote_to_mlflow.py implements no baseline comparison, so the auto-promotion "
        "delta this field documents is not enforced anywhere. The most consequential "
        "entry in this table: it sits in the promotion-gate surface (D-26)."
    ),
    "ModelConfig.test_size": (
        "superseded by SplitConfig.test_fraction (PR-B3), which the trainer does read. "
        "Two fields for one quantity; this one is the older, unread half."
    ),
}


def _declared_fields() -> dict[str, list[str]]:
    """`Model.field` names, from the AST rather than by importing pydantic."""
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    models: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields = [
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        ]
        if fields:
            models[node.name] = fields
    return models


def _is_test(path: Path) -> bool:
    """Tests are not consumers.

    A field read only by a test is not read by the service. This is not
    pedantry: writing the regression test for the MLflow fix gave its stub a
    ``self.tracking_uri`` attribute, and that alone was enough to make the
    production field look consumed again — the gate went quiet against a tree
    where the wiring had been deliberately removed.
    """
    return "tests" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _read_attributes() -> set[str]:
    """Every attribute name accessed anywhere in the payload, config.py included.

    config.py counts because a validator consuming its own field is genuine
    consumption: ``SplitConfig.validate_columns_present`` reads
    ``self.acknowledge_iid`` and refuses a random split without it, which is
    the field doing exactly its job. Excluding the file flagged that as dead.

    Attribute access, not a bare-name grep. The first version of this gate
    grepped for the field name and **would not have caught the defect it was
    written for**: ``promote_to_mlflow.py`` has a local variable called
    ``tracking_uri``, so ``MLflowConfig.tracking_uri`` looked read while MLflow
    was never told about it. Run against the pre-fix tree, that version
    reported the identical "38 read, 13 unwired" — a gate agreeing with a
    codebase it should have been failing.

    ``obj.tracking_uri`` is the only way a pydantic field is consumed, so that
    is what to look for. ``mlflow.set_tracking_uri(...)`` does not match,
    because the character before the name is an underscore.

    Two limitations, stated rather than hidden.

    *Attribute names are matched globally, not per model.* A field called
    ``enabled`` reads as consumed if **any** object in the payload has
    ``.enabled`` accessed on it. ``MLflowConfig.enabled`` was in exactly that
    position before it was wired: genuinely unread, and invisible here.
    Resolving it needs type inference, which is a large step up in machinery
    for one field, so the gate is honest about being a lower bound — every
    field it flags is really unread, and it can miss one.

    *Dictionary access is invisible.* ``cfg["mlflow"]["tracking_uri"]`` and
    ``**model_dump()`` splatting do not appear as attribute access. Neither is
    used in this payload; if one is introduced, the field reads as unwired and
    the fix is to record it in ``UNWIRED`` with that reason, not to widen this
    back into the substring search that missed the original defect.
    """
    seen: set[str] = set()
    for path in sorted(SERVICE.rglob("*.py")):
        if "__pycache__" in path.parts or _is_test(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # pragma: no cover — render-safety gate owns this
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                seen.add(node.attr)
            # `getattr(cfg, "tracking_uri")` is attribute access spelled out.
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and node.args
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                seen.add(node.args[1].value)
    return seen


def main() -> int:
    if not CONFIG.is_file():
        print(f"FAIL: {CONFIG.relative_to(REPO_ROOT)} not found — this gate would check nothing.")
        return 1

    models = _declared_fields()
    if not models:
        print(f"FAIL: no pydantic models parsed out of {CONFIG.relative_to(REPO_ROOT)}.")
        return 1

    accessed = _read_attributes()
    total = sum(len(v) for v in models.values())
    unread: list[str] = []
    for model, fields in models.items():
        for field in fields:
            if field not in accessed:
                unread.append(f"{model}.{field}")

    undeclared = sorted(set(unread) - set(UNWIRED))
    stale = sorted(set(UNWIRED) - set(unread))

    if undeclared or stale:
        print("FAIL: the service config declares fields whose read status is not accounted for.")
        print()
        if undeclared:
            print("  Declared, settable, and read by nothing — and not listed as unwired:")
            for name in undeclared:
                print(f"    - {name}")
            print()
            print("  Either consume it, or add it to UNWIRED with the reason it is still")
            print("  declared. A config field that is parsed, validated and ignored answers")
            print("  the adopter's question with a value that is not the answer.")
            print()
        if stale:
            print("  Listed as unwired but now read somewhere — remove these entries:")
            for name in stale:
                print(f"    - {name}")
            print()
            print("  A stale allowlist is how the next unread field hides.")
        return 1

    print(
        f"[config-is-read] OK — {total} declared field(s) across {len(models)} model(s); "
        f"{total - len(unread)} read, {len(unread)} declared unwired."
    )
    print(f"[config-is-read]    unwired: {len(UNWIRED)} — this number must go down, never up (ADR-050).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
