# AGENTS.md — ML-MLOps Production Template

## Project Identity

**ML-MLOps Production Template**: Agent-driven framework for building and maintaining production-grade ML systems with
multi-cloud deployment (GKE + EKS), comprehensive observability, and enterprise CI/CD. Every architectural decision
documented in ADRs with measured trade-offs.

- **Stack**: Python 3.11+, scikit-learn, XGBoost, LightGBM, FastAPI, Docker, Kubernetes, Terraform, GitHub Actions
- **Clouds**: GCP (primary) + AWS (secondary parity)
- **Tracking**: MLflow (self-hosted on K8s)
- **Monitoring**: Prometheus + Grafana + AlertManager + Evidently
- **Data**: DVC (GCS + S3 remotes), Pandera validation

## Agent Architecture

```text
LAYER 1: ORCHESTRATOR
  → Receives high-level requests ("create a new ML service for [domain]")
  → Determines which specialist agents are needed and in what order
  → Manages task dependencies (cannot deploy before training completes)
  → Calibrates engineering level to project scale (no under/over-engineering)

LAYER 2: SPECIALIST AGENTS (build phase)
  ├── Agent-EDAProfiler       Dataset exploration, baseline distributions, leakage pre-audit
  ├── Agent-DataValidator     Pandera schemas, DVC versioning, leakage checks
  ├── Agent-MLTrainer         Training pipeline, model selection, Optuna tuning
  ├── Agent-APIBuilder        FastAPI app, async inference, SHAP integration
  ├── Agent-DockerBuilder     Optimized Dockerfile, init container pattern
  ├── Agent-K8sBuilder        K8s manifests, HPA, Kustomize overlays
  ├── Agent-TerraformBuilder  IaC for GCP + AWS resources
  ├── Agent-CICDBuilder       GitHub Actions workflows
  ├── Agent-SecurityAuditor   Secret scans, IAM least-privilege, image signing, SBOM
  ├── Agent-MonitoringSetup   Prometheus metrics, Grafana dashboards, alerts
  ├── Agent-DriftSetup        PSI thresholds, CronJob, heartbeat alerts
  ├── Agent-DocumentationAI   ADRs, READMEs, runbooks
  └── Agent-TestGenerator     Unit, integration, regression, load tests

LAYER 3: MAINTENANCE AGENTS (operate phase)
  ├── Agent-DriftMonitor      PSI scores → alerts → retraining triggers
  ├── Agent-RetrainingAgent   Executes retraining with quality gates
  ├── Agent-CostAuditor       Reviews costs against budget
  ├── Agent-DocUpdater        Keeps documentation in sync with code (/document-changes)
  └── Agent-QualityGuardian   Recurring enterprise audit + Q-01..Q-08 anti-pattern watch (ADR-043)
```

## Critical Patterns — DO NOT VIOLATE

### ML Serving Invariants

- **NEVER** use `uvicorn --workers N` in Kubernetes — causes CPU thrashing, dilutes HPA signal
- **NEVER** use memory as an HPA metric for ML pods — fixed RAM footprint prevents scale-down
- **ALWAYS** use `asyncio.run_in_executor()` + `ThreadPoolExecutor` for CPU-bound inference
- **ALWAYS** use `KernelExplainer` for SHAP with complex ensemble/pipeline models
- **ALWAYS** use compatible release pinning (`~=`) for ML dependencies — `numpy 2.x` silently corrupts joblib
- **NEVER** bake model artifacts into Docker images — use `emptyDir` + Init Container
- **NEVER** call `model.predict()` directly in an async endpoint — blocks asyncio event loop

### Infrastructure Invariants

- **ALWAYS** use IRSA (AWS) and Workload Identity (GCP) — no hardcoded credentials in pods
- **ALWAYS** use remote Terraform state (GCS for GCP, S3+DynamoDB for AWS)
- **NEVER** commit secrets to tfvars or repository — use Secrets Manager
- **NEVER** overwrite existing container image tags — tags are immutable
- **ALWAYS** verify `kubectl config current-context` before applying K8s manifests

### Model Quality Invariants

- **ALWAYS** define minimum production metric thresholds per service
- **ALWAYS** run fairness checks (Disparate Impact Ratio >= 0.80) before every deploy
- **ALWAYS** compute SHAP values in ORIGINAL feature space, never transformed space
- **ALWAYS** include a data leakage sanity check (suspiciously high metrics = investigate)
- **NEVER** promote a model without passing ALL quality gates

### Documentation Invariants

- **ALWAYS** create an ADR for every non-trivial architectural decision
- **ALWAYS** document costs with real measured numbers, not estimates
- **ALWAYS** document production problems with measured evidence

## Engineering Calibration Principle

```text
The solution must match the scale of the problem.

UNDER-ENGINEERING: Missing monitoring, no tests, no drift detection, no ADRs
  → The system will fail silently in production

CORRECT SCALE: Each component sized to the actual requirements
  → CronJob + GitHub Actions for 2-3 models (not Airflow/Prefect)
  → Pandera for in-memory DataFrames (not Great Expectations)
  → PSI with quantile bins for drift (not a full feature store)

OVER-ENGINEERING: Full orchestrator for 2 models, GE for simple DataFrames
  → Complexity without proportional value
```

## Agent Behavior Protocol

Agents operate in one of **three modes** depending on the operation's risk and reversibility.
This protocol is NOT optional — every skill and workflow must map its operations to a mode.

### The three modes

| Mode | Meaning | Example |
| ------ | --------- | --------- |
| **AUTO** | Execute without asking. Reversible or low-risk. | Scaffolding a new service, running tests, generating reports |
| **CONSULT** | Propose the plan + rationale, wait for human approval before executing. | Promoting a model to production, applying Terraform in staging |
| **STOP** | Do nothing. Block the pipeline. Require explicit human instruction to proceed. | `terraform apply` in prod, rotating a secret, overriding a quality gate failure |

