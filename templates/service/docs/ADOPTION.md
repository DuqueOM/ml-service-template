# Adoption Boundary & Non-Agentic On-Ramp

This document is the canonical answer to two questions a platform reviewer
asks when evaluating this template:

1. **"Is this ready for our org?"** — answered by the maturity matrix below.
2. **"Can my team adopt it without using AI agents?"** — answered by the
   make-target equivalents.

Authority: ADR-016 PR-R2-12.

---

## 1. Maturity matrix

Each capability is rated **per environment**. Definitions:

- **ready** — works out of the box after standard configuration; covered by
  contract tests; documented in a runbook
- **partial** — works but requires team-specific decisions or a follow-on PR
  before going live (see Notes column)
- **roadmap** — the template documents the intent and may include
  scaffolding, but you would build the production surface yourself

### Compute & networking

| Capability | dev | staging | prod | Notes |
| --- | --- | --- | --- | --- |
| GKE cluster + node pool split (system / workload) | ready | ready | ready | PR-A3 cluster defaults; workload taint enforced |
| EKS cluster + node group split (system / workload) | ready | ready | ready | Mirrors GCP; same taint contract |
| VPC networking (custom-mode + private subnets) | ready | ready | ready | `network_mode = "managed" \| "existing"` |
| Private GKE/EKS API endpoint | ready | ready | ready | GCP `enable_private_endpoint` and AWS private endpoint defaults are secure; dev may relax explicitly with authorized CIDRs |
| Workload Identity (GCP) / IRSA (AWS) | ready | ready | ready | D-18 enforced by contract tests; 5-identity split per ADR-017 |
| Deny-default NetworkPolicy | ready | ready | ready | `k8s/base/networkpolicy-deny-default.yaml` selects all pods |
| Cilium / advanced eBPF policies | roadmap | roadmap | roadmap | Out of scope; bring your own CNI overlay |

### Container & supply chain

| Capability | dev | staging | prod | Notes |
| --- | --- | --- | --- | --- |
| Multi-stage Dockerfile (slim runtime) | ready | ready | ready | Base image pinned by digest in staging/prod overlays |
| Init-container model fetch (D-11) | ready | ready | ready | Models never in the image |
| Cosign keyless signing | ready | ready | ready | OIDC via GitHub Actions |
| SBOM (Syft / CycloneDX) attestation | ready | ready | ready | D-30 enforced; cosign attest in deploy workflow |
| Kyverno admission policies (verify-images) | ready | ready | ready | `verifyImages` rule on prod namespace |
| SLSA L3 hermetic builds | roadmap | roadmap | roadmap | Out of scope; would require BuildKit Frontend changes |
| Image vulnerability scanning gate | partial | partial | partial | Trivy runs in CI; threshold (block on HIGH+) is team decision |

### Secrets & IAM

| Capability | dev | staging | prod | Notes |
| --- | --- | --- | --- | --- |
| Secrets via cloud manager (GSM/ASM) | ready | ready | ready | Per-service IAM binding only |
| `common_utils.secrets.get_secret()` loader | ready | ready | ready | D-17 enforced by policy test |
| Secret rotation procedure | ready | ready | ready | `/secret-breach` workflow + skill |
| HashiCorp Vault integration | roadmap | roadmap | roadmap | Deferred by ADR-001 (revisit if IRSA/WI insufficient) |
| 5-identity IAM split (ci/deploy/runtime/drift/retrain) | ready | ready | ready | D-31 enforced by policy test on scaffolded output |

### ML quality & observability

| Capability | dev | staging | prod | Notes |
| --- | --- | --- | --- | --- |
| Pandera schema validation in serving + drift | ready | ready | ready | PR-R2-4; second validation wall |
| MLflow tracking + model registry | ready | ready | ready | Self-hosted on K8s; CMEK-backed |
| Quality gates on promotion (DIR ≥ 0.80, primary metric, latency) | ready | ready | ready | PR-B1; per-service `quality_gates.yaml` |
| Drift detection (PSI quantile-based) | ready | ready | ready | D-08 enforced; CronJob with heartbeat alert |
| Sliced performance monitoring (concept drift) | ready | ready | ready | PR-C2; ground-truth join via `entity_id` |
| Prediction logging | ready | ready | ready | D-20/D-21/D-22 enforced; non-blocking + buffered |
| Multi-window burn-rate alerts | ready | ready | ready | PR-C2; mandatory `runbook_url` |
| SLO error-budget tracking | ready | ready | ready | Recording rules + alert routes |
| Champion/challenger online experiments | partial | partial | partial | Argo Rollouts pattern documented; AnalysisTemplates require per-service tuning |
| Feature store integration | roadmap | roadmap | roadmap | PSI baseline + DVC suffices for 2-3 model scale (ADR-001) |

