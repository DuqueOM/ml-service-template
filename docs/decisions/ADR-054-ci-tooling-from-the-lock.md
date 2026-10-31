# ADR-054 — The template's own CI installs its tooling from the lock; the lanes that prove an adopter's route stay on pip

- **Status**: Accepted
- **Date**: 2026-10-31
- **Deciders**: template maintainer
- **Related**: ADR-053 (the locked contributor environment this uses), ADR-035 (uv is
  additive for the generated service), ADR-048 (co-installation groups), ADR-049
  (the served image installs only what it runs), ADR-052 (dependency merges on green),
  D-05 (compatible-release pinning)

## Context

ADR-053 made the repository root a uv project and committed `uv.lock`, locking the
tooling the contributor needs: `pre-commit`, `copier`, `jsonschema`, `pytest`. It
left the workflows alone — "moving the scaffold, policy and CI-Examples lanes to
`uv sync --locked` is a separate PR, reviewed on its own." This is that PR.

Ten jobs still installed that same tooling the old way:

```
pip install "copier>=9.0.0" jsonschema
pip install pytest pyyaml "copier>=9.0.0" jsonschema
pip install pre-commit
pip install --quiet pyyaml
```

No upper bound, no hashes, a different answer on every run. `ci-examples.yml`
documents what that costs: `pyarrow` resolved to a release that crossed the numpy
boundary and every open PR went red at once with nothing wrong in any of them. The
lock that would have prevented the next one of those existed, and nothing read it.

Two things make "just switch everything to uv" wrong, and the decision below is
shaped by them:

- **Some lanes exist to prove the adopter's route, and the adopter uses pip.** The
  README quick start tells an adopter to `pip install "copier>=9.0.0"`; the README
  standard (`check_readme.py`) admits that range only because a workflow installs
  exactly it. The dependency scan resolves with pip on the Python the service image
  runs (ADR-049, `test_dependency_scan_python.py`); CI-Examples installs the
  service's `~=` ranges the way an adopter's `pip install -r` would. Moving those to
  uv would keep them green and stop them proving the thing they are named after.
- **A job's `python` and `pip` are part of the job.** `uv sync` builds a `.venv`; a
  job that then runs `python3 scripts/x.py` or `pip` would silently use a different
  interpreter than the one `actions/setup-python` installed.

## Decision

**1. Jobs whose tooling is only OUR tooling install it from `uv.lock`.** One script,
`scripts/install_ci_tooling.sh`, exports the root `dev` group with hashes and
installs it into the interpreter on `PATH`. The job's `python`, `pip` and `PATH` are
unchanged; only the tooling's versions stop floating. Nine jobs use it; the Self-audit
job, which has no `setup-python`, runs its one gate through `uv run --frozen`.

**2. The script cannot succeed by installing nothing.** `uv export --frozen` is
written to a file and checked for hashes before anything is installed (a pipe would
turn a failed export into an installer reading an empty list — a success that
installs nothing, which is how a gate passes because its parser is missing);
`--require-hashes` checks every wheel against the lock; `--strict` rejects an
inconsistent set.

**3. `astral-sh/setup-uv` is pinned by commit SHA, like every other action here, and
to one exact uv version.** `uv.lock` pins the packages; the `version:` input pins the
installer, so a uv release cannot change what the jobs do. The uv version appears in
every job that needs it, so a governance test requires them to agree.

**4. These lanes stay on pip, each for a reason:**

| Lane | Why it is not the lock |
| --- | --- |
| `pr-smoke-lane.yml` | The per-PR proof of the README quick start. It installs exactly `pip install "copier>=9.0.0"`, so a copier release that breaks the adopter's route fails here the day it ships. It is also what lets the README standard admit that range. |
| `template-context-tests.yml` | Copier is co-installed with the generated service's dev stack in one environment. Locking it there would collide with the service's `~=` pins (ADR-048). A separate decision. |
| `ci-examples.yml`, `example-e2e` | The test stack is resolved from the service's and the example's own `~=` ranges, as an adopter's install would. `.venv-test` therefore stays unlocked on purpose — this settles the deferral in ADR-053's Consequences. |
| dependency scan, `eda-heavy-smoke.yml` | They exist to ask **pip** what it would install on the image's Python (ADR-049). |