### Operation → Mode mapping (canonical)

| Operation | Mode | Notes |
| ----------- | ------ | ------- |
| Scaffold new service (`new-service.sh`) | AUTO | Reversible via `rm -rf` |
| Run EDA pipeline on `data/raw/` | AUTO | No side effects outside `eda/` |
| Generate ADR, README, runbook | AUTO | Documents are reviewable in PRs |
| Run tests, lint, validators | AUTO | Read-only or sandboxed |
| `dvc add` new data artifact | AUTO | Reversible before push |
| Train model locally + save to MLflow | AUTO | Experiment tracking is append-only |
| Transition MLflow model to `Staging` | **CONSULT** | Affects staging deploys |
| Promote model to `Production` | **STOP** | Requires governance approval (see ADR-002) |
| `terraform plan` any environment | AUTO | Read-only |
| `terraform apply` dev | AUTO | Reversible, dev is sandbox |
| `terraform apply` staging | **CONSULT** | Propose diff, wait for approval |
| `terraform apply` prod | **STOP** | Requires PR + Platform Engineer approval |
| `kubectl apply` dev cluster | AUTO | — |
| `kubectl apply` staging cluster | **CONSULT** | Show diff, wait |
| `kubectl apply` prod cluster | **STOP** | Via GitHub Actions only, with approval |
| Build + push Docker image | AUTO | Images are content-addressable |
| Sign image (Cosign) | AUTO | Additive |
| Apply branch-protection rulesets to a fresh fork | **CONSULT** | `make setup-github`; mutates repo SCM settings — see ADR-026 |
| Modify branch-protection rulesets on an existing repo | **STOP** | Requires PR amending `docs/governance/branch-protection.md` + ADR-026 §Revisit triggers |
| Rotate a leaked secret | **STOP** | Execute `/secret-breach` workflow; never silent rotation |
| Delete any cloud resource | **STOP** | Always |
| Override a failing quality gate | **STOP** | Requires ADR documenting why |
| `terraform apply` of edge-protection resources (Cloud Armor / WAFv2 / Cloudflare), ANY environment | **CONSULT** | Never downgrades to AUTO in dev — public exposure + cost do not shrink because the environment label says "dev" (D-38, ADR-042) |
| Disable, remove, or loosen an existing WAF/rate-limit rule, ANY environment | **STOP** | Always, regardless of environment or urgency — mirrors `rollback`'s environment-independent STOP (D-38, ADR-042) |

### Escalation triggers (automatic STOP)

The agent **must** escalate to STOP mode even from AUTO/CONSULT when any of:

- Metric suspiciously high (D-06): primary metric > 0.99 without explanation
- Fairness DIR in `[0.80, 0.85]` — within margin, human judgment required
- Drift PSI > 2× the configured threshold (not just > threshold)
- Cost estimate > 1.2× monthly budget for that environment
- Any detection of a credential pattern in a commit, log, or artifact
- A test that previously passed now fails without a code change explanation

### How agents signal mode transitions

When an agent decides to change mode, it must output a structured signal:

```text
[AGENT MODE: CONSULT]
Operation: Transition fraud_detector v42 to MLflow Staging
Rationale: CI passed, tests green, metrics within gates
Waiting for: Tech Lead approval via PR review
```

This makes handoffs auditable and reproducible.

## Agent Handoff Schema

When one specialist agent produces an artifact consumed by another, the handoff
MUST use a typed dataclass from `templates/service/common_utils/agent_context.py`. This
replaces ad-hoc dict passing with validated contracts that fail fast.

Canonical handoff chain:

```text
Agent-EDAProfiler     ──[EDAHandoff]──►     Agent-MLTrainer
Agent-MLTrainer       ──[TrainingArtifact]──► Agent-DockerBuilder
Agent-DockerBuilder   ──[BuildArtifact]──► Agent-SecurityAuditor
Agent-SecurityAuditor ──[SecurityAuditResult]──► Agent-K8sBuilder
Agent-K8sBuilder      ──[DeploymentRequest]──► cluster (via GitHub Actions)
```

Each dataclass is `frozen=True` (immutable) and validates invariants at construction.
Example: `DeploymentRequest` refuses to construct if `environment == PRODUCTION` and
`security_audit.passed == False` — a gate that cannot be bypassed by omission.

## Audit Trail Protocol

Every agentic operation MUST produce an `AuditEntry` (defined in
`common_utils/agent_context.py`). Entries are append-only JSONL in
`ops/audit.jsonl` and mirrored to the GitHub Actions step summary in CI.

The protocol is OPERATIONAL (ADR-014 §3.5):

- **CLI wrapper**: `scripts/audit_record.py` — invoked from any CI step
  or local skill execution; takes `--agent --operation --environment
  --base-mode --final-mode --result --inputs --outputs --approver` and
  writes the entry plus a GHA step-summary section.
- **Deploy chain**: `templates/service/.github/workflows/deploy-common.yml` invokes
  `audit_record.py` on every deploy (success AND failure via
  `if: always()`), passing the dynamically-computed `final_mode` from
  the `Compute dynamic risk mode` step (ADR-014 §4.2).
- **Risk context wiring**: `risk_signals` field is populated automatically
  from `risk_context.RiskContext` when one is passed to
  `AuditLog.record_operation`, so the same audit entry records BOTH
  the static base mode AND the live signal that escalated it.

Minimum fields:

- `agent`, `operation`, `environment`, `mode`
- `inputs` (what was requested)
- `outputs` (what was produced — sha256, image refs, PR URLs)
- `approver` (populated when mode is CONSULT or STOP)
- `result` (`success` | `failure` | `halted`)
- `timestamp` (UTC ISO8601)

Agents MUST NOT open a GitHub issue for every operation (noise). Instead:

- Routine operations → `ops/audit.jsonl` + GHA step summary
- Operations that required CONSULT/STOP → additionally open a GitHub issue tagged
  `audit` with the entry pre-filled