### Delivery

| Capability | dev | staging | prod | Notes |
| --- | --- | --- | --- | --- |
| 4-job deploy chain (build → dev → staging → prod) | ready | ready | ready | D-26 enforced; GitHub Environment Protection |
| `terraform plan` nightly drift detection | ready | ready | ready | PR-A4; opens dedup'd `infra-drift` issue |
| Argo Rollouts canary template | partial | partial | partial | AnalysisTemplate scaffolded; metric thresholds per service |
| Rollback runbook + automation | ready | ready | ready | `/rollback` workflow + `make rollback` |
| Reproducible drills (drift, deploy-degraded) | ready | ready | ready | PR-C3; evidence under `docs/runbooks/drills/` |

### Governance

| Capability | dev | staging | prod | Notes |
| --- | --- | --- | --- | --- |
| ADRs for non-trivial decisions | ready | ready | ready | 35 ADRs cover all design choices |
| Audit trail (append-only `ops/audit.jsonl`) | ready | ready | ready | ADR-014; CLI `scripts/audit_record.py` |
| Anti-pattern policy tests on scaffolded output | ready | ready | ready | PR-R2-11; D-01..D-35 enforced |
| Agent risk-context dynamic mode (AUTO→CONSULT→STOP) | ready | ready | ready | ADR-014; risk signals from Prometheus |
| SOC2 / HIPAA controls | roadmap | roadmap | roadmap | Organizational, not template (ADR-001) |

---

## 2. Non-agentic on-ramp

Every workflow we ship has either:

- a `make` target that runs the equivalent procedure end-to-end, or
- an explicit doc/runbook reference for the team to follow manually.

Teams that don't operate with AI assistants can adopt the template without
inheriting the agentic surface.

### Workflow → make-target / runbook map

| Slash workflow | Make equivalent | Runbook reference |
| --- | --- | --- |
| `/new-service` | `make new-service NAME=<PascalCase> SLUG=<snake_case>` | `templates/scripts/new-service.sh --help` |
| `/scaffold-update` | `copier update` (manual) | `agentic/workflows/scaffold-update.md` |
| `/eda` | `make eda` (runs the 6-phase pipeline) | `eda/README.md` |
| `/drift-check` | `make drift-check` (runs `scripts/drills/run_drift_drill.py`) | `docs/runbooks/drift-detection.md` |
| `/retrain` | `make retrain` (invokes training pipeline + quality gates) | `docs/runbooks/model-retrain.md` |
| `/load-test` | `make load-test` (Locust headless 60s) | `tests/load_test.py` docstring |
| `/release` | `make release-checklist` (prints the canonical checklist) | `docs/runbooks/release-checklist.md` |
| `/rollback` | `make rollback REV=<n>` (Argo Rollouts abort + kubectl undo) | `docs/runbooks/rollback.md` |
| `/incident` | `make incident-runbook` (prints incident response steps) | `docs/runbooks/incident-response.md` |
| `/performance-review` | `make performance-review` (sliced metrics + ground truth) | `docs/runbooks/performance-review.md` |
| `/cost-review` | `make cost-review` (cloud billing pull + budget compare) | `docs/runbooks/cost-review.md` |
| `/new-adr` | `make new-adr TITLE='<title>'` | `docs/decisions/adr-template.md` |
| `/secret-breach` | `make secret-breach-check` (gitleaks scan) + escalation runbook | `docs/runbooks/secret-breach.md` |
| `/scaffold-update` | `make scaffold-update` (copier update) | `MIGRATION.md` |

### Skill → CLI / runbook map

Skills are agent reasoning bundles, so their non-agentic equivalent is the
underlying CLI tool plus the corresponding human runbook:

| Skill | CLI / runbook |
| --- | --- |
| `new-service` | `templates/scripts/new-service.sh` |
| `scaffold-update` | `copier update` (manual; see `agentic/workflows/scaffold-update.md`) |
| `deploy-gke` / `deploy-aws` | `scripts/deploy.sh` + `docs/runbooks/deploy-{gke,aws}.md` |
| `rollback` | `make rollback` + `docs/runbooks/rollback.md` |
| `drift-detection` | `scripts/drills/run_drift_drill.py` + `docs/runbooks/drift-detection.md` |
| `model-retrain` | `make retrain` + `docs/runbooks/model-retrain.md` |
| `eda-analysis` | `eda/eda_pipeline.py` + `eda/README.md` |
| `cost-audit` | `make cost-review` + `docs/runbooks/cost-review.md` |
| `security-audit` | `make security-audit` (gitleaks + bandit + trivy) |
| `secret-breach-response` | `make secret-breach-check` + `docs/runbooks/secret-breach.md` |
| `rule-audit` | `make audit-rules` (validates AGENTS.md invariants D-01..D-35 are documented) |
| `debug-ml-inference` | `docs/runbooks/debug-ml-inference.md` (manual procedure; no CLI equivalent — pure RCA reasoning) |
| `performance-degradation-rca` | `docs/runbooks/performance-degradation-rca.md` (manual RCA procedure) |
| `concept-drift-analysis` | `make performance-review` + `docs/runbooks/concept-drift-analysis.md` |
| `release-checklist` | `make release-checklist` |
| `batch-inference` | `make batch-inference DATA=<path>` (target in the service `Makefile`) |

