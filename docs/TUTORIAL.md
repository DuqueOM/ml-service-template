# Tutorial — From Notebook to Production

> A narrated walk-through that ties 8 key anti-patterns to the concrete
> failure each prevents. Designed for practitioners evaluating the
> template for the first time.

## Prerequisites

- Python 3.11+
- `pip install copier` (or `uv pip install copier`)
- No cluster, no cloud account required (local profile)

## 1. Scaffold a service

```bash
copier copy --vcs-ref=v0.27.0 https://github.com/DuqueOM/ml-service-template.git my_service
cd my_service
```

> **Why `--vcs-ref`?** Without it Copier resolves to the highest-sorting tag,
> and this repo carries frozen `v1.0.0`–`v1.12.0` audit snapshots (ADR-014)
> alongside the active `v0.x` line. `v1.12.0` sorts above `v0.26.0`, so the
> bare command silently scaffolds an April 2026 snapshot. Always pin the
> active version.

When prompted, choose `profile: local`. This gives you a
zero-cloud-dependency on-ramp — no Docker, no K8s, no Terraform.

> **Why `profile: local`?** The local profile enforces D-35: it
> MUST NOT accept cloud credentials or target a cluster. This means
> you can experiment without accidentally deploying to a real
> environment. See ADR-033.

## 2. Explore the layout

If you come from a Cookiecutter Data Science (CCDS) background, read
`templates/service/docs/CCDS_MAPPING.md` for a mapping from CCDS vocabulary to this
template's production layout.

The key directories:

```text
data/raw/          → your dataset goes here
eda/               → structured EDA pipeline (6 phases)
src/<service>/     → training + serving source code
app/               → FastAPI API
models/            → trained artifacts
reports/           → metrics, drift, evaluation
k8s/               → Kubernetes manifests (staging/prod only)
```

## 3. Run EDA (the leakage gate)

```bash
cp your_data.csv data/raw/
pip install -r eda/requirements.txt
python -m eda.eda_pipeline --input data/raw/your_data.csv --target target_col
```

Review `eda/reports/04_leakage_audit.md`. It must show
`BLOCKED_FEATURES: []`.

> **What does this prevent?** D-13 (EDA on production data) and D-14
> (Pandera schemas disconnected from observed distributions). Without
> the leakage gate, a feature like `prediction_timestamp` leaks into
> training, your model scores 0.99 on the holdout, and it fails
> silently in production because the timestamp distribution shifts
> every day.

## 4. Train a model

```bash
make train DATA=data/raw/your_data.csv
```

This runs:

1. Pandera validation (`schemas.py`)
2. Feature engineering (`features.py` — consumes
   `eda/artifacts/feature_catalog.yaml`)
3. Model training (`train.py` — logs to MLflow, writes
   `models/model.joblib`)

> **What does this prevent?**
>
> - **D-05** (bare `>=` pinning): dependencies use `~=` so
>   `numpy 2.x` doesn't silently corrupt your joblib model.
> - **D-06** (suspiciously high metrics): if your AUC > 0.99, the
>   quality gate halts training and flags it for investigation.
> - **D-15** (missing baseline distributions): EDA phase 2 persists
>   `baseline_distributions.parquet`, which the drift CronJob needs
>   in production.

## 5. Serve the model

```bash
make serve
```

This starts `uvicorn` with a single worker. In production, HPA
provides horizontal scale — never `--workers N`.

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"feature_1": 1.0, "feature_2": 2.0}'
```

> **What does this prevent?**
>
> - **D-01** (`uvicorn --workers N` in K8s): causes CPU thrashing
>   and dilutes the HPA signal. The template enforces `--workers 1`
>   and uses `asyncio.run_in_executor()` for CPU-bound inference.
> - **D-03** (blocking `model.predict()` in async endpoint): the
>   template wraps inference in `ThreadPoolExecutor` so the event
>   loop never blocks.
> - **D-24** (SHAP in transformed space): the template computes SHAP
>   values in the original feature space via
>   `predict_proba_wrapper`, so explanations are interpretable.

## 6. Check drift

```bash
make drift-check
```

This runs PSI (Population Stability Index) against the baseline
distributions from EDA phase 2.

> **What does this prevent?**
>
> - **D-15** (missing baseline): without
>   `baseline_distributions.parquet`, drift detection silently
>   no-ops and you never know your model is degrading.
> - **D-32** (drift CronJob points at missing module): the contract
>   test `test_d32_drift_cronjob_python_path` ensures the CronJob
>   command resolves to a real file.

## 7. Run the local loop

```bash
make local-loop
```

This chains `train → serve → drift-check` in a single target. It's
the fastest way to validate the full local path works.

## 8. Switch to staging (when ready)

When you're ready to test on a cluster:

```bash
make switch-profile PROFILE=staging
```

This is a CONSULT-mode operation — the skill will show you what
changes and wait for your approval.

> **What does this prevent?** D-35 (local profile accepting cloud
> credentials). The `local` profile MUST NOT accept cloud creds. If
> you need cloud deps, switch to `staging` or `prod` — never inject
> them into `local`.

## Anti-patterns covered in this tutorial

| # | Anti-pattern | Where prevented | Failure if missing |
| --- | ------------- | ----------------- | ------------------- |
| D-01 | `uvicorn --workers N` in K8s | `Makefile` + contract test | CPU thrashing, HPA signal diluted |
| D-03 | Blocking `model.predict()` in async | `app/main.py` wrapper | Event loop blocks, latency spikes |
| D-05 | Bare `>=` dependency pinning | `requirements.txt` + `pyproject.toml` | `numpy 2.x` corrupts joblib models |
| D-06 | Suspiciously high metrics | Quality gate in `train.py` | Overfitting or leakage goes undetected |
| D-13 | EDA on production data | EDA sandbox + rule 11 | Production DB creds in notebooks |
| D-15 | Missing baseline distributions | EDA phase 2 artifact | Drift detection silently no-ops |
| D-24 | SHAP in transformed space | `predict_proba_wrapper` | Explanations are uninterpretable |
| D-35 | Local profile with cloud creds | Profile YAML + contract test | Accidental cloud deployment from local |

## Next steps

- Read `QUICK_START.md` for Track A (minimal example) and Track B
  (scaffolded service).
- Read `docs/PROGRESSION.md` for the Day 1 → Month 2 progression.
- Read `docs/ADOPTION.md` for the maturity matrix.
- Run `/onboard` to generate a context file for your organization.
