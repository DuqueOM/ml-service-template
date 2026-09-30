# Repository structure

This page maps the repository, and it is written so that it cannot quietly go
stale. The version it replaces lived in the README as a directory tree inside a
code block, and it kept describing seven top-level template directories —
cicd, infra, k8s base, monitoring and more — for months after ADR-030 moved
them under `templates/service/`. Nothing checks a line of a drawn tree. Every path below
is a code span instead, and `scripts/check_doc_path_refs.py` fails CI when one
of them stops resolving.

Two trees live here. The **template repository** is what you are reading; the
**payload** under `templates/service/` is what Copier renders into a generated
service, and it is self-contained after generation.

## The payload — what a generated service contains

Copier renders every file under `templates/service/`; `_templates_suffix: ""`
in `copier.yml` makes each one a template. `{@ service_slug @}` becomes your
package name.

| Path | What it holds |
| --- | --- |
| `templates/service/app/` | FastAPI entrypoint (`main.py`) and application (`fastapi_app.py`), request schemas, the lazy Pandera schema resolver |
| `templates/service/src/` | The Python package: training, evaluation, monitoring (drift, ground truth, sliced performance), fairness, explainability |
| `templates/service/common_utils/` | Shared runtime utilities: secrets, auth, prediction logger, risk context, telemetry, tracing, model persistence |
| `templates/service/configs/` | Quality gates and their schema, slices, champion/challenger and ground-truth configuration, stack profiles |
| `templates/service/tests/` | Unit, contract, integration and policy tests for the generated service |
| `templates/service/k8s/base/` | Deployment, HPA, PDB, NetworkPolicies, RBAC, CronJobs, SLO and performance rules |
| `templates/service/k8s/overlays/` | An overlay for each cloud and environment, plus `batch-only` |
| `templates/service/k8s/components/` | Opt-in Kustomize components, such as the edge-protection layers |
| `templates/service/infra/terraform/` | GCP, AWS and Cloudflare root modules, with bootstrap layers and per-environment backend configs |
| `templates/service/.github/workflows/` | CI, the deploy chain, drift detection, retraining, the nightly Terraform plan, Dependabot auto-merge |
| `templates/service/monitoring/` | Prometheus rules, AlertManager, Grafana dashboards, Promtail |
| `templates/service/eda/` | The EDA pipeline and its artifact contract |
| `templates/service/docs/` | The service's own ADRs, runbooks, model-card and release-checklist templates |
| `templates/service/agentic/` | A byte-identical mirror of the repository's canonical `agentic/` tree |
| `templates/service/Dockerfile` | The serving image; models arrive at runtime through an init container, never baked in |

## The template repository — what builds and governs the payload

| Path | What it holds |
| --- | --- |
| `templates/scripts/new-service.sh` | The scaffolder; it delegates to `copier copy` (D-33) |
| `templates/config/` | The agentic manifest, context schemas, the MCP registry, CI-autofix and model-routing policies |
| `templates/governance/` | The promotion workflow and roles an adopter copies into their own repository |
| `templates/k8s/policies/` | Kyverno ClusterPolicies: image digest and signature verification, Pod Security Standards |
| `templates/tests/` | Tests of the template itself: governance contracts, infrastructure, unit |
| `examples/minimal/` | A working fraud-detection demo: train, serve, test and drift-check in five minutes |
| `scripts/` | The deterministic gates `make verify` runs, plus release and audit tooling |
| `agentic/` | Canonical rules, skills and workflows — the only place humans edit agent policy |
| `docs/decisions/` | Architecture decision records |
| `docs/runbooks/` | Setup and operational runbooks |
| `releases/` | Release notes, one per version |
| `.github/workflows/` | This repository's own CI |

## Generated agent adapters

Each supported IDE or agent reads a generated adapter, produced from
`agentic/` by `scripts/sync_agentic_adapters.py` and checked for drift in CI.
Which tools are supported, and how many files each receives, is an inventory
that changes whenever a tool does, so it is kept in one place under
[AGENTS.md § Multi-IDE Support](../AGENTS.md#multi-ide-support), where the
coherence gate reconciles every count against the directories.