- Failures → open an issue tagged `audit` + `incident`

## Agent Permissions Matrix

Some operations are intrinsically not permitted for certain agents regardless of
environment. This matrix codifies capability boundaries.

| Agent | dev | staging | production |
| ------- | ----- | --------- | ------------ |
| Agent-EDAProfiler | read data, write `eda/**` | read data | **blocked** |
| Agent-MLTrainer | train, log MLflow, transition None→Staging | transition Staging→None | transition to Production **blocked** (via PR only) |
| Agent-DockerBuilder | build, push to registry | build, push, sign | build, push, sign |
| Agent-K8sBuilder | `kubectl apply` | `kubectl apply` (CONSULT) | **blocked** (GitHub Actions only) |
| Agent-TerraformBuilder | `plan`, `apply` | `plan`, `apply` (CONSULT) | `plan` only; `apply` **blocked** |
| Agent-SecurityAuditor | scan, report | scan, report, block pipeline | scan, report, block pipeline |
| Agent-DriftMonitor | read metrics | read metrics | read metrics |
| Agent-RetrainingAgent | trigger retrain | trigger retrain (CONSULT) | trigger retrain **blocked**; propose via PR |
| Agent-CostAuditor | read billing | read billing | read billing |
| Agent-DocumentationAI | write `docs/**` | — | — |
| Agent-QualityGuardian | scan, report, write `docs/audit/**` | scan, report | scan, report; gate changes **blocked** (via PR + ADR only) |

"Blocked" means the agent must emit `[AGENT MODE: STOP]` and refuse the operation,
even if the human insists in conversation. The only path through is the governed
GitHub Actions flow with required_reviewers.

## Anti-Patterns That Agents Must Detect and Correct

