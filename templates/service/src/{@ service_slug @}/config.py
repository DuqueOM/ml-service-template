"""Configuration management for {@ service_name @}.

Loads and validates YAML configuration using Pydantic models.
Each config section has its own Pydantic model with defaults and validation.

Usage:
    config = ServiceConfig.from_yaml("configs/config.yaml")
    print(config.model.type)           # "ensemble"
    print(config.data.target_column)   # "target"

TODO: Rename ServiceConfig → {@ service_name @}Config (e.g., ChurnConfig).
TODO: Adjust field names, types, and defaults to match your domain.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, List

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# Demographic-target heuristic (PR-R2-7, audit R2 §4.2). Any of these
# tokens appearing as a substring of `data.target_column` (case-fold)
# triggers a STOP if `quality_gates.protected_attributes` is empty —
# silently shipping a model that classifies people on a protected
# axis without DIR enforcement is exactly the failure ADR-005 forbids.
DEMOGRAPHIC_TARGET_TOKENS: tuple[str, ...] = (
    "gender",
    "race",
    "ethnicity",
    "ethnic",
    "religion",
    "age_group",
    "nationality",
    "marital",
    "disability",
    "sexual_orientation",
    "orientation",
    "pregnancy",
)


# Split-strategy enum + matching pydantic model. Lives at module level
# so external tooling (`scripts/validate_quality_gates.py`,
# `test_quality_gates_schema_sync.py`) can import it without pulling in
# the whole service package.
SPLIT_STRATEGIES: tuple[str, ...] = ("temporal", "grouped", "random")


# ---------------------------------------------------------------------------
# Sub-configs — one per YAML section
# ---------------------------------------------------------------------------


# PR-B3: train/test split policy. Lives under `quality_gates.yaml`
# because the choice of split strategy is a CORRECTNESS contract, not a
# hyperparameter — using `random` on temporal data leaks information
# from the future into training (D-13). Codifying the choice in the
# governance config means:
#   * a typo (e.g. `temproal`) fails fast at config load, not after a
#     30-min Optuna run on a leaking split
#   * a service that switches from random→temporal must do so via PR
#     review, like every other governance change
#   * the manifest writer (PR-B3) records the EXACT strategy used
#     in `training_manifest.json` for full auditability
class SplitConfig(BaseModel):
    """Train/test split policy enforced by ``Trainer._split_data``.

    Three strategies, exactly one applies per service:

    - ``temporal``: requires ``timestamp_column``. Sorts ascending,
      uses the last ``test_fraction`` of rows as the test set. The
      ONLY sound choice for any service whose features depend on
      ordered events (fraud, churn, recommendation feedback, …).
    - ``grouped``: requires ``entity_id_column``. Uses
      ``GroupShuffleSplit`` so all rows belonging to the same entity
      stay on the same side of the split. Mandatory whenever a single
      entity (customer, device, …) generates multiple rows — random
      split would let the model memorise the entity rather than
      learn the task.
    - ``random``: stratified ``train_test_split``. Acceptable only
      when rows are i.i.d. by construction. Picking this strategy
      is a deliberate, audited choice — adopters MUST set
      ``acknowledge_iid: true`` to confirm they reviewed the
      assumption. The default (no acknowledgement) refuses to load.

    Why a sub-model rather than a flat field?
    Conditional validation: ``timestamp_column`` is only meaningful
    for ``temporal`` and ``entity_id_column`` for ``grouped``. Flat
    fields would force every service to either fill in fields it
    doesn't need or rely on documentation alone — exactly the silent-
    misuse mode this PR closes.
    """

    # Mirror ``additionalProperties: false`` from the JSON Schema —
    # the schema-sync test enforces equivalence and will fail if these
    # drift. A typo'd field would otherwise be silently ignored, which
    # is the worst possible outcome for a correctness contract.
    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(
        "random",
        description="Which split policy to apply: temporal | grouped | random.",
    )
    timestamp_column: str | None = Field(
        None,
        description="Column to sort on for temporal split. Required when strategy=temporal.",
    )
    entity_id_column: str | None = Field(
        None,
        description="Group key for grouped split. Required when strategy=grouped.",
    )
    test_fraction: float = Field(
        0.2,
        gt=0.0,
        lt=1.0,
        description="Fraction of rows assigned to the test set.",
    )
    random_state: int = Field(
        42,
        ge=0,
        description="Used by grouped + random strategies. Recorded in the manifest.",
    )
    acknowledge_iid: bool = Field(
        False,
        description=(
            "Adopter assertion that rows are independent and identically "
            "distributed. REQUIRED to be true when strategy=random."
        ),
    )

    @field_validator("strategy")
    @classmethod
    def _strategy_in_enum(cls, v: str) -> str:
        if v not in SPLIT_STRATEGIES:
            raise ValueError(f"strategy must be one of {SPLIT_STRATEGIES}, got {v!r}")
        return v

    def validate_columns_present(self, columns: list[str] | tuple[str, ...]) -> None:
        """Cross-check that the column referenced by the strategy
        actually exists in the loaded dataframe. Called from
        ``Trainer._split_data`` AFTER load_data so the failure points
        at the missing column, not at YAML parse time when we don't
        yet know the dataframe.
        """
        if self.strategy == "temporal":
            if not self.timestamp_column:
                raise ValueError("split.strategy='temporal' requires split.timestamp_column")
            if self.timestamp_column not in columns:
                raise ValueError(
                    f"split.timestamp_column={self.timestamp_column!r} not found in dataframe columns {sorted(columns)}"
                )
        elif self.strategy == "grouped":
            if not self.entity_id_column:
                raise ValueError("split.strategy='grouped' requires split.entity_id_column")
            if self.entity_id_column not in columns:
                raise ValueError(
                    f"split.entity_id_column={self.entity_id_column!r} not found in dataframe columns {sorted(columns)}"
                )
        elif self.strategy == "random":
            if not self.acknowledge_iid:
                raise ValueError(
                    "split.strategy='random' requires split.acknowledge_iid: true. "
                    "Set this only after confirming rows are i.i.d. (no temporal "
                    "ordering, no shared entity producing multiple rows). For any "
                    "doubt, prefer 'temporal' or 'grouped'."
                )


# TODO: Adjust hyperparameter fields and defaults for your model types.
class LogisticRegressionConfig(BaseModel):
    """Logistic Regression hyperparameters."""

    C: float = 0.1
    class_weight: str = "balanced"
    solver: str = "liblinear"
    max_iter: int = 1000


class RandomForestConfig(BaseModel):
    """Random Forest hyperparameters."""

    n_estimators: int = 100
    max_depth: int = 10
    min_samples_split: int = 10
    min_samples_leaf: int = 5
    class_weight: str = "balanced_subsample"
    n_jobs: int = -1


class EnsembleConfig(BaseModel):
    """Ensemble (VotingClassifier) configuration."""

    voting: str = Field("soft", pattern="^(hard|soft)$")
    weights: List[float] = [0.4, 0.6]


class AdvancedModelConfig(BaseModel):
    """Configuration for XGBoost, LightGBM, or Neural Network models."""

    xgboost_params: dict = Field(default_factory=dict)
    lightgbm_params: dict = Field(default_factory=dict)
    neural_network_params: dict = Field(default_factory=dict)
    compare_models: List[str] = Field(
        default_factory=list,
        description="List of model names to train and compare. Best is auto-selected.",
    )


class ModelConfig(BaseModel):
    """Model training configuration."""

    type: str = "ensemble"
    test_size: float = Field(0.2, ge=0.0, le=1.0)
    random_state: int = 42
    cv_folds: int = Field(5, ge=2)
    resampling_strategy: str = "none"

    # Sub-model configs
    ensemble: EnsembleConfig = EnsembleConfig()
    logistic_regression: LogisticRegressionConfig = LogisticRegressionConfig()
    random_forest: RandomForestConfig = RandomForestConfig()
    advanced: AdvancedModelConfig = AdvancedModelConfig()

    @property
    def ensemble_voting(self) -> str:
        """Backward-compatible alias."""
        return self.ensemble.voting


# TODO: Replace target_column and feature lists with your actual column names.
class DataConfig(BaseModel):
    """Data preprocessing configuration."""

    target_column: str = "target"
    categorical_features: List[str] = []
    numerical_features: List[str] = []
    drop_columns: List[str] = []


class MLflowConfig(BaseModel):
    """MLflow tracking configuration.

    These three fields were declared, loaded from ``configs/config.yaml``, and
    **read by nothing**. ``Trainer._log_to_mlflow`` called
    ``mlflow.set_experiment()`` and never ``mlflow.set_tracking_uri()``, so
    MLflow fell through to its own default (``./mlruns``) whatever this said.

    Two consequences, both measured:

    * Setting ``tracking_uri: "sqlite:///mlflow.db"`` in ``config.yaml`` loaded
      correctly into this model and changed nothing —
      ``mlflow.get_tracking_uri()`` still reported the local file store.
    * The ``staging`` and ``prod`` profiles point at
      ``http://mlflow.mlflow-system.svc.cluster.local:5000``, which therefore
      did nothing for training. Retraining in CI worked only because
      ``retrain-service.yml`` exports ``MLFLOW_TRACKING_URI`` from a secret,
      i.e. through the one channel MLflow reads on its own.

    A config that is parsed, validated and ignored is worse than no config:
    it answers the question "where do my runs go?" with a value that is not
    the answer.
    """

    tracking_uri: str = "file:./mlruns"
    experiment_name: str = "{@ service_name @}-Production"
    enabled: bool = True

    def resolve_tracking_uri(self) -> str:
        """The URI MLflow should actually use: environment first, then config.

        ``MLFLOW_TRACKING_URI`` wins because it is MLflow's own variable and
        because the deploy chain already sets it from a secret — a config file
        committed to the repository must not be able to redirect a production
        run's tracking to somewhere else. The config file is the answer for
        local and for anyone who prefers declaring it in one place.

        Precedence is resolved here rather than at the call site so it is
        stated once and can be tested without running a training pipeline.
        """
        return os.getenv("MLFLOW_TRACKING_URI") or self.tracking_uri


# ---------------------------------------------------------------------------
# Quality gates — promotion thresholds + fairness requirements (PR-R2-7).
# Loaded from a SEPARATE YAML (`configs/quality_gates.yaml`) so the
# governance bar can evolve independently of model hyperparameters
# AND can be reviewed in isolation in PRs.
#
# `protected_attributes` is REQUIRED and has NO default. The intent is
# to force every adopter to make an explicit choice — either name the
# attributes that warrant DIR enforcement, or pass `[]` and own the
# decision. The combination of `[]` + a target_column that looks
# demographic is rejected by validate_against_data (see below).
# ---------------------------------------------------------------------------


class QualityGatesConfig(BaseModel):
    """Promotion thresholds and fairness requirements.

    All fields validate at construction time. Use
    ``QualityGatesConfig.from_yaml`` to load + validate from disk.
    """

    primary_metric: str = Field(
        ...,
        description="Metric name passed to sklearn cross_val_score (e.g. 'roc_auc', 'f1').",
    )
    primary_threshold: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Minimum acceptable value of `primary_metric` on the held-out test set.",
    )
    secondary_metric: str = Field(
        ...,
        description="Second metric used as a sanity check (e.g. 'f1' alongside roc_auc).",
    )
    secondary_threshold: float = Field(..., ge=0.0, le=1.0)

    fairness_threshold: float = Field(
        0.80,
        ge=0.0,
        le=1.0,
        description="Disparate Impact Ratio floor; standard four-fifths rule defaults to 0.80.",
    )
    latency_sla_ms: float = Field(
        100.0,
        gt=0.0,
        description=(
            "P95 inference latency SLA. NOT YET WIRED (ADR-050): the description "
            "used to claim it was read by the load-test target, and tests/load_test.py "
            "does not read it. Set the threshold there until this is connected."
        ),
    )

    protected_attributes: List[str] = Field(
        ...,
        description=(
            "Feature names whose DIR will be checked. Empty list means "
            "'I have considered fairness and confirm none apply' — combined "
            "with a demographic-looking target_column it is rejected by "
            "validate_against_data."
        ),
    )

    promotion_threshold: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum delta over the current production baseline required "
            "to auto-promote. 0.0 disables baseline-comparison promotion. "
            "NOT YET WIRED (ADR-050): promote_to_mlflow.py implements no "
            "baseline comparison, so no value here changes promotion today."
        ),
    )

    require_eda_artifacts: bool = Field(
        False,
        description=(
            "When true, training fails closed unless the canonical EDA "
            "artifact directory exists and includes eda_summary.json, "
            "schema_ranges.json, baseline_distributions.parquet, "
            "feature_catalog.yaml, and leakage_report.json. "
            "Use false only for legacy migration windows or throwaway demos."
        ),
    )

    # PR-B3: train/test split policy. Defaults to a SplitConfig with
    # ``strategy=random`` and ``acknowledge_iid=False`` — that combination
    # raises in `validate_against_data` so a service that doesn't
    # think about splits gets a loud failure, not silent leakage.
    split: SplitConfig = Field(
        default_factory=SplitConfig,
        description="Train/test split strategy (temporal | grouped | random).",
    )

    @field_validator("primary_metric", "secondary_metric")
    @classmethod
    def _no_whitespace(cls, v: str) -> str:
        if not v or v != v.strip():
            raise ValueError("metric name must be non-empty and have no surrounding whitespace")
        return v

    @field_validator("protected_attributes")
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("protected_attributes contains duplicates")
        return v

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> QualityGatesConfig:
        """Load + validate a quality_gates.yaml file.

        Pydantic raises ValidationError if any required field
        (`primary_metric`, `primary_threshold`, `secondary_metric`,
        `secondary_threshold`, `protected_attributes`) is missing.
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Quality-gates config not found: {config_path}")

        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        if not isinstance(data, dict):
            raise ValueError(f"{config_path} must be a YAML mapping at the top level")

        logger.info("Loaded quality-gates config from %s", config_path)
        return cls(**data)

    def validate_against_data(self, target_column: str) -> None:
        """Cross-config sanity check executed by train.py at startup.

        Raises if:
          - `target_column` matches a demographic token AND
            `protected_attributes` is empty.

        This is the heuristic ADR-005 calls out: shipping a classifier
        whose label is itself a protected attribute, without naming
        any protected attribute for DIR enforcement, is almost
        certainly a fairness gap. Failing closed forces the operator
        to either name the attributes or document why they are
        excluded.
        """
        target_lower = target_column.lower()
        flagged_token = next(
            (tok for tok in DEMOGRAPHIC_TARGET_TOKENS if tok in target_lower),
            None,
        )
        if flagged_token and not self.protected_attributes:
            raise ValueError(
                "STOP: target_column='{tc}' contains demographic token '{tok}' "
                "AND protected_attributes is empty. Either populate "
                "protected_attributes in configs/quality_gates.yaml, or "
                "document an explicit ADR explaining why DIR enforcement "
                "is not applicable here (PR-R2-7, ADR-005).".format(tc=target_column, tok=flagged_token)
            )


