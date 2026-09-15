# Runbook — Secrets Manager Integration End-to-End

- **Authority**: ADR-051 (runtime identity and secret addressing), ADR-020 §S2-5, R4 audit finding M1.
- **Mode**: CONSULT (touches secret managers but only via existing helpers; no rotation).
- **Scope**: validate that `templates/service/common_utils/secrets.py` reads the secret Terraform created, from GCP
  Secret Manager (GSM) and AWS Secrets Manager (ASM), and never falls through to `os.environ` outside local and CI
  (D-18).
- **Approver**: Platform Lead.
- **Audit trail**: each successful execution writes an entry to `VALIDATION_LOG.md`.

---

## Why this runbook exists

R4 finding M1 flagged that `common_utils/secrets.py` was unit-tested but had never run against a real secret manager.
The previous version of this runbook could not have closed it. It set `MLOPS_ENV`, which the loader never read, and
called `get_secret(..., backend=...)`, a parameter that never existed, so every procedure failed with `TypeError`.

The secret ids were also wrong in a way no local test could catch. The loader asked for `<slug>-API_KEY`, while
Terraform creates `<project>-<service>-api_key`. ADR-051 records the naming contract, and
`tests/test_runtime_identity_contract.py` asserts it. This runbook proves the live half.

---

## Pre-conditions

- Terraform applied for a **non-production** environment, so the per-service secrets exist.
- `gcloud` authenticated with `roles/secretmanager.secretAccessor` on the project, and `aws` authenticated with
  `secretsmanager:GetSecretValue` on the `<project_name>/<service>/*` scope.
- A Python 3.11 environment where the service's `common_utils` is importable, from the generated service's root.
- The cloud SDK for the procedure you run:

```bash
pip install -r requirements-gcp.txt   # Procedure 1
pip install -r requirements-aws.txt   # Procedure 2
```

Set the names once. They must match the Terraform variables and the overlay's `SECRETS_PREFIX`:

```bash
PROJECT_NAME="<terraform project_name>"
SERVICE="<service-kebab-name>"
```

---

## Procedure 1 — GSM end-to-end

```bash
PROJECT_ID="$(gcloud config get-value project)"
SECRET_ID="${PROJECT_NAME}-${SERVICE}-api_key"

# 1. Add a version to the Terraform-created secret. Terraform creates the
#    secret, never its value, so the payload never enters state.
openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add "$SECRET_ID" --data-file=-

# 2. Resolve it exactly as a staging pod does.
ENVIRONMENT=staging CLOUD_PROVIDER=gcp GCP_PROJECT_ID="$PROJECT_ID" \
SECRETS_PREFIX="${PROJECT_NAME}-${SERVICE}" \
python -c "
import os
from common_utils.secrets import get_secret
v = get_secret('API_KEY', namespace=os.environ['SECRETS_PREFIX'])
print('OK: resolved len=%d' % len(v))
"

# 3. Negative: a secret Terraform did not create is a miss, not a fallback.
ENVIRONMENT=staging CLOUD_PROVIDER=gcp GCP_PROJECT_ID="$PROJECT_ID" API_KEY=must-not-be-returned \
python -c "
from common_utils.secrets import get_secret, SecretNotFoundError
try:
    get_secret('API_KEY', namespace='does-not-exist-r4')
    print('FAIL: expected SecretNotFoundError')
except SecretNotFoundError:
    print('OK: miss raised; os.environ was not consulted')
"
```

Expected output is `OK: resolved len=64` on step 2 and `OK: miss raised` on step 3. Never print the value itself.

## Procedure 2 — ASM end-to-end

```bash
SECRET_ID="${PROJECT_NAME}/${SERVICE}/api_key"

aws secretsmanager put-secret-value --secret-id "$SECRET_ID" \
  --secret-string "$(openssl rand -hex 32)" >/dev/null

ENVIRONMENT=staging CLOUD_PROVIDER=aws SECRETS_PREFIX="${PROJECT_NAME}/${SERVICE}" \
python -c "
import os
from common_utils.secrets import get_secret
v = get_secret('API_KEY', namespace=os.environ['SECRETS_PREFIX'])
print('OK: resolved len=%d' % len(v))
"

ENVIRONMENT=staging CLOUD_PROVIDER=aws API_KEY=must-not-be-returned \
python -c "
from common_utils.secrets import get_secret, SecretNotFoundError
try:
    get_secret('API_KEY', namespace='does-not-exist-r4')
    print('FAIL: expected SecretNotFoundError')
except SecretNotFoundError:
    print('OK: miss raised; os.environ was not consulted')
"
```

## Procedure 3 — `os.environ` refusal outside local and CI

```bash
ENVIRONMENT=production R4_TEST_SECRET=should-be-refused \
python -c "
import sys
from common_utils.secrets import get_secret, SecretBackendError
try:
    v = get_secret('R4_TEST_SECRET')
    print('FAIL: returned a value in production mode')
    sys.exit(1)
except SecretBackendError as e:
    print('OK: refused — %s' % type(e).__name__)
"
```

With no `CLOUD_PROVIDER`, the helper must refuse. Expected output is `OK: refused — SecretBackendError`.

## Procedure 4 — from inside the cluster

Procedures 1 and 2 prove the names and the SDK. They do not prove the pod's identity binding. Deploy to staging and
read the `Post-deploy smoke test` step of `deploy-common.yml`:

```text
✓ auth path verified: API_KEY resolved from the secret manager and a wrong key was rejected
```

A 503 there means the pod could not resolve the secret. Check, in order: `SECRETS_PREFIX` in the overlay, that the
secret has an enabled version, and the ServiceAccount annotation against `terraform output`.

---

## Recording evidence

For each successful procedure, write a `VALIDATION_LOG.md` entry with:

- Date, operator, and cloud project or account ID, truncated.
- Helper version (`git rev-parse HEAD` at the time of the run).
- Output excerpts. Never a secret value.
- Latency observation (cold call, warm call) if material.

## Acceptance criteria for closing M1

- [ ] Procedure 1 (GSM) executed with `OK` on steps 2 and 3.
- [ ] Procedure 2 (ASM) executed with `OK` on both calls.
- [ ] Procedure 3 (`os.environ` refusal) executed and the helper refused.
- [ ] Procedure 4 smoke line observed in a staging deploy on at least one cloud.
- [ ] `VALIDATION_LOG.md` entry recorded.

## Cadence

- After every change to `templates/service/common_utils/secrets.py`.
- Quarterly minimum.
- After any cloud-provider API deprecation notice that touches Secret Manager / Secrets Manager.