| ID | Anti-Pattern | Corrective Action |
| ---- | ------------- | ------------------- |
| D-01 | `uvicorn --workers N` in Dockerfile or deployment | Change to 1 worker, add ThreadPoolExecutor |
| D-02 | Memory HPA in any HorizontalPodAutoscaler | Remove memory metric, keep CPU only |
| D-03 | `model.predict()` directly in async endpoint | Wrap in `run_in_executor` with ThreadPoolExecutor |
| D-04 | `shap.TreeExplainer` with ensemble/pipeline/stacking | Change to KernelExplainer with predict_proba_wrapper |
| D-05 | `==` in requirements.txt for ML packages | Change to `~=` (compatible release) |
| D-06 | Unrealistically high primary metric | Investigate data leakage before promoting |
| D-07 | SHAP background data with only one class | Replace with representative sample |
| D-08 | PSI with uniform bins (not quantile-based) | Refactor to quantile bins from reference |
| D-09 | Drift detection without heartbeat alert | Add AlertManager alert for broken CronJobs |
| D-10 | `terraform.tfstate` in git repository | Move to remote state, rotate exposed secrets |
| D-11 | Models included in Docker image | Remove, implement init container pattern |
| D-12 | No quality gates before model promotion | Add all gates before deploy |
| D-13 | EDA performed directly on production data without sandbox | Move to isolated `data/raw/` copy; EDA never writes to prod paths |
| D-14 | Pandera schema without observed ranges from EDA | Add `Check.in_range(min, max)` derived from EDA distribution analysis |
| D-15 | Baseline distributions not persisted for drift detection | Save canonical `baseline_distributions.parquet` during EDA; consume in drift CronJob |
| D-16 | Feature engineering without documented rationale | Add canonical `feature_catalog.yaml` with justification tied to EDA evidence |
| D-17 | Hardcoded credentials in code, configs, or `os.environ[...]` for secrets | Use `common_utils/secrets.py` — delegates to AWS Secrets Manager / GCP Secret Manager |
| D-18 | Static AWS access keys or GCP JSON service-account keys in production | Migrate to IRSA (AWS) or Workload Identity (GCP) — remove all static creds |
| D-19 | Unsigned images reaching production or missing SBOM | Sign with Cosign, generate SBOM with Syft, enforce via Kyverno admission controller |
| D-20 | Prediction log events missing `prediction_id` or `entity_id` | Both are required at construction of `PredictionEvent`; `entity_id` is the JOIN key with ground truth |
| D-21 | Prediction logging blocking the async inference event loop | `log_prediction()` MUST buffer + flush in background task via `run_in_executor(None, ...)` |
| D-22 | Logging backend failure propagating to the HTTP response | `log_prediction()` MUST swallow exceptions and increment `prediction_log_errors_total` — observability failures NEVER break serving |
| D-23 | Liveness and readiness probes share a path | Split: `/health` (liveness, always 200 while process alive) and `/ready` (readiness, 503 until warm-up done). Add `startupProbe` on `/health` with `failureThreshold: 24` |
| D-24 | SHAP explainer rebuilt per request | Build `KernelExplainer` once in warm-up (`fastapi_app.py::warm_up_model`), cache on app state, reuse for every `/explain` call |
| D-25 | Pod killed mid-request on deploy / scale-down | Set `terminationGracePeriodSeconds` (default 30s) STRICTLY GREATER than uvicorn's `--timeout-graceful-shutdown` (default 20s) |
| D-26 | Deploys go directly to prod without staging validation | Four-job chain (build → dev → staging → prod) with GitHub Environment Protection: 1 reviewer at staging, 2 reviewers + wait_timer + tag-only at prod (ADR-011) |
| D-27 | Deployment without PodDisruptionBudget | Every Deployment ships with `PodDisruptionBudget` (`minAvailable: 1`). HPA `minReplicas >= 2`. `minAvailable: 0` requires `mlops.template/pdb-zero-acknowledged` annotation referencing an ADR |
| D-28 | Breaking API change without version bump + snapshot update | Update `tests/contract/openapi.snapshot.json` via `scripts/refresh_contract.py`, bump `app.version` in `main.py` (semver: additive=minor, renames/narrows=major), announce in `CHANGELOG.md ### API Contract`. CI rejects snapshot changes without matching version bump |
| D-29 | Namespace without Pod Security Standards labels | Label prod namespaces `pod-security.kubernetes.io/enforce: restricted`; dev/staging `enforce: baseline` + `warn/audit: restricted`. See `templates/k8s/policies/pod-security-standards.yaml`. Container `securityContext` drops ALL capabilities, `allowPrivilegeEscalation: false`, `runAsNonRoot: true` |
| D-30 | Production image without SBOM attestation | Deploy workflow MUST generate an SBOM (Syft / CycloneDX) and attach it as a Cosign attestation (`cosign attest --type cyclonedx`). Full SLSA L3 provenance is documented as roadmap in `deploy-gcp.yml` §1b |
| D-31 | Monolithic IAM identity for CI / deploy / runtime / drift / retrain (ADR-017) | Each cloud Terraform splits identities by **purpose** (`ci`, `deploy`, `runtime`, `drift`, `retrain`, `nodes`) AND by **environment** (one set per env). GCP: 6 `google_service_account` resources with WI bindings for runtime/drift/retrain. `nodes` is the GKE node identity — omitting it silently falls back to the default Compute Engine SA, which typically holds `roles/editor` project-wide (Trivy GCP-0050). AWS: GitHub OIDC for ci/deploy + IRSA for runtime/drift/retrain. Audit trail identifies which workflow acted. Blast radius: leaked CI key cannot read prod model artifacts; leaked runtime key cannot push images. Enforced by `tests/test_iam_least_privilege.py` (no wildcard principals, no `Action: "*"`, no IAM mutation on CI role) |
| {% raw %}D-32 | K8s manifests reference Python package paths using kebab-case placeholders | Python module paths MUST use snake_case (`{@ service_slug @}`), never kebab (`{service-name}`). The scaffolder renames `src/{@ service_slug @}` → `src/<snake_slug>`; a manifest pointing at `src/{service-name}/...` (e.g. `src/fraud-detector/...`) applies cleanly but explodes at runtime with `ModuleNotFoundError` because `fraud-detector` is not a valid Python package name. Enforced by `tests/policy/test_anti_patterns.py::test_d32_drift_cronjob_python_path` (placeholder leak guard + snake-case check + on-disk directory existence){% endraw %} |
| D-33 | Manual file copying or sed-based placeholder substitution in the scaffolder | The scaffolder (`templates/scripts/new-service.sh`) MUST delegate to `copier copy`. Manual `cp -r` + `sed -i` cannot handle conditional logic, directory renaming, or upgrade paths. Enforced by `scripts/test_scaffold.sh` (validates Copier render + zero unreplaced Jinja tokens) |
| {% raw %}D-34 | Unquoted Jinja tokens (`{@ @}`) in YAML list items | An unquoted `- {@ service_name @}` is invalid YAML (`@` cannot start a token). All `{@ @}` tokens in YAML lists MUST be quoted: `- "{@ service_name @}"`. Enforced by `rg -n '^\s*- \{@' templates/service/ --glob "*.yml"` returning zero hits{% endraw %} |
| D-35 | A `local` stack profile that accepts cloud credentials or targets a cluster | The `local` profile (`configs/profiles/local.yaml`) MUST set `requires.cloud_credentials: false`, `requires.kubernetes: false`, `requires.docker: false`, and `deploy.enabled: false`. A local profile that imports cloud creds or targets a cluster violates the local-first contract (ADR-033). Enforced by `tests/policy/test_anti_patterns.py::test_d35_local_profile_no_cloud_deps` |
| D-36 | Promoting, tagging, or deploying without a verified-green CI check, or overriding a red/missing check without STOP-class human approval + an audit record | `/release` and `deploy-gke`/`deploy-aws` (staging/prod) MUST invoke `ci-green-verify` (AUTO, read-only) as a hard precondition before proceeding. Overriding a red/missing verdict is always STOP, regardless of environment or urgency, and MUST produce a `scripts/audit_record.py` entry before the gated action proceeds (ADR-039). Enforced by the wiring in `agentic/workflows/release.md` step 1 and the deploy skills' Step 0; `validate_agentic_manifest.py --strict` forbids any `escalation_override` de-escalating this mode |
| D-37 | Non-English prose or a private/personal repo name committed to `docs/` or a root-level doc in this public repo | This repo's documentation is English-only; a private companion repo (e.g. a personal study guide) MUST never be named, linked, or otherwise referenced here — not even as a "why this design" aside. AUDIT R10 (2026-07-02) found four `docs/audit/*.md` files fully in Spanish and one private repo named across 6 files, including a dead clickable URL. Enforced by `scripts/check_doc_coherence.py` C7 (`check_doc_language_and_privacy`), which scans every tracked `docs/**/*.md` and root `*.md` for a curated Spanish-word list and a private-repo denylist (ADR-040) |
| D-38 | A production Ingress with no edge-protection component wired in, or disabling/loosening an existing WAF/rate-limit rule without STOP-class approval | Every `gcp-prod`/`aws-prod` overlay MUST reference the matching `k8s/components/edge-{gcp,aws}/` Kustomize Component (Cloud Armor / AWS WAFv2 + Shield Standard by default; Cloudflare optional for genuine multi-cloud, ADR-042). The resulting Ingress MUST carry a non-empty `edge-protection.mlops-template.io/implementation` annotation. `terraform apply` of these resources is CONSULT in every environment including dev (public exposure + cost do not shrink because the label says "dev"); disabling or loosening an existing rule is STOP in every environment, no exceptions. Enforced by `tests/policy/test_anti_patterns.py::test_d38_edge_component_carries_implementation_annotation` and the `edge-audit` skill |

## Session Initialization Protocol

When starting a new session in a project derived from this template:

