# ADR-050 — Configuration a generated service declares must be read, or say it is not

- **Status**: Accepted
- **Date**: 2026-09-10
- **Deciders**: template maintainer
- **Related**: ADR-047 (MLflow 3.x migration), ADR-033 (local-first profiles),
  ADR-031 (documentation coherence), ADR-046 (the dated-baseline idiom this borrows)

## Context

`MLflowConfig` declared `tracking_uri`, `experiment_name` and `enabled`. All
three were loaded from `configs/config.yaml`, validated by pydantic, and **read
by nothing**. `Trainer._log_to_mlflow` called `mlflow.set_experiment()` and
never `mlflow.set_tracking_uri()`, so MLflow fell through to its own default.

Measured, before the fix:

```text
config.yaml says:            'sqlite:///mlflow.db'
config.mlflow.tracking_uri = 'sqlite:///mlflow.db'   <- parsed fine
mlflow.get_tracking_uri()  = 'file:///…/mlruns'      <- MLflow never saw it
```

`cli.py` said it out loud in a comment on the line that did it:

```python
ServiceConfig.from_yaml(args.config)  # Validate config exists and is parseable
```

It loaded the whole config to check it parsed, and discarded the object.

Two consequences worth naming:

- The `staging` and `prod` profiles point at
  `http://mlflow.mlflow-system.svc.cluster.local:5000`. That did nothing for
  training. Retraining in CI worked only because `retrain-service.yml` exports
  `MLFLOW_TRACKING_URI` from a secret — i.e. through the one channel MLflow
  reads on its own, bypassing the config entirely.
- ADR-047's migration plan step 2 was "the five file-store defaults →
  `sqlite:///mlflow.db`". Four of those five are values the trainer never
  consulted. **On its own that step would have changed nothing**, and the
  MLflow 3.x file-store rejection would have persisted through a migration
  that looked complete.

### The class is wider than MLflow

Fixing it surfaced the pattern. Of **51 declared fields across 10 config
models, 24 are read by nothing**, most of them settable in `configs/*.yaml`.
The worst is not MLflow:

| field | what actually decides it |
| --- | --- |
| `data.categorical_features` | `model.py`'s module-level `CATEGORICAL_FEATURES` constant |
| `data.numerical_features` | `model.py`'s `NUMERIC_FEATURES` constant |
| `data.drop_columns` | the `ColumnTransformer`'s `remainder="drop"` against an explicit list |
| `model.resampling_strategy` | nothing — `build_pipeline()` never constructs `ResampleClassifier` |
| `model.logistic_regression`, `model.random_forest` (+3 nested) | nothing — `build_pipeline()` hardcodes `GradientBoostingClassifier` |
| `ensemble.weights` | nothing — no ensemble is built from config |
| `advanced.{xgboost,lightgbm,neural_network}_params` | nothing — opt-in backends that do not ship |
| `quality_gates.promotion_threshold` | nothing — `promote_to_mlflow.py` implements no baseline comparison, so the auto-promotion delta it documents is not enforced. This one sits in the promotion-gate surface (D-26). |
| `quality_gates.latency_sla_ms` | nothing — its own description claimed *"Read by the load-test target"* and `tests/load_test.py` does not read it. The claim is corrected. |

`config.yaml` marks `categorical_features` *"TODO: Replace with your actual
column names"*, and `model.py` marks `CATEGORICAL_FEATURES` *"TODO: List your
categorical feature column names"*. **Two places to declare one thing, and only
the Python one is consulted.** Feature selection is the first thing an adopter
customises, so this is the field most likely to be edited and least likely to
take effect.

## Decision

**1. The MLflow block is read.** `MLflowConfig.resolve_tracking_uri()` states
the precedence once — `MLFLOW_TRACKING_URI` first, then the config file — and
`Trainer` takes an `MLflowConfig` rather than reading a module global.
`_log_to_mlflow` calls `set_tracking_uri`, honours `enabled`, and logs the URI
it resolved at INFO, because *"where did my run go"* is the first question when
a run goes missing and the answer used to be unobtainable from the output.

Environment wins over the file deliberately: `MLFLOW_TRACKING_URI` is MLflow's
own variable and the deploy chain already sets it from a secret. A file
committed to the repository must not be able to redirect a production run's
tracking somewhere else.

**2. Every declared field is either read, or declared unwired with a reason.**
`scripts/check_config_is_read.py` (gate 18) reads the config models' fields
from the AST, collects every attribute access in the payload (tests excluded —
a field read only by a test is not read by the service), and fails on any field
that is neither consumed nor listed in its `UNWIRED` table. `test_gate_scope_ratchet.py` pins
the count, so the number of unwired fields can shrink but not grow.

**3. The fields that are not wired say so where the adopter edits them.**
`configs/config.yaml` carries a `⚠ NOT YET WIRED (ADR-050)` banner on each
affected block, naming what actually decides the value instead.

## Why not simply delete the unwired fields

Because several are real product gaps, not dead weight. The pipeline **should**
honour `data.categorical_features` — hardcoding the column lists in `model.py`
is the defect, and the config is the correct interface. Deleting the
declaration would resolve the inconsistency in the wrong direction: it removes
the promise instead of keeping it, and it would make the template quietly worse
at the thing an adopter needs most.

So the fields stay, the gate counts them, and the banner tells the truth in the
meantime. Wiring one up is a one-line deletion here and a ratchet that moves
down.

## Two things the first version of this gate got wrong

Both were found by running it against `main`, where the defect is present,
rather than trusting it on a fixed tree.

**It grepped for the field name.** `promote_to_mlflow.py` has a local variable
called `tracking_uri`, so the field read as consumed while MLflow was never
told about it — the gate reported the identical "38 read, 13 unwired" on the
broken tree. Detection is now attribute access via AST:
`mlflow.set_tracking_uri(...)` does not match `.tracking_uri`.

**It counted tests as consumers.** The regression test for the MLflow fix gives
its stub a `self.tracking_uri` attribute, and that alone made the production
field look consumed again. A field read only by a test is not read by the
service — and excluding tests is what surfaced the last two entries in the
table above, both in the quality-gates surface.

Stated limitations, since a gate that overclaims is the thing this whole line
of work is about: attribute names are matched globally rather than per model,
so a field sharing a name with any other object's attribute can hide
(`MLflowConfig.enabled` was in exactly that position), and dictionary-style
access is invisible. Every field it flags is really unread; it can miss one.

## Why a ratchet rather than fixing all twenty-four now

Wiring feature selection, resampling and model choice through config is
behaviour change for every adopter, with its own test surface and its own
decisions (what happens when `categorical_features` names a column the data
does not have?). Twenty-four of those in one diff is not
reviewable. The ratchet makes the number visible and unable to grow, which is
the property that was missing — nobody knew it was twenty-four.

## Consequences

- One more gate (eighteen), offline and sub-second, with a scope floor and its
  own negative-control suite.
- `Trainer(...)` gains an optional `mlflow_config` parameter and keeps working
  without it, so direct callers and existing tests are unaffected.
- `python -m …training.train` gains `--config` (default `configs/config.yaml`);
  a missing file is not an error, because `quality_gates.yaml` is the config
  training genuinely requires and a service that has not written a
  `config.yaml` yet should still be able to train.
- `--experiment` now overrides the config value rather than mutating a module
  global from inside `if __name__ == "__main__"`, which worked only because
  `_log_to_mlflow` happened to read that global late.
- **ADR-047's plan is corrected by this.** Changing the file-store defaults is
  necessary but was never sufficient; the config had to be reachable first.
  That decision is now able to proceed on its own terms.