### Operational reality check

If your team adopts the template **without agents**, you lose:

- automatic mode escalation on incidents (`AUTO→CONSULT→STOP` — you decide
  manually based on the same signals from Prometheus)
- audit trail entry generation (you invoke `scripts/audit_record.py` from
  your runbooks instead of the agent doing it transparently)
- proactive risk-context queries before destructive operations

You **do not** lose:

- any of the production invariants (D-01..D-35 are codified in tests, not
  agent behavior)
- contract tests (run on every PR via the same CI workflows)
- supply-chain security (Cosign + SBOM + Kyverno are pipeline, not agent)
- monitoring / alerting / drift detection (CronJobs + Prometheus, not
  agents)
- 4-job deploy chain (GitHub Environments + reviewer approval, not agents)

The agentic surface is a **productivity multiplier** for teams that want
it; it is not a load-bearing component of the template's safety guarantees.

---

## 3. What this template does NOT claim

To prevent over-promising:

- **Multi-region active-active**: out of scope. The template assumes one
  active region per service per cloud. Cross-region failover is your
  organization's choice.
- **Compliance certifications**: SOC2, HIPAA, FedRAMP — these are
  organizational programs that consume the template's evidence (audit
  trail, signed images, RBAC) but aren't the template itself.
- **Zero-downtime database migrations**: out of scope. The template's
  4-job deploy chain handles stateless service rollouts; database schema
  evolution is your team's discipline.
- **Built-in feature store**: ADR-001 deferred this until 5+ services
  share features. PSI baselines + DVC remotes serve 2-3 services well.
- **Prompt engineering for LLM services**: this template targets
  classical ML serving (sklearn/XGBoost/LightGBM). LLM serving has
  different invariants (cold-start latency, token streaming, GPU pinning)
  not covered here.
- **Mobile / edge inference**: out of scope. The template assumes
  Kubernetes serving with HPA-driven horizontal scale.

If your use case lives in any of these gaps, the template is still useful
as a starting point, but expect to do additive work rather than just
configuration.

---

## 6. Compliance gap analysis

This section documents which controls in common compliance regimes the
template materially supports vs. those that are organizational and out
of scope. The template is **not certified** under any program; it
provides evidence that organizations can compose into their own audits.

Authority: R4 audit M4, ADR-020 §S2-2.

### 6.1 GDPR (Regulation (EU) 2016/679)

| Control area | Coverage | Evidence in template | Adopter responsibility |
| --- | --- | --- | --- |
| Article 5(1)(a) lawful processing | Out of scope | None | Define lawful basis per service domain |
| Article 5(1)(c) data minimization | Partial | Pandera schema + `eda/` baseline minimization heuristic | Per-service field selection review |
| Article 5(1)(f) integrity / confidentiality | Covered | Cosign signing + Kyverno admission + IRSA / WI + secret manager | Cluster posture + key rotation cadence |
| Article 17 right to erasure | Out of scope | None | Per-service data retention + deletion pipeline |
| Article 25 data protection by design | Covered | `memory_redaction.py` PII pipeline; `prediction_logger.py` redaction hooks | Define which features are personal data |
| Article 32 security of processing | Covered | Trivy + gitleaks + signed images + remote state encryption | Org-level vulnerability management program |
| Article 33 breach notification | Out of scope | `docs/runbooks/secret-rotation.md` covers credential rotation, not subject notification | Org-level DPO + 72-hour notification process |

### 6.2 SOC 2 Type II (AICPA Trust Services Criteria)

