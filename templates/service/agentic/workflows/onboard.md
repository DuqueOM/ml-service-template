---
description: Generate an adopter context file (interview + validate, no secrets)
---

# /onboard Workflow

## 1. Pre-flight Checks

```bash
cd "$SERVICE_PATH"
test -f config/adopter_context.schema.json
grep -q "_context.local.yaml" .gitignore
```

If either fails → STOP.

## 2. Interview

Ask the adopter:

1. Cloud provider: `gcp`, `aws`, or `local`?
2. Container registry URL (or `null` for local profile)
3. MLflow tracking URI (or `sqlite:///mlflow.db` for local — MLflow 3.x
   refuses the file store, ADR-047)
4. DVC remote (or `null` for local)
5. GitHub org name
6. Monitoring endpoint (or `null` for local)
7. Log level (default: `INFO`)

## 3. Write Context File

Write to `<service_slug>_context.local.yaml`:

```yaml
cloud_provider: <answer>
container_registry: <answer>
mlflow_tracking_uri: <answer>
dvc_remote: <answer>
github_org: <answer>
monitoring_endpoint: <answer>
log_level: <answer>
profile: <from active_profile.yaml>
```

## 4. Validate

```bash
python3 -c "
import json, yaml, jsonschema
schema = json.load(open('config/adopter_context.schema.json'))
data = yaml.safe_load(open('${SERVICE_SLUG}_context.local.yaml'))
jsonschema.validate(data, schema)
print('Context file valid')
"
```

## 5. Secret Scan

```bash
grep -rEI "AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|ghp_[A-Za-z0-9]{36}" \
    "${SERVICE_SLUG}_context.local.yaml"
```

If any match → STOP. Chain to `/secret-breach`.

## 6. Report

Print:

- Context file path
- Cloud provider
- Profile
- Validation status
- Reminder: secrets → cloud secret manager, not this file
