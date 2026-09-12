# Runbook — AWS IRSA + GitHub OIDC setup

One-time setup for the GitHub Actions → AWS authentication used by
`templates/service/.github/workflows/deploy-aws.yml`, `deploy-common.yml`, `drift-detection.yml`,
and the in-cluster IRSA bindings used by every scaffolded service.

After this runbook, **no long-lived AWS access keys live in the repo
or in GitHub Secrets**. CI authenticates via GitHub OIDC, and pods
authenticate via IRSA (IAM Roles for Service Accounts).

Authority: D-17 (no hardcoded credentials), D-18 (cloud-native
delegation), ADR-014 §3.1, gap #02 (AWS half — companion to
`gcp-wif-setup.md`).

## Two distinct trust chains

This runbook covers BOTH:

1. **GitHub Actions → AWS** (CI/CD identity)
   - GitHub mints OIDC token → AWS IAM Identity Provider trusts it →
     CI assumes a role with deploy permissions.
2. **EKS Pod → AWS** (workload identity, IRSA)
   - Pod's ServiceAccount has annotation `eks.amazonaws.com/role-arn` →
     EKS pod-identity-webhook injects creds → containers call AWS APIs
     as that role, no key material.

Both flows use OIDC; they are separate IAM Identity Providers.

## Prerequisites

- AWS account with admin IAM access (`iam:CreateOpenIDConnectProvider`,
  `iam:CreateRole`, `iam:PutRolePolicy`)
- An EKS cluster (for the IRSA half — skip the EKS section if you only
  use the CI half right now)
- GitHub repo `org/repo` slug
- `aws` CLI configured against the target account

## Variables to substitute

```bash
ACCOUNT_ID="123456789012"                        # your AWS account
REGION="us-east-1"
GH_OWNER="DuqueOM"
GH_REPO="ml-service-template"
EKS_CLUSTER="ml-prod"                            # only for IRSA half
EKS_OIDC_URL="$(aws eks describe-cluster --name $EKS_CLUSTER \
                 --query 'cluster.identity.oidc.issuer' --output text \
                 | sed 's|https://||')"
CI_ROLE_NAME="github-actions-ci-deployer"
SVC_ROLE_NAME="ml-service-runtime"
```

## Part A — GitHub Actions → AWS (CI/CD identity)

### A.1 Create the GitHub OIDC Identity Provider

```bash
aws iam create-open-id-connect-provider \
  --url "https://token.actions.githubusercontent.com" \
  --client-id-list "sts.amazonaws.com" \
  --thumbprint-list "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
```

The thumbprint above is the canonical one published by GitHub.
If it changes, AWS docs show the current value.

### A.2 Create the deployer role with a repo-scoped trust policy

Save as `trust-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:GH_OWNER/GH_REPO:*"
        }
      }
    }
  ]
}
```

Substitute `ACCOUNT_ID`, `GH_OWNER`, `GH_REPO` then create the role:

```bash
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g; s/GH_OWNER/$GH_OWNER/g; s/GH_REPO/$GH_REPO/g" trust-policy.json

aws iam create-role \
  --role-name "$CI_ROLE_NAME" \
  --assume-role-policy-document file://trust-policy.json \
  --description "GitHub Actions deployer for $GH_OWNER/$GH_REPO"
```

**Critical**: the `sub` condition restricts AssumeRole to ONLY this
repo. Without it, ANY repo's GitHub Actions could assume your role.
This is the AWS analog of GCP WIF's `attribute-condition` and the
most common misconfiguration.

To restrict further (e.g. only `main` branch + version tags), tighten:

```json
"StringLike": {
  "token.actions.githubusercontent.com:sub": [
    "repo:GH_OWNER/GH_REPO:ref:refs/heads/main",
    "repo:GH_OWNER/GH_REPO:ref:refs/tags/v*"
  ]
}
```

### A.3 Attach minimum-permission policies to the deployer role

Map IAM policies to concrete CI operations. Customize per service.

