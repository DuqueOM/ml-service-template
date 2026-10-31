# Contributing to ML-MLOps Production Template

Thanks for contributing. This repository is meant to be a serious production template, so we optimize for changes that
improve reliability, clarity, security, and repeatability in real ML systems.

## Ground rules

- Follow the invariants and operating model in [AGENTS.md](AGENTS.md).
- Keep solutions proportional to the problem. This repo is intentionally opinionated, but it should not drift into
  platform over-engineering.
- Prefer production-backed patterns over purely theoretical abstractions.
- If a change affects architecture, governance, security posture, or default behavior, document it with an ADR.
- Versioning is governed by [`docs/RELEASING.md`](docs/RELEASING.md). Breaking changes to scaffolded output, contracts,
  or overlay names require a MAJOR bump and a row in [`MIGRATION.md`](MIGRATION.md).

## Evidence policy for new components (per ADR-020 §S1-2)

R4 audit finding H2 documented a recurring class of bugs where new template components shipped without execution
evidence. To prevent regression, **PRs that introduce a new component MUST include three evidence blocks in the PR
body**, enforced by [`pr-evidence-check.yml`](.github/workflows/pr-evidence-check.yml):

1. **Evidence — Schema / Contract Test**: path to the test file that pins the new component's contract.
2. **Evidence — Real Execution Output**: truncated raw output (stdout/stderr) from running the new component end-to-end.
   Description text is NOT acceptable — actual output is.
3. **Evidence — CI Run Link**: URL to the GitHub Actions run that produced the output above, OR a link to a
   [`VALIDATION_LOG.md`](VALIDATION_LOG.md) entry.

The allowlist that triggers this requirement:

- `.github/workflows/*.yml`, `templates/service/.github/workflows/*.yml` (workflows / deploy YAML)
- `templates/service/k8s/overlays/**`, `templates/k8s/policies/**` (cluster surface)
- `templates/service/tests/contract/**` (new contract tests)
- `templates/service/common_utils/*.py` (adopter API surface)
- `scripts/*.py`, `scripts/*.sh` (operational scripts)
- `templates/config/*.yaml` (policy YAML)

Typical doc-only or refactor PRs that don't introduce a new component are exempt; the evidence section can be deleted
from the PR body in that case.

## Local validation cadence

Pre-commit hooks are kept fast (target < 10 s) so they don't get bypassed
with `--no-verify`. Slow integration checks live in CI and as on-demand
Make targets, per [`docs/audit/ACTION_PLAN_R5.md`](docs/audit/ACTION_PLAN_R5.md) §R5-L4.

| Cadence | Cost | Entry point | What it covers |
| --- | --- | --- | --- |
| every commit | < 10 s | pre-commit hooks (auto) | format, lint, gitleaks, contract tests on changed files |
| on demand | ~60 s | `make smoke` | scaffold a fresh service end-to-end (catches scaffolder + dependency-graph regressions) |
| on demand | ~3 min | `make validate-templates` | lint + K8s render + agentic + scaffold + EDA |
| every PR | ~3–10 min | [`pr-smoke-lane.yml`](.github/workflows/pr-smoke-lane.yml) | scaffold + every overlay rendered + kubeconform + binary audit |
| every PR | varies | other CI workflows | full unit tests, contract tests, security scans, signing |

When you touch any of the following, **run `make smoke` locally before push**:

- `templates/scripts/new-service.sh`
- `copier.yml` (Copier template configuration)
- `templates/service/pyproject.toml` or `templates/service/requirements.txt`
- any `{@ @}` Jinja token introduced into `templates/service/`
- `templates/service/k8s/base/` or `templates/service/k8s/overlays/`
- `templates/service/.github/workflows/`

CI will catch the same class of bug, but the local feedback loop is faster.

## How to contribute