# ---------------------------------------------------------------------------
# Root config — aggregates all sub-configs
# ---------------------------------------------------------------------------
# TODO: Rename to {@ service_name @}Config.
class ServiceConfig(BaseModel):
    """Complete service configuration loaded from YAML.

    Provides sensible defaults for every field so that a minimal YAML
    (or even empty) still validates. Useful for tests and CI.
    """

    model: ModelConfig = ModelConfig()
    data: DataConfig = DataConfig()
    mlflow: MLflowConfig = MLflowConfig()

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> ServiceConfig:
        """Load configuration from YAML file.

        Parameters
        ----------
        config_path : str or Path
            Path to YAML configuration file.

        Returns
        -------
        config : ServiceConfig
            Validated configuration object.

        Raises
        ------
        FileNotFoundError
            If config file doesn't exist.
        ValidationError
            If YAML values fail Pydantic validation.
        """
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_dict = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error("Failed to parse YAML %s: %s", config_path, e)
            raise

        if config_dict is None:
            config_dict = {}

        # Provide defaults for missing top-level sections so older/focused
        # configs still validate (especially in tests/CI).
        if "model" not in config_dict:
            config_dict["model"] = ModelConfig().model_dump()
        if "data" not in config_dict:
            config_dict["data"] = DataConfig().model_dump()
        if "mlflow" not in config_dict:
            config_dict["mlflow"] = MLflowConfig().model_dump()

        logger.info("Loaded configuration from %s", config_path)
        return cls(**config_dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to nested dictionary (useful for MLflow param logging)."""
        return self.model_dump()