```bash
# ECR push (needed by deploy-aws.yml build job)
aws iam put-role-policy \
  --role-name "$CI_ROLE_NAME" \
  --policy-name ECRPush \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage"
        ],
        "Resource": "*"
      }
    ]
  }'

# EKS describe + kubectl access (read kubeconfig)
aws iam attach-role-policy \
  --role-name "$CI_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
```

For deploy permissions inside the cluster, also map this role into
the cluster's `aws-auth` ConfigMap so kubectl honors RBAC. See
`docs/environment-promotion.md` for the cluster-mapping step.

### A.4 Configure GitHub Variables and Secrets

Two role ARNs and two channels. Getting the channel wrong is silent: GitHub
does **not** fall back from `secrets.X` to `vars.X`, so a value set in the
wrong place arrives as an empty string and
`aws-actions/configure-aws-credentials` fails with an unhelpful message about
`role-to-assume`.

This section previously said *"repository variables (NOT secrets — these are
not sensitive)"* and listed `AWS_ROLE_ARN` among them. That reasoning is sound
— a role ARN is not a credential — but it did not match the shipped workflows,
which read both role ARNs from `secrets`. An adopter following it exactly got
an empty `role-to-assume` on their first deploy. `AWS_BUILD_ROLE_ARN` was not
listed at all.

**Secrets** — `https://github.com/$GH_OWNER/$GH_REPO/settings/secrets/actions`

| Secret | Value | Read by |
| -------- | ------- | --------- |
| `AWS_ROLE_ARN` | `arn:aws:iam::${ACCOUNT_ID}:role/${CI_ROLE_NAME}` | the **deploy** jobs, via `deploy-common.yml`'s `workflow_call` secrets contract |
| `AWS_BUILD_ROLE_ARN` | `arn:aws:iam::${ACCOUNT_ID}:role/${BUILD_ROLE_NAME}` | the **build** job, to push to ECR |

Two roles rather than one is deliberate — ADR-017 / D-31, per-purpose
identities. The build role needs ECR push and nothing else; the deploy role
needs EKS access and nothing else. Point them at the same role only if you
accept that a compromised build step can also deploy.

Use **environment** secrets rather than repository secrets if `dev`, `staging`
and `prod` should assume different roles, which is the arrangement D-31 is
asking for. That is also the reason these stay secrets rather than variables:
environment scoping is the mechanism that makes per-environment roles work, and
a non-sensitive value in a secret costs nothing.

> **Known asymmetry.** The GCP side reads its equivalents from `vars`
> (`vars.GCP_SERVICE_ACCOUNT`, `vars.GCP_WIF_PROVIDER`) and its runbook says so
> consistently. AWS reading role ARNs from `secrets` while GCP reads them from
> `vars` is a parity gap the template has not resolved; changing it would change
> `deploy-common.yml`'s `workflow_call` contract, which is adopter-visible and
> needs its own ADR. `scripts/check_deploy_contract_documented.py` at least
> guarantees that whichever channel each side uses is the one documented here.

**Variables** — `https://github.com/$GH_OWNER/$GH_REPO/settings/variables/actions`

| Variable | Value |
| ---------- | ------- |
| `AWS_REGION` | e.g. `us-east-1` |
| `AWS_REGISTRY_ID` | `${ACCOUNT_ID}` — the ECR registry account |
| `EKS_DEV_CLUSTER` | EKS cluster name for dev |
| `EKS_STAGING_CLUSTER` | EKS cluster name for staging |
| `EKS_PROD_CLUSTER` | EKS cluster name for prod |

If the deploy chain still references `secrets.AWS_ACCESS_KEY_ID` or
`secrets.AWS_SECRET_ACCESS_KEY` after this runbook, **delete those
secrets from the repo**. Leaving them around invites someone to
re-introduce static-key auth in a later PR.

## Part B — EKS Pod → AWS (IRSA, runtime identity)

