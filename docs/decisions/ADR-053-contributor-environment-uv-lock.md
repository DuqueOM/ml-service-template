# ADR-053 — The contributor environment is a locked uv project, and every pin keeps one owner

- **Status**: Accepted
- **Date**: 2026-10-28
- **Deciders**: template maintainer
- **Related**: ADR-035 (uv adoption for the generated service), ADR-044 (ruff),
  ADR-048 (co-installation groups), ADR-052 (dependency merges on green),
  D-05 (compatible-release pinning)

## Context

`CONTRIBUTING.md` told contributors that the recommended setup was
`uv sync && uv run pre-commit install`. Run against the root `pyproject.toml` it
did not work, and nothing noticed:

- the root `pyproject.toml` carried tool configuration only — no `[project]`, no
  dependencies — so `uv sync` created an empty environment;
- with no `requires-python`, uv defaulted to **Python 3.14**, outside the CI
  matrix (3.11–3.13);
- `uv run pre-commit install` then failed, because nothing had installed
  pre-commit.

The pip route worked, but `make install-dev` still installed black, isort and
flake8, which ADR-044 retired. The CI-Examples test environment was assembled by
inline shell in `ci-examples.yml` that no contributor could run, and its
`pytest-asyncio ~= 1.4.0` was a literal no Dependabot entry watched. The root
Dependabot entry — commented *"this repository's own tooling (root
pyproject.toml)"* — used the `pip` ecosystem on a pyproject with no
dependencies, so it watched nothing.

ADR-035 is not the gap: it governs `templates/service/`, the payload Copier
renders, and decides that the payload ships no `uv.lock` because the lock is
adopter-specific. This ADR is about the people who build the template.

## Decision

**1. The repository root is a uv project with a committed lock.** The root
`pyproject.toml` declares a non-packaged `[project]` with
`requires-python = ">=3.11,<3.14"` (the CI matrix), `.python-version` pins 3.11,
and `uv.lock` is committed. Copier renders only `_subdirectory:
templates/service`, so none of this reaches an adopter — measured below.

**2. One owner per pin.** A version is declared in exactly one file:

| What | Owner |
| --- | --- |
| The example's stack | `examples/minimal/requirements.txt` |
| The four service pins the test suite needs | the service's requirements files |
| `pytest-asyncio` | root `[dependency-groups].template-tests` |
| Contributor tooling (pre-commit, copier, jsonschema, pytest) | root `[dependency-groups].dev` |

The root groups lock only what had no owner. Retyping a requirements-file pin in
the pyproject was the first design and is rejected: ADR-048 would require the
copy to be byte-identical, and Dependabot's pip PR bumping the original cannot
also bump the copy and `uv.lock`, so every such PR would fail — which ADR-052 §4
forbids ("one PR must be able to satisfy every gate"). The CI already read the
service pins from their files for exactly this reason.

**3. Two environments, mirroring CI.** `uv sync` builds `.venv` from the `dev`
group: the scaffold and policy lanes' environment, where the governance suite
runs. `scripts/install_test_env.py` builds `.venv-test` — the CI-Examples
environment — from the owning files in **one** resolver run. CI calls the same
script with `--installer pip`; `make test-env` calls it with `--installer uv`.
Keeping them apart means copier's dependencies never mix with the pins under
test, and `uv sync` never removes the test stack.

**4. The gates enforce it.** `check_dependency_pin_coherence.py` (ADR-048) now
requires every root dependency group to declare the environment it joins,
rejects a group that redeclares a pin its environment already owns, and requires
a Dependabot `uv` entry watching `/`. `check_pin_shape.py` (D-05) now covers the
root groups too: a lock pins the resolution, not the declaration.

**5. Dependabot's root entry moves from `pip` to `uv`,** the only ecosystem that
moves `pyproject.toml` and `uv.lock` in one PR.

## What was measured

Executed on 2026-10-28, not reasoned about.

- **Before:** `uv sync --dry-run` on the root pyproject of `origin/main`
  (`aea4eaf`) — *"No `requires-python` value found … Defaulting to `>=3.14`"*, an
  empty environment.
- **Lock:** `uv sync --locked` succeeds on Python 3.11.15, 3.12.13 and 3.13.13.
- **Test environment:** the set `install_test_env.py` installs resolves on 3.11,
  3.12 and 3.13 (53 packages each, `uv pip compile`).
- **CI-Examples suite** in `.venv-test`, same paths and coverage flags as
  `ci-examples.yml`: 291 passed, coverage 41.83 % (floor 40 %).
- **Governance suite** in `.venv`: 474 passed, 9 skipped, 0 failed — including
  the eleven new negative and positive controls.
- **Gates:** `[dependency-pins]` 8 requirements files in 2 groups, 2 root
  pyproject groups with one owner per pin; `[pin-shape]` 80 pins across 13 files
  (was 74).
- **Scaffold:** `scripts/test_scaffold.sh` passes with copier from the lock.
- **No leakage:** a service rendered from a clone carrying this change contains
  no `uv.lock`, `.python-version`, `scripts/install_test_env.py` or `.venv-test`,
  and its `pyproject.toml` is the payload's, not the contributor project's.

## Consequences

- The documented `uv sync` route works and is reproducible; the pip route still
  works, unlocked.
- `.venv-test` is not locked: it resolves the owning files' `~=` ranges at install
  time, exactly as CI does today. Locking it would need a constraints file the CI
  generates and checks — deliberately left to the CI adoption PR.
- CI is unchanged in behaviour except that the test environment now resolves in
  one pip run instead of three, which can only surface a conflict the sequential
  installs used to paper over.

## What this does NOT change

- `templates/service/` and ADR-035: the payload still ships no `uv.lock`, and
  pip stays a first-class adopter path.
- The CI workflows' installers. Moving the scaffold, policy and CI-Examples lanes
  to `uv sync --locked` is a separate PR, reviewed on its own.
- Whether generated services should be uv-first. That is a product decision for
  its own ADR.

## Alternatives considered

- **Recreate a local venv with the same installs and change nothing.** Fixes one
  machine and leaves the documented route broken for every other contributor.
- **Retype the test pins in `[dependency-groups]` and extend ADR-048's identity
  rule to them.** Rejected — see Decision 2: it breaks ADR-052's auto-merge.
- **Generate the requirements files from `uv.lock`.** Inverts ownership for the
  payload, whose requirements files are what adopters and Dependabot read, and
  contradicts ADR-035.
- **One environment for tooling and tests.** copier's transitive dependencies
  (pydantic, pyyaml) would share an environment with the pins under test, and
  `uv sync` would remove whatever it did not lock.
