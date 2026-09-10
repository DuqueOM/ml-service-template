# ADR-049 — The served image installs only what it runs

- **Status**: Accepted
- **Date**: 2026-09-09
- **Deciders**: template maintainer
- **Related**: ADR-047 (MLflow 3.x migration), ADR-048 (co-installation groups),
  ADR-035 (requirements as an export of pyproject), D-05, D-17..D-19,
  `.security-baselines/trivy-fs.trivyignore`

## Context

`templates/service/requirements.txt` is the file the Dockerfile installs, and it
was a single list: ML core, serving, monitoring, **MLflow, Optuna, pytest,
pytest-cov, locust and httpx**. Every one of those last six went into every
inference pod, and no code that runs in the image imports any of them.

This was not visible from the code, because the code was right: `app/` does not
import MLflow. ADR-047 said so explicitly —

> MLflow remains a training and tracking dependency: `app/` does not import it,
> so none of these findings ever touched the inference path.

The first half is true and the conclusion does not follow. **Installed is not
the same as imported.** A vulnerability scan of the image reports what is on
disk, an attacker who reaches code execution finds what is on disk, and an SBOM
attestation attests what is on disk. The advisories were in the pods the whole
time.

## What was measured

Resolved with `pip install --dry-run --ignore-installed`, scanned with
`trivy fs --scanners vuln --severity CRITICAL,HIGH --ignore-unfixed` (0.71.0):

| | packages | findings | of which CRITICAL |
| --- | ---: | ---: | ---: |
| `requirements.txt`, before | **151** | **24** | **7** |
| `requirements.txt`, after | **35** | **4** | **0** |

The 20 findings that disappear are all MLflow. The 4 that remain are starlette
(3, transitive via fastapi) and pyarrow (1); both are already accepted with
dates in `.security-baselines/trivy-fs.trivyignore`.

**The reduced set is sufficient**, verified by executing rather than reasoning:
a virtualenv built from `requirements.txt` alone runs every import the
Dockerfile smoke-checks — `app.main`, the three `common_utils` modules, the two
monitoring modules the CronJobs invoke, and `training.features`, which serving
genuinely needs because `fastapi_app.py` raises without it.

## Decision

**Three files, one direction of inclusion.**

| file | contents | installed by |
| --- | --- | --- |
| `requirements.txt` | what the served image imports, and nothing else | the Dockerfile; the drift/performance CI lanes |
| `requirements-train.txt` | `-r requirements.txt` + mlflow, optuna | the retrain workflow; `make install-train` |
| `requirements-dev.txt` | `-r requirements.txt` + pytest, httpx, locust, jsonschema, ruff, mypy, bandit, pre-commit | `make install-dev`; the service's `ci.yml`; this repo's own test lanes |

A training environment is a superset of a serving environment. The reverse must
never be true.

`scripts/check_dependency_partition.py` (gate 17) enforces the direction that
matters: **no module reachable from the image's entrypoints may import a
distribution declared only in the training or dev sets**, unless the import is
guarded — inside `try:`/`except ImportError`, or deferred into a function, the
way `common_utils` already reaches boto3, google-cloud and opentelemetry.

The entrypoints are **parsed out of the Dockerfile** — its smoke-import `RUN`
steps and its `CMD` — rather than listed in the gate. A hand-kept list would
drift from the image the first time someone added a CronJob, and a gate
checking a stale set of entrypoints is worse than no gate.

**The Dockerfile's smoke import is the executable half of the same claim.** It
imports every entrypoint the image runs, at build time, so a runtime dependency
dropped from `requirements.txt` by mistake fails `docker build` instead of
crashing a pod on first boot.

## Two things this changes that are easy to miss

**The golden path now scans the image.** The generated service's own `ci.yml`
has always run `trivy image` and failed on CRITICAL,HIGH; the golden path built
an image and never scanned it, so the lane that is supposed to be the trust
anchor applied a weaker bar than the service it generates. The scan runs
*before* signing: an image that would fail the adopter's own gate must not be
signed, attested and admitted here.

**The runtime stage drops the system pip and setuptools.** With the Python
dependencies down to 35, the base image became the remaining source of fixable
findings — `CVE-2025-47273` (setuptools 70.3.0) and `GHSA-6v7p-g79w-8964`
(msgpack 1.1.2, vendored inside pip). The venv at `/opt/venv` already has both
removed and is first on `PATH`, so nothing in the image could reach them.
Removing beats upgrading: a serving image installs nothing at runtime, so a
*current* setuptools would be the same unused surface carrying a later CVE.
Verified before removal that every entrypoint imports cleanly with pip,
setuptools and pkg_resources blocked from `sys.meta_path`.

## What this does NOT claim

**The CVEs are not gone.** They are still reported against
`requirements-train.txt` by this repository's own dependency scan, because
`resolve_python_dependencies.py` discovers requirements files with
`git ls-files` and will find it. That is correct and deliberate: the risk moved
out of the inference pods, it did not evaporate. The 2026-12-08 expiry on the
MLflow entries stands, and ADR-047's migration decision is still owed.

What changed is the blast radius. A training-only advisory is no longer also a
production-inference advisory, and the MLflow major-version decision is no
longer coupled to the security posture of the serving image — which is what
made it possible to take that decision on its merits instead of under a
deadline.

## Consequences

- **Adopters get a new step.** `make install` no longer prepares a training
  environment; `make install-train` does. `make lock` emits two lockfiles,
  because locking only one of them is how the split quietly stops meaning
  anything.
- This repository's own `template-context-tests.yml` installs
  `requirements-dev.txt` rather than `requirements.txt`. It had been relying on
  httpx and locust arriving through the runtime file; installing only the
  runtime set would have failed every TestClient test with
  `No module named 'httpx'`.
- Two more files under `templates/service/`, both in the `generated-service`
  co-installation group of ADR-048, so their shared pins cannot drift from the
  runtime file's.

## Alternatives considered

**Keep one file and accept the advisories.** What was already happening, by
default rather than by decision. Rejected once measured: 20 of 24 findings and
all 7 CRITICAL were removable by not installing packages nothing calls.

**Split with pip extras only (`pip install .[train]`), no requirements files.**
Cleaner in principle. Rejected: ADR-035 makes `requirements*.txt` the pip-only
export for adopters in air-gapped or non-PEP-517 environments, and the
Dockerfile installs a requirements file by design. The extras exist in
`pyproject.toml` and the txt files mirror them; ADR-048's gate keeps the two
consistent.

**Put monitoring in its own third lane.** The drift and performance CronJobs
run from the served image, so their dependencies are runtime dependencies by
definition. A separate lane would describe a separation that does not exist in
the deployment.