1. **READ** this AGENTS.md fully before writing any code
2. **CONFIRM** the project has completed scaffold: check that `{% raw %}{@ service_name @}{% endraw %}` placeholders
   have been replaced
3. **CHECK** invariants:

   ```bash
   grep -r "TODO\|{% raw %}{@ service_name @}{% endraw %}\|{% raw %}{@ service_slug @}{% endraw %}" . --include="*.py" --include="*.yaml"
   ```

4. **IDENTIFY** the current phase: **Build** (new service) vs **Operate** (existing service)
5. **SELECT** the appropriate skill or workflow based on the task

## How to Invoke Skills and Workflows

**Two invocation taxonomies, kept orthogonal to AUTO/CONSULT/STOP** (ADR-041):

- **Model-invoked** — a **Skill**: the agent decides to invoke it because
  the current task matches its `when_to_use` trigger. No slash command
  required.
- **User-invoked** — a **Workflow**: a human types the slash command.
  Not every skill has a matching workflow (e.g. `debug-ml-inference`,
  `security-audit`, `rule-audit`, `pr-review`, `diagnose-bug` are
  skill-only) — a workflow only exists where a human routinely wants to
  trigger the procedure directly rather than wait for the agent to infer
  the trigger. Each skill also declares an optional `domain:` in
  `agentic_manifest.yaml` (`ml-data` / `platform-infra` /
  `security-compliance` / `sre-operations`) purely for role-based
  filtering/discoverability — it does not change AUTO/CONSULT/STOP.

**Skills** (multi-step procedures — invoked by the agent when task matches):

- `new-service` — scaffold a new ML service using `templates/scripts/new-service.sh`
- `eda-analysis` — 6-phase exploratory analysis with leakage gate + baseline distributions
- `security-audit` — pre-build/pre-deploy scans: gitleaks, trivy, cosign verify, IAM review
- `secret-breach-response` — incident playbook when a secret is leaked (detect → rotate → audit → postmortem)
- `debug-ml-inference` — diagnose serving issues (starts with D-01..D-36 checklist)
- `drift-detection` — analyze PSI drift + concept drift (sliced performance)
- `concept-drift-analysis` — root-cause sliced performance regressions with ground truth
- `model-retrain` — execute retraining with quality gates + Champion/Challenger
- `deploy-gke` / `deploy-aws` — deploy to GKE or EKS with Kustomize overlays
- `release-checklist` — full multi-cloud release process
- `rollback` — STOP-class emergency revert (Argo Rollouts abort + undo, MLflow revert, alert silencing)
- `cost-audit` — monthly cloud cost review
- `batch-inference` — scaffold + run batch scoring jobs (CronJob + Parquet output) reusing the service's model and
  feature-engineering code
- `performance-degradation-rca` — end-to-end RCA for a performance-degradation incident: correlates sliced metrics,
  drift, deploy history, upstream data changes, and prediction logs into one evidence-backed root cause
- `rule-audit` — automated scan of a service/repo for compliance with AGENTS.md invariants D-01 through D-36; produces a
  PASS/FAIL report with file:line evidence
- `scaffold-update` — update an existing scaffolded service with the latest template changes via `copier update`
- `stack-switch` — switch a scaffolded service between stack profiles (local, staging, prod)
- `template-onboard` — interview the adopter and emit a `*_context.local.yaml` (no secrets)
- `doc-coherence` — keep cross-document facts coherent (version, CHANGELOG, ADRs, anti-pattern + surface counts); drives
  `scripts/check_doc_coherence.py` (rule 16, ADR-031)
- `ci-green-verify` — verify GitHub Actions CI status before a promote/release/deploy action; AUTO to check, STOP to
  override red/missing (D-36, ADR-039)
- `pr-review` — dual-axis change review (Standards + Spec, evaluated in isolation, then reconciled) (ADR-041)
- `diagnose-bug` — systematic non-ML-serving bug diagnosis: reproduce → minimize → hypothesize → instrument → fix →
  regression-test (ADR-041)
- `new-service-spec` — capture the ML problem spec (label, fairness attribute, cost asymmetry) BEFORE scaffolding, so
  `quality_gates.yaml` thresholds trace to a real answer (ADR-041)
- `incident-postmortem` — blameless post-incident review after `/incident`/`/rollback`/`/secret-breach`: timeline from
  primary sources, 5-whys, owned action items (ADR-041)
- `edge-audit` — scan a service for edge-protection coverage (WAF, DDoS mitigation, rate limiting) against D-38;
  AUTO/read-only, mirrors `rule-audit` for one invariant domain (ADR-042)
- `enterprise-audit` — recurring 23-domain enterprise/ISO repository audit (governance → DX) with file:line evidence and
  risk ratings; AUTO to scan, CONSULT to fix, STOP to weaken any gate (ADR-043)

**Workflows** (user-triggered via slash commands):

- `/new-service` — end-to-end service creation
- `/retrain` — model retraining with quality gates
- `/incident` — classify severity (P1-P4) → execute runbook
- `/drift-check` — run PSI analysis for one or all services
- `/release` — multi-cloud deploy with rollback plan
- `/cost-review` — monthly FinOps analysis
- `/load-test` — Locust load tests against ML services
- `/new-adr` — create Architecture Decision Record
- `/eda` — run 6-phase exploratory data analysis on a new dataset
- `/secret-breach` — incident workflow for leaked secrets (STOP pipeline, rotate, audit)
- `/performance-review` — monthly sliced-performance review using ground-truth metrics (detect silent concept drift,
  document findings)
- `/rollback` — emergency rollback of a production ML service — pairs with the `rollback` skill (STOP-class operation)
- `/scaffold-update` — pull template improvements into an existing scaffolded service via `copier update`
- `/stack-switch` — switch stack profile (local, staging, prod)
- `/onboard` — generate adopter context file (interview + validate, no secrets)
- `/doc-coherence` — restore + verify cross-document coherence (version, CHANGELOG, ADRs, anti-pattern + surface counts)
- `/ci-green` — verify CI status for a ref before a promote/release/deploy action (D-36, ADR-039)
- `/edge-setup` — wire native-cloud edge protection (Cloud Armor / AWS WAF+Shield) or optional Cloudflare into an
  overlay; CONSULT for apply in any environment, STOP for disabling an existing rule (D-38, ADR-042)
