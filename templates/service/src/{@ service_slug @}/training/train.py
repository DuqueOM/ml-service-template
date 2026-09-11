"""Training pipeline for {@ service_name @}.

Implements the mandatory training sequence:
1. load_data + Pandera validation
2. engineer_features
3. split_train_val_test (temporal if dates exist)
4. cross_validate
5. evaluate with optimal threshold
6. fairness_check (DIR >= 0.80)
7. save_artifacts with SHA256
8. log_to_mlflow
9. quality_gates
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import joblib
import mlflow
import numpy as np
import optuna
import pandas as pd
from sklearn.metrics import f1_score, precision_recall_curve, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score

from ..config import MLflowConfig, QualityGatesConfig, ServiceConfig
from ..schemas import ServiceInputSchema
from .features import FeatureEngineer
from .model import build_pipeline

# PR-B2: training refuses to start when the EDA leakage gate has not
# passed. The check is fail-soft: services that have not yet adopted
# the canonical EDA contract simply skip the gate (with a warning).
# That preserves the upgrade path for existing services while making
# the gate mandatory for any service that has run the new pipeline.
try:
    from common_utils.eda_artifacts import (
        EDAArtifactNotFoundError,
        load_baseline_distributions,
        load_eda_summary,
        load_feature_catalog,
        load_leakage_report,
        load_schema_ranges,
        missing_artifacts,
    )
except ImportError:  # pragma: no cover
    EDAArtifactNotFoundError = FileNotFoundError  # type: ignore[misc,assignment]
    load_baseline_distributions = None  # type: ignore[assignment]
    load_eda_summary = None  # type: ignore[assignment]
    load_feature_catalog = None  # type: ignore[assignment]
    load_leakage_report = None  # type: ignore[assignment]
    load_schema_ranges = None  # type: ignore[assignment]
    missing_artifacts = None  # type: ignore[assignment]

try:
    from ..fairness import run_fairness_audit
except ImportError:  # pragma: no cover
    run_fairness_audit = None  # type: ignore[assignment]

# PR-B3: every successful (and failed) training run writes a
# ``training_manifest.json`` next to its model artifact. Fail-soft so
# legacy services without common_utils on the path keep training,
# but log a clear warning so operators know reproducibility evidence
# is missing.
try:
    from common_utils.training_manifest import MANIFEST_FILENAME, build_initial_manifest, file_sha256
except ImportError:  # pragma: no cover
    build_initial_manifest = None  # type: ignore[assignment]
    file_sha256 = None  # type: ignore[assignment]
    MANIFEST_FILENAME = "training_manifest.json"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — customize per service
# ---------------------------------------------------------------------------
# PR-R2-7: every quality-gate threshold (primary/secondary metric,
# fairness floor, protected attributes) was previously a module-level
# constant. They now live in `configs/quality_gates.yaml`, validated
# by Pydantic at load time, so:
#   * raising a fairness floor is a one-file PR not a code change
#   * a typo fails the run with a clear error, not silently
#   * `--validate-config-only` lets CI verify the file before any
#     expensive training step
# Hyperparameters that DO NOT belong in the governance contract
# (Optuna trials, CV folds, RNG seed) stay here.
# Retained as the fallback experiment name for callers that construct a
# Trainer directly without an MLflowConfig. `MLflowConfig.experiment_name`
# carries the same default, and the config is authoritative when one is
# supplied — this script no longer mutates this global from `main()`, which it
# did, and which worked only because `_log_to_mlflow` happened to read it late.
EXPERIMENT_NAME = "{@ service_name @}-Production"
MODEL_REGISTRY_NAME = "{@ service_name @}Classifier"

DEFAULT_QUALITY_GATES_PATH = "configs/quality_gates.yaml"
DEFAULT_SERVICE_CONFIG_PATH = "configs/config.yaml"

# PR-B2: canonical EDA artifacts location. Override in CI by passing
# ``eda_artifacts_dir=...`` if the EDA was run with a non-default
# output directory (e.g. multi-dataset services).
DEFAULT_EDA_ARTIFACTS_DIR = "eda/artifacts"

OPTUNA_TRIALS = 50
CV_FOLDS = 5
RANDOM_STATE = 42


class EDAGateError(RuntimeError):
    """Raised when EDA artifacts indicate training MUST NOT proceed.

    Distinct exception type so CI can map it to a specific exit code
    and skip the (slow) training body altogether — the alternative is
    a generic RuntimeError that callers can't differentiate from a
    transient failure.
    """


class Trainer:
    """Orchestrates the full training pipeline."""

    def __init__(
        self,
        data_path: str,
        output_dir: str = "models",
        quality_gates: QualityGatesConfig | None = None,
        quality_gates_path: str = DEFAULT_QUALITY_GATES_PATH,
        target_column: str = "target",
        eda_artifacts_dir: str | None = DEFAULT_EDA_ARTIFACTS_DIR,
        mlflow_config: MLflowConfig | None = None,
    ) -> None:
        # MLflow settings arrive as an object rather than being read off a
        # module global. They used to be neither: `configs/config.yaml`
        # declared `mlflow.tracking_uri` and nothing consulted it, so every
        # run went to MLflow's own default whatever the file said. Defaulting
        # to MLflowConfig() keeps direct `Trainer(...)` callers working and
        # gives them the same documented precedence.
        self.mlflow_config = mlflow_config or MLflowConfig()
        self.data_path = data_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.feature_engineer = FeatureEngineer()
        self.target_column = target_column
        self.eda_artifacts_dir = eda_artifacts_dir

        # Load + validate quality gates BEFORE any data work. A typo
        # in quality_gates.yaml should fail in milliseconds, not after
        # a 30-minute Optuna run.
        if quality_gates is None:
            quality_gates = QualityGatesConfig.from_yaml(quality_gates_path)
        # Cross-config sanity: target_column vs protected_attributes.
        quality_gates.validate_against_data(target_column)
        self.gates = quality_gates
        self.quality_gates_path = quality_gates_path  # recorded in manifest

        # PR-B2: EDA gate. Runs in milliseconds (just a JSON read);
        # halts a doomed training run before the 30-minute Optuna
        # search, before MLflow connection, before everything.
        self._enforce_eda_gate()

    def _enforce_eda_gate(self) -> None:
        """Refuse to train when EDA artifacts say we shouldn't.

        Three checks:
          1. Complete canonical EDA packet when
             ``quality_gates.require_eda_artifacts=true``:
             ``eda_summary.json``, ``schema_ranges.json``,
             ``baseline_distributions.parquet``, ``feature_catalog.yaml``,
             and ``leakage_report.json``.
          2. ``leakage_report.json`` — if present and ``status=BLOCKED``,
             raise ``EDAGateError``. This is the LOAD-BEARING check
             that makes "you cannot train on a leaky feature set" a
             hard rule rather than a TODO comment in the runbook.
          3. ``feature_catalog.yaml`` — if present, log the count of
             approved transforms for the audit trail. Loader enforces
             the D-16 rationale invariant on the way in, so a malformed
             catalog ALSO blocks training (raises during load).

        Services with ``quality_gates.require_eda_artifacts=true`` fail
        closed when artifacts are missing. Legacy services may keep the
        field false during migration; they still get warnings so the
        missing evidence is visible in logs and review artifacts.
        """
        require_eda = bool(
            getattr(
                getattr(self, "gates", None),
                "require_eda_artifacts",
                False,
            )
        )
        eda_loaders = (
            load_eda_summary,
            load_schema_ranges,
            load_baseline_distributions,
            load_feature_catalog,
            load_leakage_report,
            missing_artifacts,
        )
        if any(loader is None for loader in eda_loaders):
            msg = (
                "EDA gate skipped: common_utils.eda_artifacts not importable "
                "(legacy training path). PR-B2 wiring deferred."
            )
            if require_eda:
                raise EDAGateError(msg)
            logger.warning(msg)
            return
        if self.eda_artifacts_dir is None:
            msg = "EDA gate skipped: eda_artifacts_dir explicitly disabled"
            if require_eda:
                raise EDAGateError(msg)
            logger.info(msg)
            return

        artifacts_dir = Path(self.eda_artifacts_dir)
        if not artifacts_dir.exists():
            msg = (
                f"EDA gate required but {artifacts_dir} does not exist. "
                "Run `python -m eda.eda_pipeline` before training."
            )
            if require_eda:
                raise EDAGateError(msg)
            logger.warning(msg)
            return

        if require_eda:
            missing = missing_artifacts(artifacts_dir)  # type: ignore[misc]
            if missing:
                names = ", ".join(path.name for path in missing)
                raise EDAGateError(
                    f"EDA gate required but canonical artifacts are missing under {artifacts_dir}: "
                    f"{names}. Run `python -m eda.eda_pipeline` and commit the full artifact packet."
                )

            # Parse every required artifact once so schema/version drift
            # fails before tuning starts. Training only uses leakage +
            # feature catalog directly; the others are required because
            # downstream schema synthesis and drift detection depend on
            # the same packet.
            load_eda_summary(artifacts_dir)  # type: ignore[misc]
            load_schema_ranges(artifacts_dir)  # type: ignore[misc]
            load_baseline_distributions(artifacts_dir)  # type: ignore[misc]

        try:
            report = load_leakage_report(artifacts_dir)  # type: ignore[misc]
        except EDAArtifactNotFoundError:
            msg = (
                f"EDA gate required but leakage_report.json is missing under {artifacts_dir}. "
                "Run the EDA pipeline and commit the resulting artifact packet."
            )
            if require_eda:
                raise EDAGateError(msg)
            logger.warning(
                "EDA gate: leakage_report.json missing under %s — treat as PR-B2-not-yet-adopted and continue.",
                artifacts_dir,
            )
        else:
            if not report.passed:
                raise EDAGateError(
                    f"EDA leakage gate is BLOCKED. Cannot train. "
                    f"Blocked features: {list(report.blocked_features)}. "
                    f"Resolve via leakage_report.json / reports/04_leakage_audit.md and re-run EDA."
                )
            logger.info(
                "EDA leakage gate: PASSED (%d feature(s) audited, 0 blocked)",
                len(report.findings) + len(report.blocked_features),
            )

        try:
            catalog = load_feature_catalog(artifacts_dir)  # type: ignore[misc]
        except EDAArtifactNotFoundError:
            return
        n_transforms = len(catalog.get("transforms", []))
        logger.info(
            "EDA feature catalog: %d transform(s) approved (D-16 rationale enforced at load time)", n_transforms
        )

    def run(self, optuna_trials: int = OPTUNA_TRIALS) -> dict[str, Any]:
        """Execute the complete training pipeline.

        Returns:
            Dict with model metrics and artifact paths.
        """
        # Step 1: Load + validate
        logger.info("Step 1: Loading and validating data")
        df = self._load_data()

        # PR-B3: build the manifest as soon as we have row/column counts.
        # Doing it BEFORE feature engineering means the manifest reflects
        # the AS-INGESTED dataset shape (the auditable input), not a
        # post-FE artefact whose shape would shift on FE changes.
        manifest = self._build_manifest(df, optuna_trials=optuna_trials)

        # Step 2: Feature engineering
        logger.info("Step 2: Engineering features")
        X, y = self.feature_engineer.transform(df)

        # Step 3: Split
        logger.info("Step 3: Splitting train/val/test")
        splits = self._split_data(X, y)
        if manifest is not None:
            manifest.split = self._split_meta

        # Step 4: Hyperparameter tuning with Optuna
        logger.info("Step 4: Optuna hyperparameter tuning (%d trials)", optuna_trials)
        best_params = self._tune_hyperparameters(splits["X_train"], splits["y_train"], n_trials=optuna_trials)

        # Step 5: Train final model + cross-validate
        logger.info("Step 5: Training final model with best params")
        pipeline = build_pipeline(**best_params)
        cv_scores = cross_val_score(
            pipeline,
            splits["X_train"],
            splits["y_train"],
            cv=StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE),
            scoring=self.gates.primary_metric,
        )
        pipeline.fit(splits["X_train"], splits["y_train"])

        # Step 6: Evaluate on test set
        logger.info("Step 6: Evaluating on test set")
        metrics = self._evaluate(pipeline, splits)

        # Step 7: Fairness check
        logger.info("Step 7: Fairness check")
        fairness_metrics = self._fairness_check(
            pipeline,
            splits,
            threshold=metrics.get("optimal_threshold"),
        )
        metrics.update(fairness_metrics)

        # Step 8: Save artifacts
        logger.info("Step 8: Saving artifacts")
        artifact_path = self._save_artifacts(pipeline, metrics)

        # Step 9: Log to MLflow
        logger.info("Step 9: Logging to MLflow")
        self._log_to_mlflow(pipeline, metrics, best_params, artifact_path)

        # Step 10: Quality gates
        logger.info("Step 10: Checking quality gates")
        gates_result = self._quality_gates(metrics)

        # PR-B3: finalise + persist the manifest. Written even if the
        # gates fail (the dict carries `quality_gates_passed: False`)
        # so the audit trail is complete on REJECTED runs too —
        # otherwise we'd lose evidence of the very runs that need
        # the most scrutiny.
        manifest_path = self._finalize_manifest(
            manifest,
            artifact_path=artifact_path,
            metrics=metrics,
            cv_scores=cv_scores,
            best_params=best_params,
            gates_passed=gates_result.get("all_passed", False),
        )

        return {
            "metrics": metrics,
            "cv_scores": cv_scores.tolist(),
            "cv_mean": float(cv_scores.mean()),
            "best_params": best_params,
            "artifact_path": str(artifact_path),
            "quality_gates": gates_result,
            "manifest_path": str(manifest_path) if manifest_path else None,
        }

    # ------------------------------------------------------------------
    # PR-B3 manifest helpers
    # ------------------------------------------------------------------

    def _build_manifest(self, df: pd.DataFrame, *, optuna_trials: int):
        """Construct the initial training manifest.

        Returns ``None`` when ``common_utils.training_manifest`` is not
        importable (legacy path) — the caller treats ``None`` as "no
        manifest will be written". Failure to compute a SHA over the
        input data IS fatal: a manifest without a content hash is
        worse than no manifest at all.
        """
        if build_initial_manifest is None:
            logger.warning(
                "Training manifest skipped: common_utils.training_manifest "
                "not importable. Reproducibility evidence is missing for "
                "this run."
            )
            return None
        try:
            return build_initial_manifest(
                data_path=self.data_path,
                quality_gates_path=self.quality_gates_path,
                target_column=self.target_column,
                n_rows=int(len(df)),
                n_columns=int(df.shape[1]),
                optuna_trials=optuna_trials,
                cv_folds=CV_FOLDS,
                eda_artifacts_dir=self.eda_artifacts_dir,
            )
        except FileNotFoundError as exc:
            # The hash computation requires the data + quality gates
            # files to exist on disk. If either is missing we cannot
            # produce a meaningful manifest; log loudly and continue
            # rather than block training (the operator may be running
            # against an in-memory dataframe).
            logger.warning("Training manifest construction failed: %s", exc)
            return None

    def _finalize_manifest(
        self,
        manifest,
        *,
        artifact_path: Path,
        metrics: dict[str, float],
        cv_scores: np.ndarray,
        best_params: dict[str, Any],
        gates_passed: bool,
    ) -> Path | None:
        """Fill in result fields and persist the manifest next to the model."""
        if manifest is None:
            return None
        manifest.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Prefer monotonic-derived runtime when available; fall back to
        # parsing the started_at field. The manifest module records
        # started_at as wall-clock UTC so a clock skew between the
        # training pod and the manifest writer would only affect this
        # cosmetic field, not the deterministic provenance facts.
        try:
            started = time.strptime(manifest.started_at, "%Y-%m-%dT%H:%M:%SZ")
            manifest.runtime_seconds = max(0.0, time.mktime(time.gmtime()) - time.mktime(started))
        except ValueError:
            manifest.runtime_seconds = None

        manifest.model_artifact_path = str(artifact_path)
        if file_sha256 is not None and Path(artifact_path).exists():
            manifest.model_artifact_sha256 = file_sha256(artifact_path)
        manifest.metrics = {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))}
        manifest.cv_scores = [float(s) for s in cv_scores]
        manifest.best_params = dict(best_params)
        manifest.quality_gates_passed = bool(gates_passed)

        # Write next to the model artifact, not in self.output_dir, so
        # an MLflow run that copies the artifact directory picks up the
        # manifest in the same operation.
        out = Path(artifact_path).parent / MANIFEST_FILENAME
        return manifest.write(out)

    def _load_data(self) -> pd.DataFrame:
        """Load and validate data with Pandera."""
        df = pd.read_csv(self.data_path)
        validated = ServiceInputSchema.validate(df)
        logger.info("Data validated: %d rows, %d columns", len(df), len(df.columns))
        return validated

    def _split_data(self, X: pd.DataFrame, y: pd.Series) -> dict[str, pd.DataFrame | pd.Series]:
        """Split into train/test according to the configured strategy (PR-B3).

        Dispatches on ``self.gates.split.strategy``:

        - ``temporal``: sort by ``timestamp_column`` ascending; last
          ``test_fraction`` rows form the test set. No shuffling. The
          ONLY correct choice when features depend on ordered events
          (D-13).
        - ``grouped``: ``GroupShuffleSplit`` on ``entity_id_column`` so
          all rows of one entity stay on the same side. Mandatory when
          one customer/device produces many rows.
        - ``random``: stratified ``train_test_split``. Allowed only
          when ``acknowledge_iid: true`` is set in the config (the
          ``SplitConfig.validate_columns_present`` check enforces it).

        The split metadata (strategy, columns, sizes, random_state) is
        recorded on ``self._split_meta`` for the training manifest
        (PR-B3 manifest writer).
        """
        from sklearn.model_selection import GroupShuffleSplit, train_test_split

        split_cfg = self.gates.split
        # Cross-check column presence + acknowledgement BEFORE we burn
        # any compute on the split itself.
        split_cfg.validate_columns_present(list(X.columns))

        strategy = split_cfg.strategy
        test_fraction = split_cfg.test_fraction
        random_state = split_cfg.random_state

        if strategy == "temporal":
            ts_col = split_cfg.timestamp_column
            assert ts_col is not None  # validate_columns_present guarantees this
            order = X[ts_col].argsort(kind="mergesort")
            X_sorted = X.iloc[order].reset_index(drop=True)
            y_sorted = y.iloc[order].reset_index(drop=True)
            n_test = max(1, int(round(len(X_sorted) * test_fraction)))
            split_idx = len(X_sorted) - n_test
            X_train, X_test = X_sorted.iloc[:split_idx], X_sorted.iloc[split_idx:]
            y_train, y_test = y_sorted.iloc[:split_idx], y_sorted.iloc[split_idx:]
            logger.info(
                "Temporal split on %s: train=%d (≤ %s), test=%d (> %s)",
                ts_col,
                len(X_train),
                X_sorted[ts_col].iloc[split_idx - 1] if split_idx > 0 else "n/a",
                len(X_test),
                X_sorted[ts_col].iloc[split_idx - 1] if split_idx > 0 else "n/a",
            )
        elif strategy == "grouped":
            group_col = split_cfg.entity_id_column
            assert group_col is not None
            groups = X[group_col].to_numpy()
            gss = GroupShuffleSplit(n_splits=1, test_size=test_fraction, random_state=random_state)
            train_idx, test_idx = next(gss.split(X, y, groups=groups))
            X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
            y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
            n_train_groups = X_train[group_col].nunique()
            n_test_groups = X_test[group_col].nunique()
            overlap = set(X_train[group_col]) & set(X_test[group_col])
            assert not overlap, (
                f"GroupShuffleSplit invariant violated: {len(overlap)} group(s) "
                f"appear in both splits (e.g. {next(iter(overlap))})"
            )
            logger.info(
                "Grouped split on %s: train=%d rows / %d groups, test=%d rows / %d groups",
                group_col,
                len(X_train),
                n_train_groups,
                len(X_test),
                n_test_groups,
            )
        else:  # strategy == "random" — guarded by validate_columns_present
            X_train, X_test, y_train, y_test = train_test_split(
                X,
                y,
                test_size=test_fraction,
                random_state=random_state,
                stratify=y,
            )
            logger.warning(
                "Random split chosen — adopter has set acknowledge_iid=true. "
                "Review the assumption if the model behaves oddly in production."
            )

        # Record split metadata for the training manifest (PR-B3).
        # Stored on self so ``run()`` can pick it up without rerunning
        # the split or threading another return value.
        self._split_meta: dict[str, Any] = {
            "strategy": strategy,
            "timestamp_column": split_cfg.timestamp_column,
            "entity_id_column": split_cfg.entity_id_column,
            "test_fraction": test_fraction,
            "random_state": random_state,
            "n_train": int(len(X_train)),
            "n_test": int(len(X_test)),
        }

        return {
            "X_train": X_train,
            "X_test": X_test,
            "y_train": y_train,
            "y_test": y_test,
        }

    def _tune_hyperparameters(self, X_train: pd.DataFrame, y_train: pd.Series, n_trials: int) -> dict:
        """Optuna hyperparameter search.

        TODO: Define your search space in the objective function.
        """

        def objective(trial: optuna.Trial) -> float:
            # TODO: Define service-specific hyperparameter search space
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 500),
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            }
            pipeline = build_pipeline(**params)
            score = cross_val_score(
                pipeline,
                X_train,
                y_train,
                cv=StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE),
                scoring=self.gates.primary_metric,
            ).mean()
            return score

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
        return study.best_params

    def _evaluate(self, pipeline: Any, splits: dict) -> dict[str, float]:
        """Evaluate model on test set with multiple metrics."""
        X_test = splits["X_test"]
        y_test = splits["y_test"]

        y_prob = pipeline.predict_proba(X_test)[:, 1]

        # Optimal threshold via precision-recall curve
        precisions, recalls, thresholds = precision_recall_curve(y_test, y_prob)
        f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-8)
        optimal_idx = np.argmax(f1_scores)
        optimal_threshold = float(thresholds[optimal_idx]) if optimal_idx < len(thresholds) else 0.5

        y_pred = (y_prob >= optimal_threshold).astype(int)

        return {
            "roc_auc": float(roc_auc_score(y_test, y_prob)),
            "f1": float(f1_score(y_test, y_pred)),
            "optimal_threshold": optimal_threshold,
            "test_size": len(y_test),
        }

    def _fairness_check(self, pipeline: Any, splits: dict, threshold: float | None = None) -> dict[str, float]:
        """Check Disparate Impact Ratio per protected attribute.

        DIR = P(positive | unprivileged) / P(positive | privileged)
        Must be >= ``self.gates.fairness_threshold`` (default 0.80,
        the EOC four-fifths rule) for each protected attribute.

        The check uses the same decision threshold selected during
        evaluation. Running fairness at 0.50 while serving at another
        threshold can pass a model that behaves differently in
        production.
        """
        metrics: dict[str, float] = {}

        if not self.gates.protected_attributes:
            # An EMPTY list is a deliberate, audited choice the operator
            # made in quality_gates.yaml (and validate_against_data has
            # already rejected the demographic-target × empty-list case
            # at config-load time). Skip cleanly without warning spam.
            logger.info("No protected attributes configured — fairness check skipped by design")
            return metrics

        X_test = splits["X_test"]
        y_test = splits["y_test"]
        y_prob = pipeline.predict_proba(X_test)[:, 1]
        operating_threshold = 0.5 if threshold is None else float(threshold)
        y_pred = (y_prob >= operating_threshold).astype(int)

        missing_attrs = [attr for attr in self.gates.protected_attributes if attr not in X_test.columns]
        for attr in missing_attrs:
            logger.warning("Protected attribute '%s' not in test data — fairness gate will fail closed", attr)
            metrics[f"dir_{attr}"] = 0.0
            metrics[f"fairness_missing_{attr}"] = 1.0

        present_attrs = [attr for attr in self.gates.protected_attributes if attr in X_test.columns]
        if not present_attrs:
            return metrics

        sensitive_df = X_test[present_attrs].copy()
        fairness_report_path = self.output_dir / "fairness.json"

        if run_fairness_audit is not None:
            report = run_fairness_audit(
                y_test.to_numpy() if hasattr(y_test, "to_numpy") else np.asarray(y_test),
                y_pred,
                sensitive_df,
                y_prob=y_prob,
                output_path=fairness_report_path,
                intersectional=len(present_attrs) >= 2,
                disparate_impact_threshold=self.gates.fairness_threshold,
            )
            for attr in present_attrs:
                indicators = report.get(attr, {}).get("fairness", {})
                if "disparate_impact_ratio" in indicators:
                    metrics[f"dir_{attr}"] = float(indicators["disparate_impact_ratio"])
                if "equal_opportunity_difference" in indicators:
                    metrics[f"equal_opportunity_gap_{attr}"] = float(indicators["equal_opportunity_difference"])
                if "calibration_parity_difference" in indicators:
                    metrics[f"calibration_parity_gap_{attr}"] = float(indicators["calibration_parity_difference"])
                if bool(indicators.get("consultation_required", False)):
                    metrics[f"fairness_consult_{attr}"] = 1.0
            if bool(report.get("_summary", {}).get("consultation_required", False)):
                metrics["fairness_consult_required"] = 1.0
            metrics["fairness_overall_pass"] = 1.0 if report.get("_summary", {}).get("overall_pass", True) else 0.0
            return metrics

        logger.warning("Fairness helper unavailable; falling back to DIR-only check")
        for attr in present_attrs:
            groups = X_test[attr].unique()
            if len(groups) < 2:
                continue
            # Calculate positive rate per group
            rates = {}
            for group in groups:
                mask = X_test[attr] == group
                rates[group] = float((y_prob[mask] >= operating_threshold).mean())

            # DIR = min_rate / max_rate
            min_rate = min(rates.values())
            max_rate = max(rates.values())
            dir_value = min_rate / max_rate if max_rate > 0 else 0.0
            metrics[f"dir_{attr}"] = dir_value

            if dir_value < self.gates.fairness_threshold:
                logger.warning(
                    "Fairness violation: DIR for %s = %.3f (threshold: %.2f)",
                    attr,
                    dir_value,
                    self.gates.fairness_threshold,
                )

        return metrics

    def _save_artifacts(self, pipeline: Any, metrics: dict) -> Path:
        """Save model with SHA256 checksum."""
        model_path = self.output_dir / "model.joblib"
        joblib.dump(pipeline, model_path)

        # SHA256 for integrity verification
        sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
        meta = {"sha256": sha256, "metrics": metrics}
        meta_path = self.output_dir / "model_metadata.json"
        meta_path.write_text(json.dumps(meta, indent=2))

        logger.info("Model saved: %s (SHA256: %s)", model_path, sha256[:16])
        return model_path

    def _log_to_mlflow(
        self,
        pipeline: Any,
        metrics: dict,
        params: dict,
        artifact_path: Path,
    ) -> None:
        """Log experiment to MLflow, at the configured tracking URI.

        `set_tracking_uri` is the line this method was missing. Without it
        `set_experiment` below bound to whatever MLflow defaulted to, and the
        configured URI — including the in-cluster server the staging and prod
        profiles name — was never contacted.
        """
        if not self.mlflow_config.enabled:
            logger.info("MLflow logging disabled (mlflow.enabled=false) — skipping")
            return

        tracking_uri = self.mlflow_config.resolve_tracking_uri()
        mlflow.set_tracking_uri(tracking_uri)

        # Logged at INFO because "where did my run go" is the first question
        # when a run goes missing, and the answer used to be unobtainable from
        # the output.
        experiment = self.mlflow_config.experiment_name
        logger.info("MLflow tracking URI: %s (experiment: %s)", tracking_uri, experiment)
        mlflow.set_experiment(experiment)

        with mlflow.start_run():
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.log_artifact(str(artifact_path))
            mlflow.log_artifact(str(artifact_path.parent / "model_metadata.json"))

            mlflow.set_tag("git_commit", os.getenv("GIT_SHA", "unknown"))
            mlflow.set_tag("environment", os.getenv("ENVIRONMENT", "development"))

            # Register model
            mlflow.sklearn.log_model(
                pipeline,
                artifact_path="model",
                registered_model_name=MODEL_REGISTRY_NAME,
            )

    def _quality_gates(self, metrics: dict) -> dict[str, bool]:
        """Check all quality gates. ALL must pass for promotion."""
        primary = self.gates.primary_metric
        secondary = self.gates.secondary_metric
        gates = {
            f"{primary} >= {self.gates.primary_threshold}": metrics.get(primary, 0) >= self.gates.primary_threshold,
            f"{secondary} >= {self.gates.secondary_threshold}": metrics.get(secondary, 0)
            >= self.gates.secondary_threshold,
        }

        # Fairness gates
        if "fairness_overall_pass" in metrics:
            gates["fairness audit overall pass"] = bool(metrics["fairness_overall_pass"])
        for attr in self.gates.protected_attributes:
            key = f"dir_{attr}"
            if key in metrics:
                gates[f"DIR({attr}) >= {self.gates.fairness_threshold}"] = metrics[key] >= self.gates.fairness_threshold
            consult_key = f"fairness_consult_{attr}"
            if consult_key in metrics:
                gates[f"DIR({attr}) outside consultation band"] = not bool(metrics[consult_key])
            missing_key = f"fairness_missing_{attr}"
            if missing_key in metrics:
                gates[f"protected attribute present: {attr}"] = False

        all_passed = all(gates.values())
        failed = [name for name, passed in gates.items() if not passed]

        if all_passed:
            logger.info("All quality gates PASSED")
        else:
            logger.warning("Quality gates FAILED: %s", failed)

        return {"all_passed": all_passed, "gates": gates, "failed": failed}


def _load_mlflow_config(config_path: str) -> MLflowConfig:
    """The `mlflow` block from config.yaml, or the built-in defaults.

    A missing or unreadable config file is deliberately NOT fatal here.
    Training has its own required config — `configs/quality_gates.yaml`, which
    is loaded strictly and fails loudly — and a service that has not written a
    `config.yaml` yet should still be able to train against the defaults. What
    must never happen again is the previous behaviour: reading the file,
    validating it, and then ignoring what it said.
    """
    try:
        return ServiceConfig.from_yaml(config_path).mlflow
    except FileNotFoundError:
        logger.info("No %s — using built-in MLflow defaults", config_path)
        return MLflowConfig()
    except Exception as exc:  # noqa: BLE001 — a malformed mlflow block is worth naming
        logger.warning("Could not read the mlflow block from %s (%s) — using defaults", config_path, exc)
        return MLflowConfig()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Train {@ service_name @} model")
    parser.add_argument("--data", help="Path to training CSV (required unless --validate-config-only)")
    parser.add_argument(
        "--experiment",
        default=None,
        help="MLflow experiment name (overrides mlflow.experiment_name in --config)",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_SERVICE_CONFIG_PATH,
        help=(
            "Path to config.yaml. Its `mlflow` block sets the tracking URI and "
            "experiment name; MLFLOW_TRACKING_URI still wins over the file. "
            "Missing file is not an error — the built-in defaults apply."
        ),
    )
    parser.add_argument("--optuna-trials", type=int, default=OPTUNA_TRIALS, help="Optuna trials")
    parser.add_argument(
        "--quality-gates",
        default=DEFAULT_QUALITY_GATES_PATH,
        help="Path to quality_gates.yaml (default: configs/quality_gates.yaml)",
    )
    parser.add_argument(
        "--target-column",
        default="target",
        help="Target column name; cross-checked vs protected_attributes (PR-R2-7).",
    )
    parser.add_argument(
        "--validate-config-only",
        action="store_true",
        help="Load + validate configs/quality_gates.yaml and exit. CI gate (PR-R2-7).",
    )
    args = parser.parse_args()

    # PR-R2-7: cheap CI-time validation that quality_gates.yaml parses
    # AND survives the demographic-target heuristic. Run BEFORE any
    # expensive setup (no MLflow connection, no data load).
    if args.validate_config_only:
        try:
            gates = QualityGatesConfig.from_yaml(args.quality_gates)
            gates.validate_against_data(args.target_column)
        except Exception as exc:  # noqa: BLE001 — surface every config error
            print(f"quality_gates config invalid: {exc}", file=sys.stderr)
            sys.exit(2)
        print(f"quality_gates config OK: {args.quality_gates}")
        sys.exit(0)

    if not args.data:
        parser.error("--data is required unless --validate-config-only is set")

    # The MLflow block used to be unreachable from here: this script mutated a
    # module global for the experiment name and nothing ever read the tracking
    # URI. Both now travel as one object, so `python -m ...training.train` and
    # `python -m ...cli train` resolve them identically.
    mlflow_config = _load_mlflow_config(args.config)
    if args.experiment:
        mlflow_config = mlflow_config.model_copy(update={"experiment_name": args.experiment})

    trainer = Trainer(
        data_path=args.data,
        quality_gates_path=args.quality_gates,
        target_column=args.target_column,
        mlflow_config=mlflow_config,
    )
    result = trainer.run(optuna_trials=args.optuna_trials)

    print(json.dumps(result, indent=2, default=str))
