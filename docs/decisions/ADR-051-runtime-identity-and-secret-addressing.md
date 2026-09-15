# ADR-051 — Runtime identity and secret addressing are one naming contract, asserted end to end

- **Status**: Accepted
- **Date**: 2026-09-15
- **Deciders**: template maintainer
- **Related**: ADR-017 (per-purpose identities), ADR-016 (API auth rollout),
  ADR-049 (runtime/training dependency partition), ADR-011 (environment
  promotion), D-17, D-18, D-19, D-29, D-31

## Context

No staging or production deployment of this template could authenticate a
request, on either cloud, and every deploy went green.

Nothing was wrong in any single file. Four layers each chose a name, and no
two chose the same one:

| Layer | Named | Actually deployed |
| --- | --- | --- |
| GCP Workload Identity binding | `ml-services/<project>-sa` | `<service>-<env>/<service>-sa` |
| GKE ServiceAccount annotation | GSA `<service>-sa@…` | Terraform creates `<project>-runtime-<env>@…` |
| AWS IRSA trust `sub` | `ml-services:<service>` | `<service>-<env>:<service>-sa` |
| EKS ServiceAccount annotation | role `<service>-irsa-role` | Terraform creates `<project>-<service>-irsa-<env>` |
| Secret requested (GCP / AWS) | `<slug>-API_KEY` / `<slug>/API_KEY` | `<project>-<service>-api_key` / `<project>/<service>/api_key` |
| Environment read by the loader | `ENV` | overlays set `ENVIRONMENT` |
| Cloud SDK in the served image | required by the loader | installed by nothing |

A pod bound to no identity cannot download its model or read a secret. The
drift CronJob ran as the predictor's ServiceAccount, so ADR-017's separate
drift identity was bound to a name no workload used.

Three more defects made the post-deploy smoke test unable to see any of it.
It called only `/ready`, which never touches the secret backend. Its pod was
tag-pinned, which Kyverno's `require-image-digest` rejects in staging and
production. It had no `restricted` securityContext, which PSS rejects in
prod. And NetworkPolicy default-deny gave it no egress, not even DNS, while
the predictor admitted ingress only from the ingress controller and
Prometheus. kind enforces none of these, which is why the golden path stayed
green.

## Decision

**One naming contract, derived in one place per layer, and asserted across
all of them.**

1. **Kubernetes coordinates.** Namespace `<service>-<dev|staging|prod>`.
   ServiceAccounts `<service>-sa` for runtime, `<service>-drift-sa` for the
   drift CronJob, and `<service>-retrain-sa` reserved for an in-cluster
   retrain Job.
2. **Terraform derives from those coordinates.** `local.k8s_env_suffix` maps
   `production` to `prod`. Every Workload Identity member and IRSA trust `sub`
   is built per service from it. `var.environment` is validated to
   `dev | staging | production`, and `service_names` defaults to the
   scaffolded service.
3. **Overlays name what Terraform creates.** Each cloud overlay annotates
   both ServiceAccounts with the GSA or IAM role of the matching purpose and
   environment. The only adopter inputs are `{PROJECT_NAME}`, `{PROJECT_ID}`
   and `{AWS_ACCOUNT_ID}`.
4. **Secret ids reproduce Terraform's names.** Each overlay sets
   `CLOUD_PROVIDER` and `SECRETS_PREFIX`. `common_utils.secrets.secret_id`
   joins prefix and lowercased key with `-` on GCP and `/` on AWS. The loader
   reads `ENV`, then `ENVIRONMENT`, then `APP_ENV`. `auth.py` delegates to
   that detector instead of keeping a second copy.
5. **Cloud images carry their backend.** `requirements-gcp.txt` and
   `requirements-aws.txt` hold only the secret-manager SDK. The Dockerfile
   installs one of them when built with `--build-arg CLOUD_PROVIDER`, and
   the deploy workflows pass it. A build-time import check fails an image
   that cannot load its backend.
6. **Lookups are cached, misses are not.** Cloud reads are cached for
   `SECRETS_CACHE_TTL_SECONDS`, default 300. That keeps a Secret Manager
   round-trip off every request while still picking up a rotation. A
   provider "not found" maps to `SecretNotFoundError`. Every other provider
   error is a `SecretBackendError`, and auth fails closed with 503.
7. **The smoke test proves the auth path without holding the key.** It
   calls `/model/info`, which `verify_api_key` protects, with a deliberately
   wrong credential. With auth enabled, 401 means the secret resolved and
   the comparison ran, and 503 means it did not. The smoke pod is
   digest-pinned, runs with a `restricted` securityContext, and is admitted
   by `k8s/base/networkpolicy-smoke-test.yaml`.
8. **The contract is a test, not a comment.**
   `tests/test_runtime_identity_contract.py` reads every `gcp-*` and `aws-*`
   overlay and evaluates the Terraform interpolations against it. It checks
   the smoke step and the image build the same way. It keeps no table of
   expected names. On the pre-fix tree it fails 22 of 22 cases, each on its
   own defect.

## Consequences

- **Adopter-visible.** Terraform resource addresses for the three Workload
  Identity bindings gain a `for_each` key, and IRSA trust policies change.
  No adopter has completed a cloud deploy, so there is no state to move.
  MIGRATION.md gives the `terraform state mv` form for anyone who has.
- **Least privilege improves in two places.** The runtime Role no longer
  grants `secrets: get`; nothing reads Kubernetes Secrets. The smoke test
  needs no secret access in CI.
- **Image surface grows only where it is used.** Cloud images add one SDK
  and its dependencies. Local, CI and golden-path images are unchanged, so
  ADR-049's partition holds for them.
- **Lowercased keys.** A caller of `get_secret("EXTERNAL_API_KEY")` in
  staging or production now requests `…-external_api_key`. The old
  uppercase path never worked in a cloud image, so no working configuration
  depended on it.

## What this does NOT claim

- **No cloud deploy has been run.** Terraform validates, the overlays
  render, and the contract holds. Whether a real GKE or EKS pod resolves its
  secret is L4 evidence this repository does not yet have.
- **The drift CronJob's network path is not fixed here.** Its pods are
  labelled `<service>-drift`, which no allow policy selects, so default-deny
  blocks its DNS, bucket and Pushgateway egress. That is tracked separately.

## Alternatives considered

- **Mount the key from a Kubernetes Secret, synced by External Secrets.**
  This adds an operator the template does not ship. It also re-creates the
  standing `secrets: get` grant, and the Secret's name is one more
  unasserted name.
- **Put both cloud SDKs in `requirements.txt`.** Every image, including
  local and CI, would carry grpcio and botocore it never imports. ADR-049
  measured that shape before and removed it.
- **Hand-roll REST calls to avoid SDKs.** GCP would work through the metadata
  server. AWS would need a hand-written SigV4 signer, which is security code
  the template would have to maintain.
- **Hold the real API key in CI for an authenticated `/predict` smoke.** That
  grants the deploy identity secret access (D-31), for a signal the
  wrong-key probe already provides.