- `/audit-quality` — run the recurring enterprise audit + Q-01..Q-08 sweep; AUTO scan, CONSULT fixes, STOP on gate
  weakening (ADR-043)
- `/document-changes` — document the current working set: CHANGELOG entry, rule-16 cascade, audit-trail record;
  Agent-DocUpdater entry point (ADR-043)

## Agentic Configuration

```text
agentic/                                # Canonical agentic source (ADR-027) — 19 rules, 27 skills, 20 workflows
├── rules/                              # Behavioral constraints (context-aware)
│   ├── 01-mlops-conventions.md         # always_on — stack + Behavior Protocol (static + dynamic ADR-010)
│   ├── 02-kubernetes.md                # glob: k8s/**/*.yaml, helm/**/*.yaml — D-02/11/23/25/27/29
│   ├── 03-terraform.md                 # glob: **/*.tf — remote state, no secrets
│   ├── 04a-python-serving.md           # glob: **/app/*.py, **/api/*.py — D-01/03/04/24
│   ├── 04b-python-training.md          # glob: **/training/*.py, **/models/*.py — D-05/06/12
│   ├── 05-github-actions.md            # glob: .github/workflows/*.yml — D-26/30, OIDC, no static creds
│   ├── 06-documentation.md             # glob: docs/**/*.md — ADR + measured numbers
│   ├── 07-docker.md                    # glob: **/Dockerfile*, docker-compose*.yml — D-11/19
│   ├── 08-data-validation.md           # glob: **/schemas.py, **/validate*.py — D-14/15
│   ├── 09-monitoring.md                # glob: monitoring/**/* — metrics catalog, alerts, dashboards
│   ├── 10-examples.md                  # glob: examples/**/* — demo scope, no prod patterns
│   ├── 11-data-eda.md                  # glob: eda/**/*, **/notebooks/**/*.ipynb — D-13/16, leakage gate
│   ├── 12-security-secrets.md          # always_on — D-17/D-18/D-19, no hardcoded creds
│   ├── 13-closed-loop-monitoring.md    # glob: prediction_logger/ground_truth/performance_monitor — D-20/21/22
│   ├── 14-api-contracts.md             # glob: **/app/schemas.py, **/tests/contract/** — D-28, OpenAPI snapshot + semver
│   ├── 15-template-lifecycle.md        # glob: copier.yml, templates/service/** — D-33/34, Copier scaffolding invariants
│   ├── 16-doc-coherence.md             # glob: VERSION, CHANGELOG.md, llms.txt, docs/decisions/** — cross-doc coherence (ADR-031)
│   ├── 17-edge-protection.md           # glob: **/ingress*.yaml, k8s/components/edge-*/**, infra/terraform WAF/security-policy/cloudflare — D-38 (ADR-042)
│   └── 18-audit-quality.md             # glob: CI workflows, LICENSE/llms.txt, gate configs, scripts — Q-01..Q-08 (ADR-043)
├── skills/                             # 27 multi-step operational procedures
│   ├── batch-inference/SKILL.md
│   ├── ci-green-verify/SKILL.md
│   ├── concept-drift-analysis/SKILL.md
│   ├── cost-audit/SKILL.md
│   ├── debug-ml-inference/SKILL.md
│   ├── deploy-aws/SKILL.md
│   ├── deploy-gke/SKILL.md
│   ├── diagnose-bug/SKILL.md
│   ├── doc-coherence/SKILL.md
│   ├── drift-detection/SKILL.md
│   ├── eda-analysis/SKILL.md
│   ├── edge-audit/SKILL.md
│   ├── enterprise-audit/SKILL.md
│   ├── incident-postmortem/SKILL.md
│   ├── model-retrain/SKILL.md
│   ├── new-service/SKILL.md
│   ├── new-service-spec/SKILL.md
│   ├── performance-degradation-rca/SKILL.md
│   ├── pr-review/SKILL.md
│   ├── release-checklist/SKILL.md
│   ├── rollback/SKILL.md
│   ├── rule-audit/SKILL.md
│   ├── scaffold-update/SKILL.md
│   ├── secret-breach-response/SKILL.md
│   ├── security-audit/SKILL.md
│   ├── stack-switch/SKILL.md
│   └── template-onboard/SKILL.md
└── workflows/                          # 20 slash-command workflows
    ├── audit-quality.md                # /audit-quality
    ├── ci-green.md                     # /ci-green
    ├── cost-review.md                  # /cost-review
    ├── doc-coherence.md                # /doc-coherence
    ├── document-changes.md             # /document-changes
    ├── drift-check.md                  # /drift-check
    ├── edge-setup.md                   # /edge-setup (CONSULT-class)
    ├── eda.md                          # /eda
    ├── incident.md                     # /incident
    ├── load-test.md                    # /load-test
    ├── new-adr.md                      # /new-adr
    ├── new-service.md                  # /new-service
    ├── performance-review.md           # /performance-review
    ├── release.md                      # /release
    ├── retrain.md                      # /retrain
    ├── rollback.md                     # /rollback (STOP-class)
    ├── scaffold-update.md              # /scaffold-update
    ├── secret-breach.md                # /secret-breach (STOP-class)
    ├── stack-switch.md                 # /stack-switch
    └── onboard.md                      # /onboard
```

Note: `pr-review`, `diagnose-bug`, and `new-service-spec` are skill-only
(no matching workflow) — same pattern as `debug-ml-inference`,
`security-audit`, and `rule-audit`: invoked by trigger match, not a slash
command. `incident-postmortem` chains from the existing `/incident`
workflow rather than getting its own.

