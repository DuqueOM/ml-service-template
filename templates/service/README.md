# {@ service_name @}

> {One sentence describing the business problem solved}

## Quick Start

```bash
# Option A: uv (recommended, 10x faster) — make install-uv
uv sync

# Option B: pip (compatible, e.g. air-gapped environments)
# Three lanes (ADR-049). A training env is a superset of a serving env;
# the served image gets requirements.txt and nothing more.
pip install -r requirements-train.txt   # runtime + mlflow/optuna — needed to train
# pip install -r requirements.txt      # serving only, what the image installs
# pip install -r requirements-dev.txt  # runtime + pytest/httpx/locust/linters

python -m src.{@ service_slug @}.training.train --data data/raw/dataset.csv
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Coming from a Cookiecutter Data Science background? See
[docs/CCDS_MAPPING.md](docs/CCDS_MAPPING.md) for how this layout maps to the
`data/ notebooks/ models/ references/` vocabulary (ADR-034).

## Model

- **Architecture**: {Model type — see ADR-NNN}
- **Primary Metric**: {metric} = {value}
- **Secondary Metric**: {metric} = {value}
- **Fairness (DIR)**: {value} per {protected attributes}
- **Training Date**: {YYYY-MM-DD}

## Endpoints

| Method | Path | Description |
| -------- | ------ | ------------- |
| POST | `/predict` | Main prediction endpoint (async, ThreadPoolExecutor) |
| POST | `/predict?explain=true` | Prediction with SHAP explanation (D-04) |
| POST | `/predict_batch` | Batch prediction for multiple inputs |
| GET | `/health` | Liveness probe — 200 while process alive (D-23) |
| GET | `/ready` | Readiness probe — 503 until warm-up complete (D-23) |
| GET | `/model/info` | Model metadata (version, SHA256, loaded_at) |
| POST | `/model/reload` | Hot-reload model without pod restart |
| GET | `/metrics` | Prometheus metrics |

### Example Request

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "feature_a": 42.0,
    "feature_b": 50000.0,
    "feature_c": "category_A"
  }'
```

### Example Response

```json
{
  "prediction_score": 0.7234,
  "risk_level": "HIGH",
  "model_version": "v1.0.0"
}
```

## Serving Latency (Measured)

| Cloud | Instance | P50 (idle) | P95 (idle) | P50 (100u) |
| ------- | ---------- | ----------- | ----------- | ----------- |
| GCP | {type} | {X}ms | {Y}ms | {X}ms |
| AWS | {type} | {X}ms | {Y}ms | {X}ms |

## Drift Detection

- **Metric**: PSI with quantile-based bins
- **Schedule**: Daily at 02:00 UTC via K8s CronJob
- **Thresholds**: See `src/{@ service_slug @}/monitoring/drift_detection.py`

## Deploy

```bash
# Manual application is for dev iteration / emergency only.
# Production deploys go via the dev → staging → prod chain in CI (ADR-011).

# GCP — substitute env: dev | staging | prod
kubectl apply -k k8s/overlays/gcp-dev/

# AWS — substitute env: dev | staging | prod
kubectl apply -k k8s/overlays/aws-dev/
```

## Architecture Decisions

- ADR-NNN: {Model selection rationale}
- ADR-NNN: {Any service-specific decisions}

## Memory Footprint

```text
Model + dependencies: ~{N}Mi
Pod request: {X}Mi
Pod limit: {Y}Mi
```
