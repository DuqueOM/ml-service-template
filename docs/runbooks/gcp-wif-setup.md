# Runbook — GCP Workload Identity Federation (WIF) setup

One-time setup for the GitHub Actions → GCP authentication used by
`templates/service/.github/workflows/deploy-gcp.yml`, `deploy-common.yml`, `drift-detection.yml`,
and `retrain-service.yml`. After this runbook, **no static service-account
JSON keys live in the repo or in GitHub Secrets**.

Authority: D-17 (no hardcoded credentials), D-18 (cloud-native delegation),
ADR-014 §3.1, gap #02.

## Prerequisites

- GCP project with billing enabled
- IAM permission to create Workload Identity Pools (`roles/iam.workloadIdentityPoolAdmin`)
- IAM permission to create service accounts and bindings (`roles/iam.serviceAccountAdmin`)
- The GitHub repo's `org/repo` slug
- `gcloud` CLI authenticated as a project admin

## Variables to substitute

```bash
PROJECT_ID="your-gcp-project"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
POOL_ID="github"                              # one pool per repo or per org is fine
PROVIDER_ID="github"
GH_OWNER="DuqueOM"                            # change to your org / user
GH_REPO="ml-service-template"        # change to your repo
SA_NAME="ci-deployer"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
```

## 1 — Create the Workload Identity Pool + Provider

```bash
# Pool: a logical group of external identities allowed to impersonate SAs.
gcloud iam workload-identity-pools create "$POOL_ID" \
  --location=global \
  --display-name="GitHub Actions"

# Provider: trusts GitHub's OIDC token issuer.
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --location=global \
  --workload-identity-pool="$POOL_ID" \
  --display-name="GitHub provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GH_OWNER}/${GH_REPO}'"
```

**Why the attribute-condition**: it restricts WIF to ONLY this repo. Without
it, any GitHub repo could impersonate the SA. This is the most common
misconfiguration.

## 2 — Create the deployer service account + grant minimum roles

```bash
gcloud iam service-accounts create "$SA_NAME" \
  --description="Deploy + manage ML services from GitHub Actions" \
  --display-name="CI Deployer"

# Minimum roles for the deploy chain. Customize per service.
for ROLE in \
    roles/artifactregistry.writer \
    roles/container.developer \
    roles/storage.objectAdmin \
    roles/iam.serviceAccountTokenCreator; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --condition=None
done
```

**Principle of least privilege**: every role granted here should map to a
concrete operation in `deploy-gcp.yml`. If you don't know why a role is
needed, remove it and let the next deploy fail with the missing IAM error
— that's faster than over-granting.

## 3 — Allow GitHub OIDC to impersonate the SA

```bash
WIF_PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GH_OWNER}/${GH_REPO}"

gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="$WIF_PRINCIPAL"
```

## 4 — Configure the GitHub Variables (NOT Secrets)

Go to: `https://github.com/${GH_OWNER}/${GH_REPO}/settings/variables/actions`

Add **repository variables** (NOT secrets — these are not sensitive):

| Variable | Scope | Value |
| ---------- | ------- | ------- |
| `GCP_WIF_PROVIDER` | repository | `projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}` |
| `GCP_SERVICE_ACCOUNT` | repository | `${SA_EMAIL}` |
| `GCP_PROJECT_ID` | repository | `${PROJECT_ID}` |
| `GCP_REGION` | repository | e.g. `us-central1` |
| `GKE_DEV_CLUSTER` | repository | the GKE cluster name for dev |
| `GKE_STAGING_CLUSTER` | repository | the GKE cluster name for staging |
| `GKE_PROD_CLUSTER` | repository | the GKE cluster name for production |

All of these are repository-scoped. `deploy-gcp.yml` reads them in its
top-level `env`, its build job and its caller jobs, none of which run in an
environment, so an environment-scoped value would reach them as an empty
string. The scheduled workflows authenticate as their own
service accounts; the full table is in `docs/environment-promotion.md`.

If the deploy chain still references `secrets.GCP_SA_KEY` after this runbook,
**delete that secret from the repo** — leaving it around invites someone to
re-introduce static-key auth in a later PR.

## 5 — Verify

Trigger any workflow that authenticates to GCP (e.g. `deploy-gcp.yml` via
`workflow_dispatch`). The auth step should print:

```text
Successfully authenticated to GCP via Workload Identity Federation.
```

If it fails with `unable to acquire impersonated credentials`, the most
common causes:

1. The provider's `attribute-condition` does not match the repo. Re-check
   step 1.
2. The SA does not have `roles/iam.workloadIdentityUser` granted to the WIF
   principal. Re-check step 3.
3. The workflow is missing `permissions: id-token: write` at the job level.
   Every job that calls `google-github-actions/auth` needs it.

## Related

- ADR-014 §3.1 — Phase 3 supply-chain governance
- Invariants D-17, D-18
- `templates/service/.github/workflows/deploy-common.yml` — consumer
- `templates/service/.github/workflows/deploy-gcp.yml` — chain entry point
- AWS counterpart: `docs/runbooks/aws-irsa-setup.md` (existing, similar shape)
- Google docs: <https://cloud.google.com/iam/docs/workload-identity-federation>
