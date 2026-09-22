# Environment Promotion (dev → staging → prod)

**Status**: implemented in `deploy-gcp.yml`, `deploy-aws.yml`, and the
reusable `deploy-common.yml` (v1.7.1). Anti-pattern **D-26** codifies the
gap: changes reaching production without passing through staging
validation.

This document explains how to configure the GitHub Environment Protection
Rules so the Agent Behavior Protocol's **AUTO/CONSULT/STOP** modes are
enforced at the GitHub level, not just at the agent layer.

## Promotion chain

```text
  push / tag
       │
   [build]          ← produces signed images once
       │
   [deploy-dev]     ← AUTO     — every push to main runs this
       │
   [deploy-staging] ← CONSULT  — requires 1 reviewer (tech lead)
       │
   [deploy-prod]    ← STOP     — requires 2 reviewers + wait_timer
                                 + version-tag branch rule
```

## GitHub Environment Protection Rules to configure

The YAML only DECLARES which environment each job targets. The PROTECTION
lives in `Settings → Environments` of the repository. Configure these
environments manually once per repo (or via `gh api`).

### gcp-dev / aws-dev

| Setting | Value |
| --- | --- |
| Required reviewers | none |
| Wait timer | 0 |
| Deployment branches | All branches |

### gcp-staging / aws-staging

| Setting | Value |
| --- | --- |
| Required reviewers | 1 (a team member with `@MLTechLeads` or equivalent) |
| Wait timer | 0 |
| Deployment branches | `main` + version tags |

### gcp-production / aws-production

| Setting | Value |
| --- | --- |
| Required reviewers | 2 (must include `@PlatformEngineer` or equivalent) |
| Wait timer | 5 minutes |
| Deployment branches | Version tags ONLY: `v*` |

## Secrets/vars layout

Per ADR-014 §3.1 and invariant D-18, **no static cloud credentials**
live in this repo or in GitHub Secrets. Both clouds federate identity
from GitHub OIDC tokens at workflow runtime.

### GCP — Workload Identity Federation (NO static service account keys)

- Setup: `docs/runbooks/gcp-wif-setup.md`
- The deploy chain authenticates via `google-github-actions/auth@v2`
  exchanging the GitHub OIDC token for a federated SA. NO `GCP_SA_KEY`
  secret is used or required.
- If you find `GCP_SA_KEY` referenced anywhere in this repo, it is a
  bug — open an issue. The previous template iteration leaked this
  pattern; it has been removed.

### AWS — IAM Identity Provider + IRSA (per-env federated role)

- Setup: `docs/runbooks/aws-irsa-setup.md`
- One IAM role per env (`github-actions-ci-deployer-{dev,staging,prod}`)
  trusts the GitHub OIDC provider with a `sub:` condition restricting
  to this repo and (for prod) only main + version tags.
- Terraform's trust policy allows the subject a job in that environment
  presents, `repo:<owner>/<repo>:environment:aws-<environment>`.
- The role ARN IS sensitive (it controls deploy access to that env)
  and lives in **Environment Secrets**:
  `Settings → Environments → {aws-dev,aws-staging,aws-production}` →
  add secret `AWS_ROLE_ARN`. Each env's role has the smallest IAM
  policy needed for its scope.

### Where each value goes

GitHub exposes an **environment**-scoped secret or variable only to a job that
declares that `environment:`. Every other job sees repository-scoped values
only. That includes a deploy workflow's build job and the caller jobs that pass
`cluster_name` to `deploy-common.yml`. For them an environment-scoped value
arrives as an empty string. GitHub does not fall back between `secrets` and
`vars` either. `scripts/check_deploy_contract_documented.py` fails CI when a
workflow and the tables below disagree on a name, its channel or its scope.

The only environment-scoped value is the per-environment deploy role.
Everything else is repository-scoped. The scheduled workflows use their own
purpose-named identities, because they run outside any environment and must not
reuse the deploy role (ADR-017, D-31).

| Secret | Scope | Read by | Value |
| --- | --- | --- | --- |
| `AWS_ROLE_ARN` | environment: one per `aws-dev`, `aws-staging`, `aws-production` | `deploy-common.yml` deploy job | Terraform output `deploy_role_arn` for that environment |
| `AWS_BUILD_ROLE_ARN` | repository | `deploy-aws.yml` build job, to push to ECR | Terraform output `ci_role_arn`, or a dedicated build role |
| `AWS_CI_ROLE_ARN` | repository | `terraform-plan-nightly.yml` | Terraform output `ci_role_arn`. That role carries AWS-managed `ReadOnlyAccess` so `plan` can refresh every managed resource; a plan that cannot read reports "no changes" for what it could not see |
| `AWS_DRIFT_ROLE_ARN` | repository | `drift-detection.yml` when `DATA_BUCKET_KIND=s3` | Terraform output `drift_ci_role_arn` — read on the data bucket, trusted for this repository's GitHub OIDC subjects |
| `AWS_RETRAIN_ROLE_ARN` | repository | `retrain-service.yml` when the buckets are `s3` | Terraform output `retrain_ci_role_arn` — read on data, write on the models prefix, no delete |
| `MLFLOW_TRACKING_URI` | repository | `retrain-service.yml` | tracking server URL; a secret because it may embed credentials |
| `INFRACOST_API_KEY` | repository, optional | `terraform-plan-nightly.yml` | cost breakdown; the step is skipped when absent |
| `CODECOV_TOKEN` | repository, optional | `ci.yml` | coverage upload |

