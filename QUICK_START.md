# Quick Start — ML-MLOps Production Template

**From clone to first model served in 10 minutes.**

Two clearly labeled tracks below. Pick ONE — do not mix them on a first read.

- **Track A** — 5-minute taste: runs the `examples/minimal/` demo. Confirms your laptop is ready. No scaffolding, no
  cluster, no Docker.
- **Track B** — 10-minute scaffold: generates a full-shape service via `copier copy` and runs its test suite locally.
  Still no cluster.

If you need the longer "Day 1 to Month 2" arc (cluster deploys, production overlay, closed-loop retraining), read
[`docs/PROGRESSION.md`](docs/PROGRESSION.md) AFTER finishing Track A or B. For a narrated walk-through from notebook to
production, see [`docs/TUTORIAL.md`](docs/TUTORIAL.md).

---

## Prerequisites

| Component | Version | Check |
| ----------- | --------- | ------- |
| **Python** | 3.11+ | `python --version` |
| **Docker** | 20.10+ | `docker --version` |
| **Make** | Any | `make --version` |
| **Copier** | 9.0+ | `pip install copier` |

---

## Track A — Try the Working Example (5 min)

Run the fraud detection demo — no setup required beyond Python.

```bash
git clone https://github.com/DuqueOM/ml-service-template.git
cd ml-service-template

# Install and run end-to-end
make demo-minimal
```

Or step by step:

```bash
cd examples/minimal
pip install -r requirements.txt

# Train (synthetic data + Pandera validation + quality gates)
python train.py

# Serve (async inference + SHAP + Prometheus metrics)
uvicorn serve:app --host 0.0.0.0 --port 8000

# Predict (in another terminal)
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"amount": 150.0, "hour": 2, "is_foreign": true, "merchant_risk": 0.8, "distance_from_home": 45.0}'

# With SHAP explanation
curl "http://localhost:8000/predict?explain=true" \
  -X POST -H "Content-Type: application/json" \
  -d '{"amount": 9500.0, "hour": 3, "is_foreign": true, "merchant_risk": 0.9, "distance_from_home": 200.0}'

# Regression tests (leakage, SHAP consistency, latency, fairness)
pytest test_service.py -v

# Drift detection
python drift_check.py
```

---

## Track B — Scaffold Your Own Service (10 min)

```bash
git clone https://github.com/DuqueOM/ml-service-template.git
cd ml-service-template
pip install copier

# Scaffold a new service (Copier renders templates/service/ with your answers)
copier copy --vcs-ref=v0.27.0 https://github.com/DuqueOM/ml-service-template.git ChurnPredictor

# Or via the thin wrapper script
# ./templates/scripts/new-service.sh ChurnPredictor churn_predictor

# Or via Make
# make new-service NAME=ChurnPredictor SLUG=churn_predictor
```

> **Why `--vcs-ref`?** Without it Copier resolves to the highest-sorting tag,
> and this repo carries frozen `v1.0.0`–`v1.12.0` audit snapshots (ADR-014)
> alongside the active `v0.x` line. `v1.12.0` sorts above `v0.26.0`, so the
> bare command silently scaffolds an April 2026 snapshot. Always pin the
> active version.

This creates a complete service directory:

```text
ChurnPredictor/
├── app/                    # FastAPI serving layer
├── src/churn_predictor/    # Training, features, monitoring
├── tests/                  # Unit, integration, explainer, load
├── k8s/                    # Kubernetes manifests (base + overlays)
├── infra/                  # Terraform GCP + AWS
├── monitoring/             # Grafana + Prometheus
├── .github/workflows/      # CI/CD pipelines
├── Dockerfile              # Multi-stage, non-root
├── Makefile                # train, test, serve, build, deploy
└── docker-compose.demo.yml # Local demo stack
```

The generated FastAPI service is not a blank stub. It already includes
the required `/predict`, `/predict_batch`, `/health`, `/ready`,
`/metrics`, `/model/info`, and `/model/reload` surface, plus async
inference, feature-parity hooks, structured errors, auth hooks, and
prediction logging. The contract is summarized in
[`docs/FASTAPI_TEMPLATE_CONTRACT.md`](docs/FASTAPI_TEMPLATE_CONTRACT.md).

**Next steps after scaffolding:**

```bash
cd ChurnPredictor

# 1. Define your public API schema
#    Edit app/schemas.py

# 2. Define your data contract
#    Edit src/churn_predictor/schemas.py

# 3. Define your features
#    Edit src/churn_predictor/training/features.py

# 4. Define your model pipeline
#    Edit src/churn_predictor/training/model.py

# 5. Install, train, serve, and verify the FastAPI contract
# Option A: uv (recommended, 10× faster)
uv sync
# Option B: pip (compatible)
pip install -r requirements.txt
make train DATA=data/raw/your-dataset.csv
pytest tests/test_fastapi_template_contract.py -v
make serve
```

---

## Option C: Full Stack with MLflow (15 min)

Requires Docker.

```bash
cd ml-service-template
pip install copier

# Start MLflow + Pushgateway
docker compose -f templates/service/infra/docker-compose.mlflow.yml up -d

# Scaffold and run your service
./templates/scripts/new-service.sh MyService my_service
cd MyService
export MLFLOW_TRACKING_URI=http://localhost:5000
pip install -r requirements.txt
make train
make serve
```

**Access points:**

| Service | URL |
| --------- | ----- |
| Your API | <http://localhost:8000/docs> |
| MLflow UI | <http://localhost:5000> |

---

## Agentic Workflows

If using Devin, Claude Code, Cursor, or Codex, the template includes pre-configured rules, skills, and workflows:

```text
# In your AI assistant:
/new-service       # Scaffold a new ML service
/scaffold-update   # Pull template improvements into an existing service
/onboard           # Generate adopter context file (interview + validate)
/stack-switch      # Switch stack profile (local, staging, prod)
/retrain           # Retrain with quality gates
/drift-check       # Run PSI drift analysis
/release           # Full multi-cloud release
/incident          # Incident response
```

---

## Troubleshooting

| Issue | Solution |
| ------- | ---------- |
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| Port 8000 in use | `lsof -i :8000` then `kill -9 <PID>` |
| Model not found | Run `make train` first |
| Docker OOM | Increase Docker memory to 8GB+ |

---

## Protect your fork (one-time, CONSULT-class)

If you have just forked or cloned this template into your own GitHub
account / org, apply the same branch + tag protection the upstream uses:

```bash
make setup-github-preview   # show the 2 ruleset payloads without applying
make setup-github            # apply main-branch-baseline + tag-immutability-v
make setup-github-check      # verify both rulesets are active
```

This is **opt-in** because it mutates the SCM settings of your repo
(force-push, deletion, required checks, bypass actors). The CONSULT
posture means: read what it does first, then run it deliberately. Full
rationale: `docs/decisions/ADR-026-branch-protection.md`. Single-source
of-truth for the exact configuration: `docs/governance/branch-protection.md`.

Requirements:

- `gh` CLI authenticated against your fork (`gh auth login`)
- A token with `repo` admin scope on your fork
- Optional: `jq` for pretty-printed dry-run output (the script falls back
  to `python3 -m json.tool` if `jq` is absent)

## Next Steps

- **[README.md](README.md)** — Full documentation, architecture, invariants
- **[docs/TUTORIAL.md](docs/TUTORIAL.md)** — Narrated notebook-to-production walk-through
- **[RUNBOOK.md](RUNBOOK.md)** — Template operations reference
- **[CHANGELOG.md](CHANGELOG.md)** — Release history
- **[examples/minimal/](examples/minimal/)** — Working fraud detection demo