### Skills → Workflow Cross-References

| Trigger | Skill Invoked | Workflow Chained |
| --------- | -------------- | ----------------- |
| New dataset to explore | `eda-analysis` | `/eda` → `/new-service` (if leakage-free) |
| Pre-build / pre-deploy | `security-audit` | auto-chain before DockerBuilder/K8sBuilder |
| Secret leak detected | `secret-breach-response` | `/secret-breach` (STOP pipeline, rotate) |
| Inference bug | `debug-ml-inference` | `/incident` |
| Drift alert (PSI ≥ threshold) | `drift-detection` | `/retrain` |
| Version release | `release-checklist` | `/release` |
| Tag push (GKE) | `deploy-gke` | — |
| Tag push (EKS) | `deploy-aws` | — |
| Scheduled retrain | `model-retrain` | `/drift-check` post-deploy |
| New ADR / version bump / surface change | `doc-coherence` | `/doc-coherence` (propagate + gate) |
| New ML service request | `new-service` | `/new-service` |
| Monthly cost review | `cost-audit` | `/cost-review` |
| New ML service request (before scaffolding) | `new-service-spec` | → `new-service` / `/new-service` |
| Non-trivial PR/diff ready for merge | `pr-review` | — (skill-only) |
| Non-ML-serving bug report | `diagnose-bug` | — (skill-only) |
| Incident resolved | `incident-postmortem` | chains from `/incident` (after closure) |
| Pre-deploy check / scheduled edge-coverage review | `edge-audit` | `/edge-setup` (if a FAIL needs wiring) |
| Quarterly / pre-minor-release audit sweep | `enterprise-audit` | `/audit-quality` → `/document-changes` |
| Any non-trivial working set before review | `doc-coherence` | `/document-changes` (CHANGELOG + cascade + audit trail) |

## Multi-IDE Support

The template supports **4 agent surfaces** with equivalent invariant coverage: Devin (Desktop), Cursor, Claude Code, and
Codex. `AGENTS.md` is the behavior authority, `agentic/` is the vendor-neutral canonical body store (ADR-027), and
`templates/config/agentic_manifest.yaml` is the cross-surface index. Surfaces are generated, never hand-edited:
`.devin/` is a full-body **mirror** (the IDE ingests bodies); `.cursor/`, `.claude/`, `.codex/` are thin **pointers**.

Generated surfaces are produced from the canonical store, not forks:

```bash
python3 scripts/sync_agentic_adapters.py
python3 scripts/sync_agentic_adapters.py --check
python3 scripts/validate_agentic_manifest.py --strict
```

```text
agentic/rules/         # CANONICAL rules: 18 files (humans edit here)
agentic/skills/        # CANONICAL skills: 26 SKILL.md files
agentic/workflows/     # CANONICAL workflows: 18 files

.devin/rules/          # generated MIRROR (full bodies): 19 files
.devin/skills/         # generated MIRROR: 27 SKILL.md files
.devin/workflows/      # generated MIRROR: 20 files

.cursor/rules/         # generated rule pointers: 19 .mdc files
.cursor/commands/      # generated workflow pointers: 20 commands

.claude/rules/         # generated rule pointers: 19 .md files
.claude/skills/        # generated skill pointers + INDEX.md: 27 skills as <id>/SKILL.md (Claude Code discoverable layout)
.claude/commands/      # generated workflow pointers: 20 commands

.codex/rules/          # generated rule pointers: 19 .md files
.codex/workflows/      # generated workflow pointers: 20 workflows
.codex/automations/    # Codex-specific schedules/events, never STOP writes

.agents/skills/        # generated skill pointers + INDEX.md, shared by Cursor and Codex: 27 skills as <id>/SKILL.md (ADR-027 §9)
```

Note: the project often says "17 rules" because rule 04 is split into `04a-python-serving` and `04b-python-training`.
The on-disk canonical set is therefore 18 files.

### IDE Parity Matrix (manifest-enforced)

| Asset | Canonical (`agentic/`) | Devin | Cursor | Claude | Codex |
| ------- | ------------------------ | ------- | -------- | -------- | ------- |
| Rules | `agentic/rules/*.md` | `.devin/rules/*.md` mirror | `.cursor/rules/*.mdc` pointers | `.claude/rules/*.md` pointers | `.codex/rules/*.md` pointers |
| Skills | `agentic/skills/**/SKILL.md` | `.devin/skills/**/SKILL.md` mirror | `.agents/skills/<id>/SKILL.md` pointers (shared with Codex) | `.claude/skills/<id>/SKILL.md` pointers | `.agents/skills/<id>/SKILL.md` pointers (shared with Cursor) |
| Workflows | `agentic/workflows/*.md` | `.devin/workflows/*.md` mirror | `.cursor/commands/*.md` pointers | `.claude/commands/*.md` pointers | `.codex/workflows/*.md` pointers |
| Context | `AGENT_CONTEXT.md` | `.devin_context.md` | `.cursor_context.md` | `.claude_context.md` | `.codex_context.md` |

Every surface inherits the same **Agent Behavior Protocol** (AUTO/CONSULT/STOP) from `AGENTS.md`;
`scripts/validate_agentic_manifest.py --strict` rejects pointers that omit the canonical source or authority chain, and
fails if any `.devin/` mirror body drifts from its `agentic/` source.

## Template System