| Control area | Coverage | Evidence in template | Adopter responsibility |
| --- | --- | --- | --- |
| CC6.1 logical access controls | Covered | IRSA / WI per-purpose identities (D-31); RBAC manifests | IdP integration + access reviews |
| CC6.6 environmental controls | Covered | PSS labels per environment (D-29); deny-default NetworkPolicy | Cluster-level firewall + WAF |
| CC7.1 system monitoring | Covered | Prometheus + Grafana + AlertManager wiring | 24/7 oncall rotation + escalation matrix |
| CC7.2 change management | Covered | `docs/RELEASING.md` + ADR-026 rulesets (`make setup-github`) + `pr-evidence-check.yml` | PR review SLA + approver matrix |
| CC7.3 system operations | Covered | `docs/runbooks/`; audit trail (`ops/audit.jsonl`) | Auditor access to evidence |
| CC8.1 change deployment | Covered | dev → staging → prod gate (D-26); digest pinning; signed images | Reviewer training |
| A1.2 availability monitoring | Covered | SLO PrometheusRules + multi-window burn-rate alerts | SLO target negotiation per service |

### 6.3 ISO/IEC 27001:2022

| Control area | Coverage | Evidence in template | Adopter responsibility |
| --- | --- | --- | --- |
| A.5.7 threat intelligence | Out of scope | None | Org-level threat intel feed |
| A.5.30 ICT readiness for business continuity | Partial | `docs/runbooks/` cover deploy + rollback; backups out of scope | DR drills + RPO / RTO targets |
| A.8.3 information access restriction | Covered | RBAC + NetworkPolicy + IRSA / WI | IdP federation |
| A.8.10 information deletion | Out of scope | None | Per-service retention + deletion pipeline |
| A.8.16 monitoring activities | Covered | Prometheus + AlertManager + audit trail | Detection rule tuning |
| A.8.24 use of cryptography | Covered | KMS-backed remote state + Sigstore signing chain | Key custodian assignment |
| A.8.28 secure coding | Covered | bandit + mypy + pre-commit + 14 hooks | Secure coding training |
| A.8.32 change management | Covered | `docs/RELEASING.md` + `MIGRATION.md` + ADR discipline | Change advisory board (if required) |

### 6.4 HIPAA Security Rule (45 CFR §164.302–.318)

| Safeguard | Coverage | Evidence in template | Adopter responsibility |
| --- | --- | --- | --- |
| §164.308 administrative safeguards | Out of scope | None | Workforce training + risk analysis program |
| §164.310 physical safeguards | N/A — cloud-managed | Cluster runs in cloud-provider physical secure facilities | Cloud BAA negotiation |
| §164.312(a) access control | Covered | Per-purpose IRSA / WI identities, RBAC, audit trail | IdP federation + role assignment |
| §164.312(b) audit controls | Covered | `ops/audit.jsonl` append-only + GHA step summaries + `VALIDATION_LOG.md` | Long-term archival of audit logs |
| §164.312(c) integrity | Covered | Digest pinning + Cosign signing + Kyverno admission | None |
| §164.312(d) person / entity authentication | Out of scope at template layer | Service-level auth via OIDC; user authn is adopter problem | IdP integration + MFA |
| §164.312(e) transmission security | Partial | Cluster mTLS via Istio recommended in roadmap; TLS at ingress | Per-cloud ingress TLS configuration |
| §164.404 breach notification | Out of scope | None | Org-level breach response program |

### 6.5 Out-of-scope by template philosophy (ADR-001)

The template explicitly does **not** address:

- **PCI DSS** — payment card data should not transit a generic ML service.
  The template offers no specific PCI controls.
- **FedRAMP** — federal authorization requires hermetic builds (SLSA L3+),
  org-level personnel screening, and continuous monitoring beyond the
  template's scope.
- **HITRUST CSF** — derived framework; gaps inherited from §6.4 above.

### 6.6 How adopters use this analysis

1. Pick the regime your org targets.
2. For each "Covered" row, link the cited evidence file in your audit
   binder (`docs/runbooks/`, `ops/audit.jsonl`, contract test names).
3. For each "Partial" / "Out of scope" row, document the organizational
   process you have in place. The template does not pretend to cover it.
4. Re-run this analysis at every MAJOR release per `docs/RELEASING.md` —
   new contract changes can shift coverage.

---

## 7. Disclosure SLA (R4 audit M4 clarification)

The vulnerability response timeline in
[`SECURITY.md`](https://github.com/DuqueOM/ml-service-template/blob/main/SECURITY.md)
is **operational** for this template:

- The maintainer commits to the response times listed (Critical 48h /
  High 7d / Medium 14d / Low 30d).
- The maintainer is a single individual and SLA depends on
  availability; for organizations adopting the template, **fork and
  assign internal maintainers** so the SLA is sized to your operational
  reality.
- Vulnerability findings that affect already-deployed adopters trigger a
  CVE assignment via GitHub's private vulnerability reporting before any
  public disclosure.
- After resolution, a redacted post-mortem is published in
  `docs/incidents/` (existing convention).
