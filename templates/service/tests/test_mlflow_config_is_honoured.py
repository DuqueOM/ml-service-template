"""Contract — the configured MLflow tracking URI is the one MLflow is told to use.

Why this exists
---------------
``MLflowConfig`` declared ``tracking_uri``, ``experiment_name`` and
``enabled``. All three were loaded from ``configs/config.yaml``, validated by
pydantic, and **read by nothing**. ``Trainer._log_to_mlflow`` called
``mlflow.set_experiment()`` and never ``mlflow.set_tracking_uri()``, so every
run went wherever MLflow defaulted::

    config.yaml says:            'sqlite:///mlflow.db'
    config.mlflow.tracking_uri = 'sqlite:///mlflow.db'   <- parsed fine
    mlflow.get_tracking_uri()  = 'file:///.../mlruns'    <- MLflow never saw it

The ``staging`` and ``prod`` profiles name an in-cluster tracking server, so
those were decorative too. Retraining in CI worked only because the workflow
exports ``MLFLOW_TRACKING_URI``, which MLflow reads on its own — the one
channel that bypassed the config entirely.

No test caught it because no test asked the question. The MLflow tests that
existed exercised what ``_log_to_mlflow`` logged, never *where*.

Why this runs without mlflow installed
--------------------------------------
``requirements-dev.txt`` deliberately excludes mlflow and optuna (ADR-049):
they are training dependencies and this is the test lane. So the module under
test is imported with a **stub** ``mlflow`` in ``sys.modules``, which is
sufficient — and arguably better — for the assertion being made. The contract
is *"the code calls set_tracking_uri with the resolved value"*, and a stub
records that exactly, with no tracking store, no temp directory and no network.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _slug() -> str:
    src = SERVICE_ROOT / "src"
    packages = [d.name for d in src.iterdir() if d.is_dir() and not d.name.startswith("__")]
    assert len(packages) == 1, f"expected one package under src/, found {packages}"
    return packages[0]


class _Recorder:
    """A stand-in for the mlflow module that records what it was told."""

    def __init__(self) -> None:
        self.tracking_uri: str | None = None
        self.experiment: str | None = None
        self.started = 0
        self.log_model_kwargs: dict[str, Any] = {}

    # --- the surface _log_to_mlflow touches -------------------------------
    def set_tracking_uri(self, uri: str) -> None:
        self.tracking_uri = uri

    def set_experiment(self, name: str) -> None:
        self.experiment = name

    def start_run(self, *a: Any, **k: Any) -> Any:
        self.started += 1
        recorder = self

        class _Run:
            def __enter__(self) -> Any:
                return recorder

            def __exit__(self, *exc: Any) -> bool:
                return False

        return _Run()

    def log_params(self, *a: Any, **k: Any) -> None: ...
    def log_metrics(self, *a: Any, **k: Any) -> None: ...
    def log_metric(self, *a: Any, **k: Any) -> None: ...
    def log_artifact(self, *a: Any, **k: Any) -> None: ...
    def set_tag(self, *a: Any, **k: Any) -> None: ...
    def set_tags(self, *a: Any, **k: Any) -> None: ...

    # --- what log_model was called with ----------------------------------
    def record_log_model(self, *a: Any, **kwargs: Any) -> None:
        self.log_model_kwargs = kwargs


@pytest.fixture
def train_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    """`training.train`, imported with stubbed mlflow and optuna."""
    recorder = _Recorder()

    mlflow_stub = types.ModuleType("mlflow")
    for name in dir(recorder):
        if not name.startswith("_"):
            setattr(mlflow_stub, name, getattr(recorder, name))
    sklearn_stub = types.ModuleType("mlflow.sklearn")
    sklearn_stub.log_model = recorder.record_log_model  # type: ignore[attr-defined]
    mlflow_stub.sklearn = sklearn_stub  # type: ignore[attr-defined]
    optuna_stub = types.ModuleType("optuna")

    monkeypatch.setitem(sys.modules, "mlflow", mlflow_stub)
    monkeypatch.setitem(sys.modules, "mlflow.sklearn", sklearn_stub)
    monkeypatch.setitem(sys.modules, "optuna", optuna_stub)

    name = f"{_slug()}.training.train"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    module._recorder = recorder  # type: ignore[attr-defined]
    return module


def _trainer(module: Any, tmp_path: Path, **mlflow_kwargs: Any) -> Any:
    """A Trainer with the EDA gate and quality gates satisfied cheaply.

    Constructed through `object.__new__` on purpose: `__init__` loads
    quality_gates.yaml and enforces the EDA artefact gate, neither of which
    this contract is about. What is under test is one method.
    """
    config_module = importlib.import_module(f"{_slug()}.config")
    trainer = object.__new__(module.Trainer)
    trainer.mlflow_config = config_module.MLflowConfig(**mlflow_kwargs)
    trainer.output_dir = tmp_path
    return trainer


def _log(module: Any, trainer: Any, tmp_path: Path) -> None:
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"")
    (tmp_path / "model_metadata.json").write_text("{}")
    module.Trainer._log_to_mlflow(trainer, object(), {"roc_auc": 0.9}, {"n_estimators": 1}, artifact)


def test_the_configured_uri_reaches_mlflow(train_module: Any, tmp_path: Path, monkeypatch) -> None:
    """The assertion the defect would have failed."""
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    trainer = _trainer(train_module, tmp_path, tracking_uri="sqlite:///mlflow.db")
    _log(train_module, trainer, tmp_path)

    recorder = train_module._recorder
    assert recorder.tracking_uri == "sqlite:///mlflow.db", (
        "the configured tracking URI never reached MLflow. This is the original defect: "
        "set_experiment was called and set_tracking_uri was not, so the config was parsed, "
        f"validated and ignored. Recorded: {recorder.tracking_uri!r}"
    )


def test_the_environment_overrides_the_file(train_module: Any, tmp_path: Path, monkeypatch) -> None:
    """MLFLOW_TRACKING_URI wins — the deploy chain sets it from a secret.

    A config file committed to the repository must not be able to redirect a
    production run's tracking somewhere else.
    """
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.internal:5000")
    trainer = _trainer(train_module, tmp_path, tracking_uri="sqlite:///mlflow.db")
    _log(train_module, trainer, tmp_path)

    assert train_module._recorder.tracking_uri == "http://mlflow.internal:5000"


def test_the_configured_experiment_name_is_used(train_module: Any, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    trainer = _trainer(train_module, tmp_path, experiment_name="Explicitly-Named")
    _log(train_module, trainer, tmp_path)

    assert train_module._recorder.experiment == "Explicitly-Named", (
        "the experiment name came from somewhere other than the config. It used to come "
        "from a module global that `main()` mutated from inside `if __name__ == '__main__'`."
    )


def test_disabling_mlflow_starts_no_run(train_module: Any, tmp_path: Path, monkeypatch) -> None:
    """`enabled: false` was declared and, like the rest of the block, ignored."""
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    trainer = _trainer(train_module, tmp_path, enabled=False)
    _log(train_module, trainer, tmp_path)

    recorder = train_module._recorder
    assert recorder.started == 0, "mlflow.enabled=false still started a run"
    assert recorder.tracking_uri is None, "mlflow.enabled=false still contacted a tracking store"


def test_precedence_is_stated_in_one_place() -> None:
    """The resolver is testable without a Trainer, a stub or a pipeline.

    Precedence lives on the config model rather than at the call site so it can
    be checked here, in three lines, instead of only by running training.
    """
    config_module = importlib.import_module(f"{_slug()}.config")
    cfg = config_module.MLflowConfig(tracking_uri="sqlite:///from-file.db")

    import os

    os.environ.pop("MLFLOW_TRACKING_URI", None)
    assert cfg.resolve_tracking_uri() == "sqlite:///from-file.db"
    os.environ["MLFLOW_TRACKING_URI"] = "http://from-env:5000"
    try:
        assert cfg.resolve_tracking_uri() == "http://from-env:5000"
    finally:
        os.environ.pop("MLFLOW_TRACKING_URI", None)


# ---------------------------------------------------------------------------
# MLflow 3.x serialisation (ADR-047 amendment)
# ---------------------------------------------------------------------------
# 3.x serialises sklearn models with skops, which REFUSES types it does not
# recognise — and the template's own ColumnTransformer produces one:
# `sklearn.compose._column_transformer._RemainderColsList`, from its
# `remainder`. The default path fails with "The saved sklearn model references
# untrusted types", at the END of a training run.
#
# ADR-047 originally reported the round-trip intact. It was, for the Pipeline
# it probed — one with no ColumnTransformer. This is the contract that stops
# that measurement gap from reopening.


def test_log_model_declares_the_skops_format_and_derives_its_trusted_types(
    train_module: Any, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    trainer = _trainer(train_module, tmp_path)
    _log(train_module, trainer, tmp_path)

    kwargs = train_module._recorder.log_model_kwargs
    assert kwargs, "log_model was never called"
    assert kwargs.get("serialization_format") == "skops", (
        "the serialisation format must be stated, not inherited. pickle and cloudpickle "
        "both work and MLflow warns they execute arbitrary code on load; skops is the "
        f"deliberate choice. Got: {kwargs.get('serialization_format')!r}"
    )
    assert "skops_trusted_types" in kwargs, (
        "skops_trusted_types is not optional for this pipeline. Without it, "
        "mlflow.sklearn.log_model raises on sklearn.compose._column_transformer."
        "_RemainderColsList and the training run fails after all its work."
    )
    assert isinstance(kwargs["skops_trusted_types"], list), "trusted types must be a list"
    assert kwargs.get("name") == "model", "3.x deprecated artifact_path in favour of name"


def test_the_trusted_types_are_derived_not_hardcoded() -> None:
    """A fixed list would go stale the first time an adopter edits model.py.

    The template tells them to. So the assertion is about *how* the list is
    produced, not what it contains today: the helper must interrogate the
    object it is given, and must not carry the type name as data.

    Docstrings and comments are excluded — naming the type in prose is how the
    reason gets recorded, and that is the opposite of hardcoding it.
    """
    import ast

    source = (SERVICE_ROOT / "src" / _slug() / "training" / "train.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_skops_trusted_types"),
        None,
    )
    assert helper is not None, "the derivation helper is gone"

    body = helper.body[1:] if ast.get_docstring(helper) else helper.body
    executable = "\n".join(ast.unparse(node) for node in body)

    assert "get_untrusted_types" in executable, (
        "the trusted types are no longer derived from the fitted model. A hardcoded list "
        "is wrong for any adopter who edited their preprocessor, and wrong at the end of "
        "a training run rather than at its start."
    )
    assert "_RemainderColsList" not in executable, (
        "the helper carries a specific sklearn internal type as DATA. That is the "
        "hardcoding this test exists to prevent — name it in a comment, never in code."
    )


def test_the_derivation_never_fails_training(train_module: Any) -> None:
    """A probe that cannot run must return nothing, not raise.

    If skops cannot be introspected the correct outcome is MLflow's own
    refusal, which names the offending type, rather than a crash in the
    helper — or worse, a blanket trust.
    """

    class Unserialisable:
        def __reduce__(self):  # noqa: ANN204 - deliberately hostile
            raise RuntimeError("cannot be serialised")

    assert train_module._skops_trusted_types(Unserialisable()) == []
