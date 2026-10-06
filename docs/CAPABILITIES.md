# Capabilities, in detail

What the template ships, area by area — the long form the [README](../README.md) summarises. Moved here from the
README when it adopted the shared [README standard](governance/readme-standard.md): the README now answers what this
is, how mature it is and how to start, and links here for the rest. Nothing was dropped in the move.

Maturity per capability, cloud and environment is rated in [ADOPTION.md](ADOPTION.md); the canonical anti-pattern
definitions and the operation matrix are in [AGENTS.md](../AGENTS.md).

## Core capabilities

### Serving and APIs

- Async FastAPI serving with `run_in_executor` for CPU-bound inference.
- Explicit FastAPI template contract for required endpoints, feature
  parity, auth, readiness, CORS, error envelope, metrics, and prediction
  logging: [`docs/FASTAPI_TEMPLATE_CONTRACT.md`](FASTAPI_TEMPLATE_CONTRACT.md).
- Single-worker pod model for correct HPA behavior.
- Request validation, contract versioning, snapshot-based API checks, and structured error envelopes.
- Model loading through init containers and shared volumes instead of baking models into images.
- Warm-up path for model readiness and SHAP explainer caching.

### Kubernetes and runtime

- Kustomize base plus an overlay for each cloud and environment (`gcp-dev`, `gcp-staging`, `gcp-prod`, `aws-dev`,
  `aws-staging`, `aws-prod`) and a `batch-only` overlay for scheduled scoring.
- CPU-only HPA, PodDisruptionBudget, NetworkPolicy, RBAC, non-root security context, and Pod Security Standards labels.
- Separate liveness, readiness, and startup probes.
- Digest-based deployment and immutable image flow.

### Infrastructure

- Terraform layouts separated by cloud and environment concerns.
- Remote state patterns for both GCP and AWS.
- Workload Identity and IRSA as the default runtime identity model.
- Example resource topology for buckets, registries, clusters, IAM, and observability prerequisites.

### CI/CD and controlled automation

- Build → scan → sign → attest → deploy → smoke-test promotion chain.
- Drift detection and retraining workflows as first-class operational paths.
- Audit trail written to JSONL and surfaced in GitHub Actions summaries.
- CI self-healing is roadmap: a failure classifier runs in shadow mode and writes nothing (see below).

### Data validation and ML quality

- Pandera-based contracts for data validation.
- Leakage checks, baseline distributions, reproducibility hooks, and configurable quality gates.
- Fairness checks, champion/challenger evaluation, and retraining evidence packages.
- Versioned artifacts and model promotion rules that are designed to fail closed.

### Observability and closed-loop monitoring

- Prometheus metrics, structured logs, Grafana dashboards, and alert rules.
- Prediction logging with `prediction_id` and `entity_id` as required primitives.
- Delayed ground-truth ingestion, sliced performance monitoring, heartbeat monitoring, and trend analysis.
- Metrics and alerts designed for incident response and governed promotion.

### Security and supply chain

- Secret scanning, vulnerability scanning, SBOM generation, Cosign signing, and attestation.
- Admission-policy-oriented deployment posture.
- Least-privilege identity patterns for cloud access.
- Clear separation of dev, staging, and production credentials and approval paths.

### Technology stack

| Layer | Technologies | Coverage |
| ------- | ------------- | ---------- |
| ML and training | Python 3.11+, scikit-learn, XGBoost, LightGBM, Optuna | baseline models, ensembles, hyperparameter tuning |
| Serving and API | FastAPI, Uvicorn, Pydantic | async inference, contract validation, structured responses |
| Explainability | SHAP | feature attribution in original feature space |
| Data validation | Pandera, pandas, DVC | schema contracts, dataset versioning, reproducible pipelines |
| Model registry | MLflow, joblib | experiment tracking, model registry, serialized artifacts |
| Containers | Docker, multi-stage builds | image build, non-root runtime, init-container model loading |
| Kubernetes runtime | Kubernetes, Kustomize, HPA, PDB, NetworkPolicy | deployment, autoscaling, resilience, network isolation |
| Infrastructure | Terraform, GKE, EKS, GCS, S3, Artifact Registry, ECR | cloud provisioning, remote state, multi-cloud environment separation |
| Observability | Prometheus, Grafana, Alertmanager, Evidently | metrics, dashboards, alerting, drift and performance monitoring |
| CI/CD and security | GitHub Actions, Trivy, Syft, Cosign, Kyverno, gitleaks | build, scan, sign, attest, policy enforcement, secret detection |