```text
templates/
├── service/            # Complete ML service boilerplate
│   ├── app/            # FastAPI serving layer
│   ├── src/            # Training, features, monitoring
│   ├── tests/          # Unit, integration, regression
│   ├── dvc.yaml        # DVC pipeline (validate → featurize → train → evaluate)
│   ├── .dvc/config     # DVC remote config (GCS/S3)
│   ├── Dockerfile
│   ├── pyproject.toml  # Modern Python project config
│   ├── requirements.txt
│   └── README.md
├── tests/integration/  # Integration test templates (health, predict, latency SLA)
├── k8s/                # K8s manifests (base/ + overlays/), SLO PrometheusRule
├── infra/              # Terraform IaC (GCP + AWS), docker-compose.mlflow.yml
├── scripts/            # new-service.sh, deploy.sh, promote_model.sh
├── cicd/               # GitHub Actions workflow templates
├── docs/               # ADR, runbook, model card, mkdocs.yml, CHECKLIST_RELEASE.md
├── common_utils/       # Shared utilities (seed, logging, persistence)
└── monitoring/         # Grafana dashboard + Prometheus alert templates
```

```text
docs/                   # Template-level architectural decisions
└── decisions/
    └── ADR-001-template-scope-boundaries.md  # Scope: LLM, multi-tenancy, Vault, compliance
```

## Operational Runbooks

`docs/runbooks/` carries one-time-setup and incident-response procedures
that are too long to live inline in a skill or workflow. Agents should
LINK to these (not paraphrase) when a task touches the corresponding
domain:

| Runbook | When agents reference it |
| --------- | -------------------------- |
| `gcp-wif-setup.md` | First GCP deploy from a new fork; rotating WIF provider; debugging `iam.workloadIdentityUser` failures |
| `aws-irsa-setup.md` | First AWS deploy from a new fork; debugging `AssumeRoleWithWebIdentity` failures; per-env IAM role creation |
| `terraform-state-bootstrap.md` | Setting up a new env's tf state bucket + DynamoDB lock; debugging `init -reconfigure` errors |
| `mcp-config-hygiene.md` | Adding a new MCP server; rotating MCP secrets; debugging `${VAR}` interpolation in mcp_config.json |
| `secret-rotation.md` | Quarterly rotation calendar; STOP-class operation; per-credential cadence |

These runbooks pair with the corresponding skills (`security-audit`,
`secret-breach-response`, `deploy-gke`, `deploy-aws`) — the SKILL is
the procedure, the runbook is the cloud-specific configuration detail.

## MCP Integrations

MCPs (Model Context Protocol servers) extend what agents can **do**, not just what they can read.
Install only MCPs that change agent capabilities for this stack. Skip MCPs for technologies not in this template.

### Recommended MCPs (high ROI for this template)

| MCP | Package / Server | What the agent gains |
| ----- | ----------------- | ---------------------- |
| **`github`** | Streamable HTTP — `api.githubcopilot.com/mcp/` + PAT | Read CI logs, PR status, issues — no copy-paste into chat |
| **`kubectl-mcp-server`** | `npx kubectl-mcp-server@latest --read-only` | Run `kubectl get/logs/describe` directly — skills execute instead of instruct |
| **`terraform-mcp-server`** | `npx terraform-mcp-server@latest` or `docker run hashicorp/terraform-mcp-server` | Registry lookup, `plan/validate` — workflow `/release` with real infra state |
| **`mcp-prometheus`** | varies by deployment | Query live metrics — `drift-detection` and `/incident` with real data |
| **`docker`** | `npx docker-mcp@latest` (`DOCKER_MCP_LOCAL=true`) | Inspect images/containers + container logs during `debug-ml-inference` / `security-audit`; never pushes registry images (D-19/D-30) |
| **`postgres`** | `npx @modelcontextprotocol/server-postgres@<pinned>` + read-only DSN | Read-only SQL against ground-truth / prediction-log DBs for closed-loop skills (D-20); writes/DDL stay STOP |

### Setup (per surface)

Each surface reads its own config file; the authoritative matrix lives in
`templates/config/mcp_registry.yaml` §surfaces. Render the JSON for your
surface from the registry — never commit credentials (D-17):

| Surface | Config file |
| --------- | ------------- |
| Devin Desktop | `~/.codeium/mcp_config.json` (legacy Windsurf: `~/.codeium/windsurf/mcp_config.json`) |
| Cursor | `~/.cursor/mcp.json` |
| Claude Code | `~/.claude.json` (user) or repo-scoped `.mcp.json` — copy `.mcp.json.example` and export the env vars it references |
| Codex | `.codex/mcp.json` (committed example: `.codex/mcp.example.json`) |

A committed, placeholder-only reference for Claude Code lives at
`.mcp.json.example`; tokens come from the environment, never the file.

**GitHub PAT scopes needed**: `repo`, `actions` (CI logs), `pull_requests`.
Create at: <https://github.com/settings/personal-access-tokens/new>

**Note**: `kubectl-mcp-server` uses your current `kubectl` context.
**Always run** `kubectl config current-context` before any cluster operation.
**`postgres` MCP**: connect with a read-only role; the DSN comes from the
secret store, not from the config file.

**`mcp-prometheus` is required for the Dynamic Behavior Protocol (ADR-010).**
Without it, the agent falls back to the static AGENTS.md mapping — which is
safe but less precise. The template's `common_utils/risk_context.py` helper
reads this server when available and transparently degrades to local-file
signals otherwise.

### Agent behavior with MCPs installed

When `mcp-github` is active: agents read CI failures directly — no need to paste logs into chat.
When `mcp-kubernetes` is active: skills `deploy-gke`/`deploy-aws` verify pod status after apply.
When `mcp-terraform` is active: skill `release-checklist` validates infra before deploying.
When `mcp-prometheus` is active: the Dynamic Behavior Protocol escalates
AUTO → CONSULT or CONSULT → STOP based on live risk signals (incident,
drift, off-hours, error budget) — see `common_utils/risk_context.py`.

Without MCPs: agents generate correct commands and instruct the human to run them (current behavior).
With MCPs: agents execute those commands directly and verify the results. Same invariants apply.

## AI Transparency

This template uses AI-assisted coding agents for code generation and boilerplate. All architectural decisions, system
design, trade-off analysis, and ADR documentation require human engineering judgment. AI tools accelerate throughput —
they don't replace the engineer's responsibility to calibrate solutions to the right scale.
