# Progression — where to start and where to go next

> **Closes external-feedback gap 7.3 (May 2026).** The template
> surfaces ML + K8s + Terraform + CI/CD + security + agentic system
> at once. This page is a layered "where to start" map so adopters
> do not try to absorb everything on day 1.

This is a **navigation document**, not a tutorial. Each stage points
to the existing docs that deliver it; the value is the ordering and
the "what's expected to work" checklist at the end of each stage.

Everything here respects ADR-001 scope: no tutorial track, no video
walkthroughs, no guided UI. Those are IDP features and are explicitly
deferred.

---

## Stage 1 — Day 1 (30–60 min) : taste the working example

**Goal**: confirm the minimal path works end-to-end on your laptop.

**Run**:

```bash
git clone https://github.com/DuqueOM/ml-service-template.git
cd ml-service-template
cd examples/minimal
pip install -r requirements.txt
python train.py
uvicorn serve:app --port 8000 &
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"amount": 150.0, "hour": 2, "is_foreign": true, "merchant_risk": 0.8, "distance_from_home": 45.0}'
```

**What's expected to work**:

- `train.py` exits 0 and writes `model.joblib`.
- `/predict` returns JSON with `score`, `prediction_class`, `latency_ms`.
- `/predict?explain=true` returns SHAP values per feature.
- `pytest test_service.py -v` is green.

**Reference**: `QUICK_START.md` § Track A · `examples/minimal/README.md`

---

## Stage 2 — Day 2–3 (2–4 h) : scaffold YOUR service locally

**Goal**: use `copier copy` to generate a full-shape service, run
its unit tests + contract tests on your laptop. No cluster yet.

**Run**:

```bash
copier copy --vcs-ref=v0.27.0 https://github.com/DuqueOM/ml-service-template.git ChurnPredictor
cd ChurnPredictor
# Option A: uv (recommended, 10× faster)
uv sync
# Option B: pip (compatible)
make install
make test            # unit + integration
make contract-test   # schema + policy contracts
```

> **Why `--vcs-ref`?** Without it Copier resolves to the highest-sorting tag,
> and this repo carries frozen `v1.0.0`–`v1.12.0` audit snapshots (ADR-014)
> alongside the active `v0.x` line. `v1.12.0` sorts above `v0.26.0`, so the
> bare command silently scaffolds an April 2026 snapshot. Always pin the
> active version.

**What's expected to work**:

- `make test` green locally.
- `tests/integration/test_train_serve_drift_e2e.py` passes (train →
  serve → drift, the end-to-end path wired in v0.15.1).
- `tests/contract/` passes (D-01..D-27 rules enforced on scaffolded
  output).

**What is explicitly NOT expected yet**: deploying to a cluster,
publishing images, running MLflow in prod mode. Those are Stage 3+.

**Reference**: `QUICK_START.md` § Track B · `docs/ADOPTION.md`
§"Non-agentic on-ramp"

---

## Stage 3 — Week 2 (1–2 days) : deploy to a dev Kubernetes

**Goal**: get the scaffolded service onto a dev cluster (your choice
of kind / minikube / GKE / EKS), with the base Kustomize overlay.

**Scope inclusions**:

- Image build + push to a registry you control.
- `kustomize build k8s/overlays/<cloud>-dev/ | kubectl apply -f -`.
- Liveness / readiness / `/ready` traffic gating.
- Prometheus metrics scraping (local Prometheus or Grafana Cloud
  with the dashboards from `templates/service/monitoring/grafana/`).

**Scope exclusions at this stage**:

- Cosign verification (warn mode in dev, enforce in prod — that's
  Stage 4).
- Kyverno admission (cluster-level policy, Stage 5).
- Argo Rollouts (Stage 5+, see
  `docs/runbooks/progressive-delivery.md`).

**Reference**: `docs/runbooks/deploy-gke.md` OR `deploy-aws.md` —
dev overlay sections only.

---

## Stage 4 — Month 1 (1 week) : the production overlay

**Goal**: move the scaffolded service to a production overlay with
all security invariants on.

**Checklist (each one has a corresponding ADR + runbook)**:

- `MODEL_SIGNATURE_VERIFY=enforce` — cosign verify-blob of the
  model blob at init time (v0.15.1).
- Image pinned to a `@sha256:...` digest (ADR-024 HIGH-6 / D-26).
- Retrain workflow emits audit entry on every run (ADR-024 HIGH-7).
- SLO PrometheusRule wired (ADR-024 CRIT-1).
- Default-deny egress NetworkPolicy in overlay (ADR-024 MED-11).
- Secrets resolved via `common_utils.secrets.get_secret` (D-17/D-18).

**Reference**: `docs/environment-promotion.md` (dev → staging → prod
promotion contract) · `docs/audit/feedback-may-2026-triage.md`
(full list of what ships, what's disclosed, what's out of scope).

---

## Stage 5 — Month 2 (2 weeks) : closed-loop + progressive delivery

**Goal**: wire the drift detection + retraining loop AND (optionally)
switch to Argo Rollouts for metric-gated deploys.

**Wire order**:

1. Prediction logger writing to your storage backend
   (`common_utils.prediction_logger.ParquetBackend` or BigQuery).
2. Ground-truth ingestion SLA
   (`docs/runbooks/closed-loop-sla.md` — PR-5 of this triage).
3. Drift CronJob alert thresholds tuned from your own PSI baseline
   (see `docs/decisions/ADR-022-psi-thresholds.md`).
4. Retrain workflow (`templates/service/.github/workflows/retrain-service.yml`) pointed
   at your MLflow + model registry.
5. OPTIONAL: swap Deployment → Rollout, wire AnalysisTemplate.
   See `docs/runbooks/progressive-delivery.md`.

**Reference**: `ADR-008-champion-challenger.md` · `ADR-018` · ADR-019

---

## What this page is NOT

- It is NOT a replacement for the per-topic runbooks.
- It does NOT define what "production-ready" means for your
  organization — that is a legal + org decision.
- It does NOT promise that Stage 5 will be complete in 2 weeks for
  every adopter. Data shape, compliance, and cluster ops vary.

## If you get stuck

- First: check `RUNBOOK.md` for common ops failures.
- If a local command errors: the `scripts/ci_collect_context.py`
  output shipped in CI logs is the same tool you can run locally.
- If a claim in this doc does not match reality: open an issue
  tagged `docs`. The progression is a contract with adopters.