| Variable | Scope | Read by | Value |
| --- | --- | --- | --- |
| `GCP_WIF_PROVIDER` | repository | every workflow that authenticates to GCP | Terraform output `github_workload_identity_provider`. Terraform creates the pool and provider when `github_repo` is set, with an attribute condition pinning this repository |
| `GCP_SERVICE_ACCOUNT` | repository | `deploy-gcp.yml` build job, `deploy-common.yml` | Terraform output `deploy_service_account_email` |
| `GCP_CI_SERVICE_ACCOUNT` | repository | `terraform-plan-nightly.yml` | Terraform output `ci_service_account_email`. That account holds `roles/viewer` so `plan` can refresh |
| `GCP_DRIFT_SERVICE_ACCOUNT` | repository | `drift-detection.yml` when `DATA_BUCKET_KIND=gcs` | Terraform output `drift_service_account_email`; Terraform also grants this repository's principalSet `roles/iam.workloadIdentityUser` on it |
| `GCP_RETRAIN_SERVICE_ACCOUNT` | repository | `retrain-service.yml` when the buckets are `gcs` | Terraform output `retrain_service_account_email`, with the same impersonation grant |
| `GCP_PROJECT_ID` | repository | `deploy-gcp.yml` top-level `env` (registry URL), `terraform-plan-nightly.yml` | the project ID |
| `GCP_REGION` | repository | GCP workflows | e.g. `us-central1` |
| `GKE_DEV_CLUSTER`, `GKE_STAGING_CLUSTER`, `GKE_PROD_CLUSTER` | repository | `deploy-gcp.yml` caller jobs | GKE cluster names |
| `AWS_REGION` | repository | AWS workflows | e.g. `us-east-1` |
| `AWS_REGISTRY_ID` | repository | `deploy-aws.yml` build job | the ECR registry account |
| `EKS_DEV_CLUSTER`, `EKS_STAGING_CLUSTER`, `EKS_PROD_CLUSTER` | repository | `deploy-aws.yml` caller jobs | EKS cluster names |
| `PROMETHEUS_URL` | repository, optional | `deploy-common.yml` dynamic risk mode | degrades gracefully when absent (ADR-010, ADR-014 §4.2) |
| `DATA_BUCKET`, `DATA_BUCKET_KIND` | repository | `drift-detection.yml`, `retrain-service.yml` | bucket name; `gcs` or `s3` |
| `MODEL_BUCKET`, `MODEL_BUCKET_KIND` | repository | `retrain-service.yml` | bucket name; `gcs` or `s3` |

## Branch-based guards (defense in depth)

Even with Environment Protection Rules, the workflow files add a second
guard via `if:` conditions:

- Feature branches → only `deploy-dev` runs
- `main` branch → dev + staging run; prod is gated (`if: startsWith(github.ref, 'refs/tags/v')` is false)
- Version tag `v1.2.3` → dev + staging + prod all run (but prod still needs reviewer approval)

This prevents a misconfigured environment (e.g., someone removed the
`deployment_branches` rule) from letting a feature branch reach prod.

## Overlay-per-environment layout

Each environment has its own Kustomize overlay so resource sizing and
namespaces differ:

```text
k8s/
├── base/                    # Deployment, Service, HPA, PDB, etc.
└── overlays/
    ├── gcp-dev/             # small replicas, dev namespace
    ├── gcp-staging/
    ├── gcp-prod/            # prod sizing, Kyverno policies, etc.
    ├── aws-dev/
    ├── aws-staging/
    └── aws-prod/
```

## How this maps to the Agent Behavior Protocol

The workflow's structure enforces AGENTS.md invariants at CI time:

| Env | GitHub Protection | Agent mode | Why |
| --- | --- | --- | --- |
| dev | none | AUTO | Low blast radius; reversible |
| staging | 1 reviewer | CONSULT | Validates pre-prod; single sign-off sufficient |
| prod | 2 reviewers + wait_timer | STOP | Customer-facing; requires deliberation + branch gate |

Agents proposing a deploy via `/release` or `deploy-gke` skill do NOT
bypass these gates — the gate lives at the GitHub API level, not at the
agent layer.

## Migration from pre-v1.7.1 setup

Services created with v1.7.0 or earlier have flat `production-gcp` /
`production-aws` environments and tag-triggered deploys. Migration:

1. Create the 6 new environments in repo Settings (dev/staging/prod × gcp/aws)
2. Move `AWS_ROLE_ARN` into each `aws-*` environment and keep every other value repository-scoped, per the tables above
3. Adopt the new `deploy-*.yml` (diffable — minimal user edits)
4. Add `k8s/overlays/*-dev` and `*-staging` overlays
5. Delete the old flat environments AFTER the first successful pipeline

No data or runtime migration required — images continue to build and
deploy; only the promotion chain changes.

## See also

- ADR-011 — Environment Promotion Gates (authorship & trade-offs)
- `agentic/rules/05-github-actions.md` — D-26 enforcement
- `agentic/skills/rollback/SKILL.md` — emergency path out of production