## Agentic system

The template treats agent behavior as an engineering surface, not a prompt configuration.

The governance pattern is now single-source:

- `AGENTS.md` is the behavioral authority.
- `agentic/` stores canonical rule, skill, and workflow bodies (ADR-027).
- `templates/config/agentic_manifest.yaml` declares which surfaces consume each asset.
- Every supported IDE or agent reads a **generated** adapter, never a hand-edited copy. Which tools are supported and
  what each receives is listed once, with counts the coherence gate reconciles, under
  [AGENTS.md § Multi-IDE Support](../AGENTS.md#multi-ide-support).

Run `make agentic-sync` after changing the manifest or canonical `agentic/` files, then `make validate-agentic` to prove
parity. Today the manifest exposes the same 19 rules + 27 skills + 20 workflows to Devin, Cursor, Claude, and
Codex. The project shorthand "18 rules" refers to the numbered policy set; on disk, rule 04 is split into serving and
training files, which is why the file count is 19.

### Static decision protocol

Every operation maps to one of three modes:

| Mode | Meaning | Examples |
| ------ | --------- | ---------- |
| `AUTO` | Safe to execute without waiting for approval | scaffolding, docs, tests, local training, lint, read-only inspection |
| `CONSULT` | Propose plan and rationale, then wait for approval | staging deploys, workflow changes with moderate blast radius, non-prod infra changes |
| `STOP` | Block and require explicit human governance | production infra changes, quality-gate override, secret rotation, destructive cloud actions |

### Dynamic escalation

The template supports live escalation based on risk signals including:

- severe drift
- active incident
- exhausted error budget
- recent rollback
- off-hours deployment
- suspicious quality signals
- detected credential pattern

Dynamic escalation only moves toward a safer mode. It never downgrades a risky action.

### Typed handoffs and auditability

- Inter-agent handoffs use typed dataclasses instead of ad-hoc dictionaries.
- Every meaningful operation produces an audit entry.
- Consulted or blocked operations can be surfaced as GitHub issues with evidence.

### Model routing

Which class of model handles which agentic task — a cheap router for triage, a stronger reviewer for diffs, frontier
models only for escalation, preview models never on a protected branch — is a policy in
[`templates/config/model_routing_policy.yaml`](../templates/config/model_routing_policy.yaml), explained in
[docs/agentic/model-routing.md](agentic/model-routing.md). A generated service never calls a language model; this
governs the maintenance lanes only.

See [AGENTS.md](../AGENTS.md) for the canonical operation matrix and invariant catalog.

## Anti-patterns encoded

The template encodes and audits 38 production anti-patterns across serving, training, Kubernetes, Terraform, security,
observability, and delivery.

| ID | Anti-pattern | Corrective action |
| ---- | -------------- | ------------------- |
| D-01 | `uvicorn --workers N` in Kubernetes | Use one worker per pod and move CPU-bound inference into `ThreadPoolExecutor`. |
| D-02 | Memory as an HPA metric for ML pods | Use CPU-only HPA so scale-down remains meaningful. |
| D-03 | `model.predict()` called directly in an async endpoint | Wrap inference with `run_in_executor`. |
| D-04 | `shap.TreeExplainer` with ensemble or pipeline models | Use `KernelExplainer` with a stable prediction wrapper. |
| D-05 | Exact `==` version pinning for ML dependencies | Use compatible release pinning (`~=`) and automate updates through Dependabot. |
| D-06 | Unrealistically high primary metric | Treat as a leakage investigation, not as a promotion win. |
| D-07 | SHAP background sample contains only one class | Replace with a representative background sample. |
| D-08 | PSI computed with uniform bins | Use quantile-based bins derived from the reference distribution. |
| D-09 | Drift detection without heartbeat alerting | Add heartbeat alerting for broken or stalled CronJobs. |
| D-10 | `terraform.tfstate` committed to Git | Move state to remote storage and rotate exposed credentials immediately. |
| D-11 | Model artifacts baked into the Docker image | Download models at runtime through init containers and shared volumes. |
| D-12 | No quality gates before promotion | Enforce metrics, fairness, leakage, and integrity gates before deploy. |
| D-13 | EDA executed directly on production data | Work from an isolated copy under `data/raw/` and keep EDA out of prod paths. |
| D-14 | Pandera schema without observed bounds from EDA | Add observed ranges and constraints derived from exploratory analysis. |
| D-15 | Baseline distributions not persisted for drift | Save and version baseline distributions for drift consumers. |
| D-16 | Feature engineering without rationale | Document feature proposals and tie them to EDA evidence. |
| D-17 | Hardcoded credentials in code or config | Use secret manager integrations through shared utilities. |
| D-18 | Static AWS keys or GCP JSON keys in production | Use IRSA on AWS and Workload Identity on GCP. |
| D-19 | Unsigned images or missing SBOM in production | Sign images, generate SBOMs, and enforce them at admission time. |
| D-20 | Prediction logs missing `prediction_id` or `entity_id` | Require both fields for traceability and ground-truth joins. |
| D-21 | Prediction logging blocks the async event loop | Buffer and flush logging asynchronously in the background. |
| D-22 | Logging backend failure leaks into the HTTP response path | Swallow logging failures and surface them as observability counters. |
| D-23 | Shared liveness and readiness endpoint | Split `/health`, `/ready`, and startup gating for warm-up correctness. |
| D-24 | SHAP explainer rebuilt on every request | Build once during warm-up and reuse from application state. |
| D-25 | Pod can be terminated mid-request | Keep `terminationGracePeriodSeconds` above the graceful shutdown timeout. |
| D-26 | Deploys bypass staging validation | Enforce dev → staging → prod promotion with environment approvals. |
| D-27 | Deployment ships without a PodDisruptionBudget | Require a PDB and sane minimum replica assumptions. |
| D-28 | Breaking API change without version bump and snapshot refresh | Refresh the OpenAPI snapshot and apply semantic version discipline. |
| D-29 | Namespace missing Pod Security Standards labels | Label namespaces and enforce the correct pod security level by environment. |
| D-30 | Production image lacks SBOM attestation | Attach a CycloneDX SBOM attestation as part of the signed release chain. |
| D-31 | Monolithic IAM identity for ci/deploy/runtime/drift/retrain | Per-purpose, per-environment service accounts with WIF (GCP) and IRSA (AWS); enforced by `tests/test_iam_least_privilege.py`. |
| D-32 | K8s manifests reference Python paths with kebab-case placeholders | Python module paths use `{service}` (snake), never `{service-name}` (kebab); enforced by `tests/policy/test_anti_patterns.py::test_d32_drift_cronjob_python_path`. |
| D-33 | Manual file copying or sed-based placeholder substitution in the scaffolder | The scaffolder (`templates/scripts/new-service.sh`) MUST delegate to `copier copy`. Manual `cp -r` + `sed -i` cannot handle conditional logic, directory renaming, or upgrade paths. Enforced by `scripts/test_scaffold.sh`. |
| D-34 | Unquoted Jinja tokens in YAML list items | All `{@ @}` tokens in YAML lists MUST be quoted: `- "{@ service_name @}"`. Unquoted tokens are invalid YAML. Enforced by `rg -n '^\s*- \{@' templates/service/ --glob "*.yml"` returning zero hits. |
| D-35 | `local` stack profile accepts cloud credentials or targets a cluster | A `local` profile MUST have `requires.kubernetes`, `requires.docker`, `requires.terraform` all `false` and `deploy.enabled` is `false`. Enforced by `tests/policy/test_anti_patterns.py::test_d35_local_profile_no_cloud_deps`. |
| D-36 | Promoting, tagging, or deploying without verified-green CI, or overriding red/missing without STOP-class approval | `/release` and `deploy-gke`/`deploy-aws` (staging/prod) invoke `ci-green-verify` (AUTO) as a hard precondition; an override MUST produce a `scripts/audit_record.py` entry. Enforced by the wiring in `agentic/workflows/release.md` + the deploy skills' Step 0 (ADR-039). |
| D-37 | Non-English documentation or a private/personal repo reference committed to `docs/` or a root doc | This repo's documentation is English-only; a private companion repo must never be named or linked here. Enforced by `scripts/check_doc_coherence.py` C7 (ADR-040). |
| D-38 | Public inference Ingress without an edge-protection component, or disabling/loosening an existing WAF/rate-limit rule | Native-cloud-first: Cloud Armor (GCP) / AWS WAFv2 + Shield Standard (AWS) by default via opt-in Kustomize Components; Cloudflare optional for genuine multi-cloud. `terraform apply` is CONSULT in every environment; disabling a rule is STOP in every environment. Enforced by `tests/policy/test_anti_patterns.py::test_d38_edge_component_carries_implementation_annotation` (ADR-042). |

The full invariant text and operating rules live in [AGENTS.md](../AGENTS.md).

## How this compares

Four open-source projects are widely treated as references in the MLOps space. They are excellent, and this template
deliberately borrows ergonomics from each — through its own canonical layer, never by forking them (see
[ADR-029](decisions/ADR-029-agentic-adoption-contract.md)). They optimize for different things:

| | This template | [Made With ML](https://github.com/GokuMohandas/Made-With-ML) | [Cookiecutter Data Science](https://github.com/drivendataorg/cookiecutter-data-science) | [ZenML](https://github.com/zenml-io/zenml) | [Kubeflow](https://github.com/kubeflow/kubeflow) |
| --- | --- | --- | --- | --- | --- |
| **Primary optimization** | Production discipline + governance | Teaching the *why* | Recognizable project structure | Infra-agnostic pipelines | Full ML platform (pipelines, KServe, Katib) |
| **Production hardening** | leader | medium | low | medium | high |
| **Multi-cloud (GKE + EKS)** | leader | no | no | via stacks | via distro |
| **Governance / supply chain** | unique (AUTO/CONSULT/STOP, 38 anti-patterns, cosign/SBOM/Kyverno) | low | no | medium | low (platform, not policy) |
| **Entry friction** | medium (local-first profile, `copier copy`) | medium | very low | low | very high (needs a platform team) |
| **Standardized scaffolding** | Copier | n/a | de-facto | CLI | n/a |
| **Pedagogy / learning arc** | narrated tutorial + anti-pattern walk-through | leader | medium | good | low |

**What makes this template special** — and what no reference above ships — is the agentic spine: a vendor-neutral
canonical rule store ([ADR-027](decisions/ADR-027-vendor-neutral-canonical-surface.md)) read natively by Cursor,
Devin, Claude Code, and Codex, governed by a three-mode behavior protocol (AUTO/CONSULT/STOP) with dynamic risk
escalation, and 38 contract-tested anti-patterns.

**Where we are improving adoption** — standardized scaffolding (Copier), a local-first on-ramp (stack profiles), a
recognizable layout, and a guided tutorial — is tracked transparently in
[`docs/audit/ACTION_PLAN_ADAPTABILITY.md`](audit/ACTION_PLAN_ADAPTABILITY.md). Every one of those improvements is
required to flow through the canonical agentic layer, so adoption ergonomics never dilute the governance that
differentiates the template.

If you want a guided course on ML fundamentals, start with Made With ML. If you want a minimal, deployment-agnostic
project skeleton, start with Cookiecutter Data Science. If you want orchestrator portability, look at ZenML. If you have
a dedicated platform team and want a full ML platform (pipelines, KServe, Katib), graduate to Kubeflow or Vertex AI
Pipelines. If you want a **governed, production-hardened, multi-cloud service template with an agentic operating
model**, you are in the right place.

### Tools we compose with, not against

Two adjacent tools are complementary rather than alternatives, and are tracked as deliberate seams:

- **[BentoML](https://github.com/bentoml/BentoML)** — best-in-class model packaging and serving DX (adaptive batching,
  `bentoml.Service`). Our serving path (FastAPI + `asyncio.run_in_executor` + `ThreadPoolExecutor`) is correct and
  dependency-light, but BentoML is evaluated as an *optional alternative serving backend* behind the same K8s/HPA
  invariants (1 worker, CPU-only HPA, init-container model load) — see
  [ADR-032](decisions/ADR-032-bentoml-alternative-serving-backend.md). The stance is *evaluate, don't mandate*.
- **[Evidently](https://github.com/evidentlyai/evidently)** — already used for drift and data-quality checks; the drift
  workflow can additionally emit an Evidently HTML report as a reviewer-friendly artifact (roadmap).

## Release and operate

Typical flow:

1. Build and test in CI.
2. Scan dependencies and container image.
3. Generate SBOM and sign by digest.
4. Deploy to `dev`.
5. Promote to `staging` with approval.
6. Validate smoke tests, SLOs, and quality signals.
7. Promote to `prod` through protected environments.
8. Monitor closed-loop metrics, drift, and incident signals.
9. Retrain only through the governed quality gate path.

Deploy, incident, and retrain are part of one operating model, not separate ad-hoc scripts.

**SCM-level protection** sits beneath this flow as a one-time setup: see
[`docs/decisions/ADR-026-branch-protection.md`](decisions/ADR-026-branch-protection.md)
for the two GitHub Rulesets the template ships (main-branch-baseline +
tag-immutability-v). Adopters apply both to their fork with
`make setup-github` once `gh auth login` is configured.

## Optional: Progressive delivery (Argo Rollouts)

`argo-rollout.yaml` ships in `templates/service/k8s/base/` with full security parity to `deployment.yaml` (PSS
restricted, init containers, `model-verifier`), but it is **opt-in** — it is intentionally NOT in
`kustomization.yaml#resources` because it and `deployment.yaml` cannot coexist (they own the same Pods). Enabling is a
deliberate swap.

Enable when you need canary deploys with metric-gated rollback, want to exercise the shipped champion/challenger
`AnalysisTemplate`, or have an SRE rotation that cannot be paged for a metric regression a Rollout could have caught at
30 % traffic. Do not enable for single-replica, low-traffic services.

See [`docs/runbooks/progressive-delivery.md`](runbooks/progressive-delivery.md) for the full enable procedure (base
swap, overlay patch rename, verification steps, failure paths).

## Scope boundaries

**Included:**

- single-service and small-to-medium team MLOps patterns
- multi-cloud Kubernetes deployment (GKE and EKS)
- production CI/CD, supply-chain security, monitoring, and retraining paths
- agentic governance and bounded automation for Devin, Cursor, Claude Code, and Codex

**Not included by default:**

- full workflow orchestration platforms (Airflow, Prefect, Kubeflow Pipelines)
- feature store platform ownership
- multi-region active-active failover
- complex canary meshes beyond the documented rollout boundary
- compliance programs that require dedicated legal or regulated tooling

If you outgrow the template, the documented invariants and ADRs are designed to survive that transition.

## What you must provide before a cloud deploy

Before deploying to a cloud environment, configure the following. Runbooks for each step live under `docs/runbooks/`.

- cloud identity federation (Workload Identity or IRSA)
- remote Terraform state backend
- secret store integrations
- MLflow tracking and registry backend
- observability backends (Prometheus, Grafana, Alertmanager)
- GitHub Environment protections and required reviewers

External dependencies remain your responsibility: cloud accounts, Kubernetes clusters, MLflow backend, secret stores,
and observability backends must exist before the template can operate in a real environment.