1. Fork the repository.
2. Create a branch for your change.
3. **Install local checks (mandatory)**:

   ```bash
   # Option A: uv (recommended — locked by uv.lock, ADR-053)
   uv sync && uv run pre-commit install
   # Option B: pip (compatible, unlocked)
   pip install pre-commit && pre-commit install
   ```

   This installs the pre-commit hooks (ruff lint + format per ADR-044, mypy,
   bandit, gitleaks, fast contract tests). Total runtime is targeted
   at < 10 s on a no-op commit. `make install-dev` does all of it in one step
   (uv when present, pip otherwise) and also builds the test environment below.

   **Two environments, as in CI (ADR-053).** `uv sync` builds the root `.venv`
   with the contributor tooling pinned in `uv.lock` (pre-commit, copier,
   jsonschema, pytest) — the environment of the scaffold and policy lanes, so the
   governance suite runs there: `uv run pytest templates/tests/governance`. The
   CI-Examples test stack lives in a separate `.venv-test`, built by `make test-env` from the
   files that own its pins — `examples/minimal/requirements.txt`, four of the
   generated service's pins and the root `template-tests` group — through
   [`scripts/install_test_env.py`](scripts/install_test_env.py), the same script
   the CI-Examples job runs. Keeping them apart is deliberate: copier's own
   dependencies never mix with the pins under test, and `uv sync` never removes
   the test stack. Run the full suite with `.venv-test/bin/python -m pytest`
   and the paths in the *Run full test + coverage matrix* step of
   [`ci-examples.yml`](.github/workflows/ci-examples.yml).

   A dependency already pinned in a requirements file is never retyped in the
   root `pyproject.toml`: `check_dependency_pin_coherence.py` rejects the copy,
   because a Dependabot PR bumping the original could not also bump it
   (ADR-052 §4).

   **Without pre-commit installed, your commits will fail CI.** The CI runs the exact same pre-commit configuration, so
   local failures predict CI failures.

   The slower scaffold smoke test (~60 s) was retired from pre-push in R5-L4 because it was duplicating
   [`pr-smoke-lane.yml`](.github/workflows/pr-smoke-lane.yml). Run it on demand via `make smoke` per the *Local
   validation cadence* table above.

4. Make your changes.
5. Run the relevant quality gates locally:

   ```bash
   # Run all commit-stage hooks manually
   pre-commit run --all-files

   # Run the scaffold smoke (replaces the retired pre-push hook; ~60 s)
   make smoke

   # Or the full local validation chain (lint + render + agentic + scaffold + EDA)
   make validate-templates
   ```

6. Commit with sign-off:

   ```bash
   git commit -s -m "feat: describe your change"
   ```

7. Push and open a pull request.

## Developer Certificate of Origin (DCO)

This project uses the Developer Certificate of Origin (DCO).

By contributing, you certify that:

- you created the contribution or otherwise have the right to submit it
- you understand the contribution will be distributed under the Apache License 2.0

Every commit must include a `Signed-off-by` line. The simplest way is to use `git commit -s`.

## Commit style

We recommend Conventional Commits:

```text
feat: add guarded CI autofix policy
fix: align SLO metric names with service exporter
docs: clarify operational memory plane boundaries
```

## What kinds of contributions are useful here

- improvements extracted from real production usage
- stronger tests for template guarantees
- better cloud parity between GCP and AWS
- security, observability, and CI/CD hardening
- documentation that reduces ambiguity for adopters
- safer agentic workflows and clearer operating boundaries

## Contribution expectations

### Template code

- Keep scaffolded repos self-contained.
- Do not introduce hidden runtime dependencies on the template root.
- Preserve the separation between training, serving, monitoring, and infrastructure concerns.
- Avoid mutable image tags and static cloud credentials.
- Keep production defaults safe by default.
- When editing `templates/service/` files, remember Copier renders all
  content through Jinja with `{@ @}` delimiters. Use `{% raw %}` blocks
  around literal `{@ @}` text in documentation files to prevent
  rendering errors (D-34).
- The scaffolded service has both `pyproject.toml` (source of truth) and
  `requirements.txt` (pip-compatible export). When adding a dependency,
  add it to `pyproject.toml` first, then regenerate `requirements.txt`.
  `uv sync` reads `pyproject.toml` natively (ADR-035).
- `copier update` can pull template improvements into an existing
  scaffolded service. See `/scaffold-update` workflow.

### Documentation

- Use measured evidence where possible.
- Be explicit about boundaries and non-goals.
- Keep README, ADRs, runbooks, and contribution guidance in sync when behavior changes.

### Agentic system

- `AUTO`, `CONSULT`, and `STOP` behavior must remain auditable.
- Dynamic risk escalation must only increase caution, never silently weaken policy.
- New automated repair paths need bounded blast radius and deterministic verification.

## Pull request review

Reviewers will look for:

- operational safety
- alignment with template invariants
- clarity of documentation
- test coverage proportional to risk
- open-source maintainability

## License

All contributions are accepted under the Apache License 2.0. No Contributor License Agreement (CLA) is required.

By submitting a contribution, you agree to the terms defined in the DCO.

## Code of conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