**5. The gates hold it.** `templates/tests/governance/test_ci_tooling_locked.py`
fails when a workflow `pip install`s a package that `uv.lock` owns outside the lanes
above (each allowlisted with its reason, and an entry that no longer applies fails
too), when the installer runs before `setup-uv` and `setup-python`, when `setup-uv`
is not SHA-pinned or jobs disagree on the uv version, when a job calls `uv` without
installing it, and when the installer loses `--frozen`, `--require-hashes`,
`--strict`, or reads its export through a pipe.

## What was measured

Executed on 2026-10-31, not reasoned about.

- **Installer, end to end,** into a fresh Python 3.11 standing in for `setup-python`'s:
  37 hashed packages; `copier 9.19.1`, `jsonschema 4.26.0`, `pyyaml 6.0.3`,
  `jinja2 3.1.6`, `pytest 9.2.0`, `pre-commit 4.7.0`; `copier` and `pre-commit`
  resolve on that interpreter's `PATH`.
- **Lock untouched:** `uv lock --check` passes; no dependency, pin or `uv.lock` line
  changed.
- **Before the workflow edits, the new test failed** on the three things it guards
  (floating installs, no installer in use, no `setup-uv`); after, 15 pass.
- **Each guarantee fails when removed.** Thirteen reversions — a job back on a floating
  `pip install`, an unpinned `pip install pyyaml`, `setup-uv` removed, `setup-python`
  removed before the installer, `setup-uv` on a floating tag, a differing uv version,
  `version: latest`, a job calling `uv` before installing it, and the installer without
  `--frozen`, without `--require-hashes`, without `--strict`, piping its export, and a
  stale allowlist entry — each fail a specific test. A first run exposed one gap:
  the stale-allowlist test read the whole workflow, so the comment explaining the
  exception kept the exception alive after its command was gone. It now reads only
  commands.

## Consequences

- Ten jobs stop floating. Dependabot's `uv` entry for `/` (ADR-053) bumps the lock; its
  PR is validated by these same jobs, which is ADR-052's "merge on green" applied to
  the tooling.
- Two kinds of copier now run in CI on purpose: the **locked** one in the gate and
  scaffold lanes, and the **latest within the README's range** in `pr-smoke-lane`,
  every PR. The second is the canary for the adopter's route.
- The uv version lives in nine jobs. Dependabot cannot bump a `version:` input, so
  raising it is a manual change in all of them; the governance test makes a partial
  edit fail instead of leaving jobs on different installers.

## What this does NOT change

- `templates/service/`, the Dockerfile, the generated service's `requirements*.txt`
  and ADR-035: the payload still ships no `uv.lock` and pip stays a first-class
  adopter path. Whether generated services should be uv-first is a product decision
  for its own ADR.
- Required status checks: job and workflow names are unchanged.
- The README and its standard.

## Alternatives considered

- **A composite action for the two steps.** One definition instead of nine copies, but
  Dependabot's `github-actions` entry watches `/` and `/templates/service`, not
  `.github/actions/*`: the SHA pin inside it would be unwatched and rot, exactly what
  ADR-052 is built to avoid. The script is the single definition instead.
- **`uv sync` and put `.venv/bin` on `PATH`.** Replaces the job's `python` and removes
  its `pip` for every later step; several of these jobs run scaffolded-service tooling
  that expects both.
- **Pin versions inline in each workflow** (`pip install "copier==9.19.1"`). A second
  owner for every pin, which ADR-053 Decision 2 rejects: Dependabot's PR bumping the
  lock cannot also bump twelve copies.
- **Move everything, adopter lanes included.** Keeps CI green and stops it testing the
  route adopters take — see Context.