This is REQUIRED for any service that calls AWS APIs from inside the
cluster: S3 reads (model artifacts, drift baseline), Secrets Manager,
DynamoDB, etc.

### B.1 Associate the cluster's OIDC provider with IAM

EKS clusters auto-create an OIDC provider URL at creation. You must
register that URL with IAM once per cluster:

```bash
eksctl utils associate-iam-oidc-provider \
  --cluster "$EKS_CLUSTER" \
  --region "$REGION" \
  --approve
```

(If you don't use eksctl: equivalent `aws iam create-open-id-connect-provider`
with the `EKS_OIDC_URL` from the variables block above.)

### B.2 Create the runtime role with an IRSA trust policy

Save as `irsa-trust.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/EKS_OIDC_URL"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "EKS_OIDC_URL:sub": "system:serviceaccount:NAMESPACE:SERVICE_ACCOUNT_NAME",
          "EKS_OIDC_URL:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
```

Substitute `ACCOUNT_ID`, `EKS_OIDC_URL`, `NAMESPACE`,
`SERVICE_ACCOUNT_NAME`. Then:

```bash
aws iam create-role \
  --role-name "$SVC_ROLE_NAME" \
  --assume-role-policy-document file://irsa-trust.json \
  --description "IRSA role for $SVC_ROLE_NAME"

# Attach least-privilege policies, e.g. read model bucket:
aws iam put-role-policy --role-name "$SVC_ROLE_NAME" \
  --policy-name S3ModelRead \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::your-model-bucket",
        "arn:aws:s3:::your-model-bucket/*"
      ]
    }]
  }'
```

### B.3 Annotate the K8s ServiceAccount

In your service's K8s manifests (`templates/service/k8s/base/serviceaccount.yaml`):

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {service-name}-sa
  namespace: {namespace}
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT_ID:role/SVC_ROLE_NAME
```

The pod-identity-webhook injects AWS_ROLE_ARN +
AWS_WEB_IDENTITY_TOKEN_FILE env vars + a projected SA token. The
boto3/aws-sdk in the pod auto-resolves them — no code changes needed.

The CI's `IRSA/Workload Identity enforcement` step in `templates/service/.github/workflows/ci.yml`
already greps for this annotation in staging/prod manifests and FAILS
the pipeline if it is missing (D-18 invariant).

## Part C — Verify

### CI half

Trigger any workflow that authenticates to AWS (e.g. `deploy-aws.yml`
via `workflow_dispatch`). The auth step should print:

```text
Successfully assumed role arn:aws:iam::...:role/github-actions-ci-deployer
Caller identity: arn:aws:sts::...:assumed-role/...
```

If it fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity`:

1. The trust policy `sub` condition does not match. Re-check A.2 and
   the workflow's `permissions: id-token: write` is set.
2. The `aud` claim is missing. Recent versions of
   `aws-actions/configure-aws-credentials` set this automatically.
3. The OIDC provider thumbprint is stale. Check GitHub's
   docs/security advisory for the current value.

### IRSA half

Spawn a test pod using your annotated ServiceAccount and call AWS:

```bash
kubectl run irsa-test --rm -it --restart=Never \
  --serviceaccount={service-name}-sa \
  --namespace={namespace} \
  --image amazon/aws-cli \
  -- sts get-caller-identity
```

The `Arn` in the output should reference the IRSA role, not a node
EC2 instance role. If you see the node role, the SA annotation is
missing or the pod-identity-webhook is not installed
(`kubectl get mutatingwebhookconfigurations | grep pod-identity`).

## Related

- ADR-014 §3.1 — Phase 3 supply-chain governance
- Invariants D-17, D-18
- `templates/service/.github/workflows/deploy-common.yml` — consumer (uses `vars.AWS_ROLE_ARN`)
- `templates/service/.github/workflows/deploy-aws.yml` — chain entry point
- GCP counterpart: `docs/runbooks/gcp-wif-setup.md`
- AWS docs: <https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html>
- GitHub OIDC docs: <https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services>
