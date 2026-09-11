# Changelog

All notable changes to the ML-MLOps Production Template are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

> **Versioning policy is now governed by [`docs/RELEASING.md`](docs/RELEASING.md).** Adopter migration guidance lives in
> [`MIGRATION.md`](MIGRATION.md). Verified-execution evidence lives in [`VALIDATION_LOG.md`](VALIDATION_LOG.md). Tags
> `v1.0.0`–`v1.12.0` are immutable historical audit snapshots, archived under `archive/v1.x` per
> [ADR-045](docs/decisions/ADR-045-tag-namespace-separation.md) so version-resolving tooling stops picking them over the
> active line; the commits, trees and signatures are unchanged. The active public line is now `v0.x` hardening; `v1.0.0`
> is reserved for the first real cloud E2E validation across GKE and EKS.

---

## [Unreleased]

### Changed — MLflow pinned to `~= 3.16.0`, and ADR-047's own conclusion corrected

- The migration is taken. The **twenty** MLflow entries are removed from
  `.security-baselines/trivy-fs.trivyignore` — 27 accepted findings down to 7,
  every CRITICAL among the twenty — and nothing MLflow-related is suppressed any
  more: a new finding blocks. The 2026-12-08 forcing function is discharged
  three months early.
- **ADR-047 said the model round-trip was intact. It was not.** It verified a
  `Pipeline` with no `ColumnTransformer`; the pipeline the template *ships* has
  one, and MLflow 3.x fails on it:

  ```text
  MlflowException: The saved sklearn model references untrusted types.
  Root error: Untrusted types found in the file:
  ['sklearn.compose._column_transformer._RemainderColsList']
  ```

  3.x serialises sklearn models with `skops`, which refuses types it does not
  recognise, and the `ColumnTransformer`'s `remainder` produces that one. The
  section of ADR-047 headed *"What was NOT verified"* named the full pipeline as
  the gap — **and the gap was where the defect was.** A measurement that skips
  the integration is not a smaller version of the real one.
- Fixed by deriving `skops_trusted_types` from the fitted pipeline via
  `skops.io.get_untrusted_types`, **not** hardcoding it: the template instructs
  adopters to edit `model.py`'s preprocessor, so a fixed list would go stale
  through documented use — and would fail at the *end* of a training run.
  `cloudpickle` and `pickle` also round-trip identically and were rejected as
  the default: MLflow warns they execute arbitrary code on load, and choosing
  the unsafe serialiser to save one line is not a trade to make for every
  adopter. `MIGRATION.md` documents it as the escape hatch.
- **Verified end to end** against a real generated service — full ~700-line
  pipeline, quality gates passed, `Trusting 1 skops type(s) from this run's own
  pipeline`, model registered, then reloaded through **all three** paths
  (`sklearn.load_model`, `pyfunc.load_model`, and
  `models:/…@champion`, the alias `promote_to_mlflow.py` depends on) — all three
  reproducing the joblib artefact's predictions exactly.
- The five file-store defaults now say `sqlite:///mlflow.db`. This only means
  anything because ADR-050 made the config reachable first: **four of the five
  were values the trainer never read**, so this step executed on its own would
  have edited five files, changed no behaviour, and left 3.x still refusing the
  file store — through a migration that looked complete.
- `configs/profiles/local.yaml` was also the one site spelled `file://./mlruns`
  while every other said `file:./mlruns`. Both are gone.
- `mlruns/` and `mlflow.db` were already gitignored in both the repo and the
  payload. `MIGRATION.md` carries `mlflow migrate-filestore` for existing runs.

### Fixed — client/server compatibility was unverifiable, not unverified

- The follow-up plan asked to *"verify client 3.16 ↔ the server staging/prod
  uses"*. There was nothing to verify: the template deploys no tracking server,
  and both compose files that ship one used `ghcr.io/mlflow/mlflow:latest` — a
  server that can change **major version** between two `docker compose up` runs.
  Compatibility was not unverified; it was unverifiable by construction, in a
  repository that mandates immutable tags and Kyverno digest verification
  everywhere else.
- Both are pinned to `v3.16.0`, the version the client is pinned to.
  `minio/minio:latest` and `minio/mc:latest` are pinned to their current
  `RELEASE.*` tags.
- Dependabot's docker ecosystem watched only `/templates/service`, so those four
  tags were unpinned **and** unwatched. It now covers `/`,
  `/templates/service` and `/templates/service/infra`.
- `test_gate_scope_ratchet.py`'s baseline floor was lowered 27 → 7, deliberately
  and in a reviewed diff. A baseline shrinking because the debt was *paid* is the
  one case where lowering a floor is correct — and the ratchet still made it a
  decision rather than a number nobody read.

### Fixed — Dependabot was generating pull requests this repository cannot merge

- `.github/dependabot.yml` set no `versioning-strategy`, and pip's default is
  `widen`: it rewrites `~= 1.5.0` as `>=1.5,<1.10`. This repository **mandates**
  compatible-release pins (D-05 — numpy 2.x silently corrupts joblib models), so
  every such PR was unmergeable on arrival. **Ten were opened and closed
  unmerged** (#130, #132, #133, #134, #136, then #146, #149, #151, #153, #155)
  before anyone asked why they kept coming. `versioning-strategy: increase` now
  bumps the pin in place — `~= 1.5.0` → `~= 1.9.0` — which is reviewable rather
  than automatically wrong.
- The service and its EDA lane had **separate** pip entries, so a package
  declared in both got two independent PRs — and the pin-coherence gate
  (ADR-048) correctly rejects each on its own. #148 raised pandas only in the
  EDA lane and #152 only pyarrow, both failing with

  ```text
  group 'generated-service' disagrees on 'pyarrow':
    ~=25.0.1   templates/service/eda/requirements.txt
    ~=18.0.0   templates/service/requirements.txt
  ```

  That is the gate working as designed on a PR that could never have been
  mergeable. One `directories:` entry with a group now lands the shared bump in
  a single PR, in a shape that can pass.
- **The gate landed four days before the first real divergence arrived**, and
  caught it unprompted on someone else's PR. That is the only kind of evidence
  worth having about a control.

### Added — Dependabot coverage is enforced, not just requested

- `.github/dependabot.yml` carried this warning in a comment: *"Adding a
  requirements file without adding an entry here puts it back in the blind
  spot."* **Nothing enforced it.** The pip ecosystem was absent entirely once,
  while the repository carried four requirements files — three nominal controls
  over Python dependencies inert at the same time.
- `scripts/check_dependency_pin_coherence.py` now also fails when any tracked
  requirements file sits outside every pip entry's directories, understanding
  both `directory:` and `directories:`. Two new controls cover it: dropping the
  EDA directory from the entry, and adding an unwatched requirements file — the
  second trips the group check and the coverage check at once, which is what
  should happen.
- A stated risk with no control is the shape this whole line of work rejects.
  It was stated, in the file, by whoever wrote the entry.

### Fixed — the MLflow config was parsed, validated and read by nothing

- `MLflowConfig` declared `tracking_uri`, `experiment_name` and `enabled`. All
  three loaded from `configs/config.yaml`, all three consumed by nothing:
  `Trainer._log_to_mlflow` called `mlflow.set_experiment()` and **never**
  `mlflow.set_tracking_uri()`. Measured:

  ```text
  config.yaml says:            'sqlite:///mlflow.db'
  config.mlflow.tracking_uri = 'sqlite:///mlflow.db'   <- parsed fine
  mlflow.get_tracking_uri()  = 'file:///…/mlruns'      <- MLflow never saw it
  ```

- `cli.py` admitted it in a comment on the line that did it —
  `ServiceConfig.from_yaml(args.config)  # Validate config exists and is parseable`
  — it loaded the whole config to check it parsed, then discarded the object.
- So the `staging` and `prod` profiles naming an in-cluster tracking server did
  **nothing** for training. Retraining in CI worked only because the workflow
  exports `MLFLOW_TRACKING_URI`, the one channel MLflow reads on its own.
- **This corrects ADR-047's migration plan.** Its step 2 was "the five
  file-store defaults → `sqlite:///mlflow.db`". Four of those five are values
  the trainer never consulted, so on its own that step would have changed
  nothing and MLflow 3.x would still have refused the file store — through a
  migration that looked complete.
- `MLflowConfig.resolve_tracking_uri()` states the precedence once:
  `MLFLOW_TRACKING_URI` wins over the file, deliberately, because the deploy
  chain sets it from a secret and a committed file must not be able to redirect
  a production run's tracking. `--experiment` now overrides the config value
  instead of mutating a module global from inside `if __name__ == "__main__"`.
- Verified end to end in a generated service: with `sqlite:///mlflow.db` in
  `config.yaml` and no env var, the run went to `mlflow.db` — experiment
  created, one run, model registered. Before, it went to `./mlruns`.

### Added — a gate for the whole class, and the class is wider than MLflow

- **24 of 51 declared config fields are read by nothing**, most of them
  settable in `configs/*.yaml`. The worst is not MLflow: `config.yaml` marks
  `data.categorical_features` *"TODO: Replace with your actual column names"*
  while `model.py` marks `CATEGORICAL_FEATURES` *"TODO: List your categorical
  feature column names"* — **two places to declare one thing, and only the
  Python one is consulted.** Feature selection is the first thing an adopter
  edits.
- Also unwired: `model.resampling_strategy` (the SMOTE toggle —
  `build_pipeline()` never constructs `ResampleClassifier`),
  `quality_gates.promotion_threshold` (`promote_to_mlflow.py` implements no
  baseline comparison, so the promotion-gate delta it documents is
  unenforced), and `quality_gates.latency_sla_ms`, whose own description
  claimed *"Read by the load-test target"* — `tests/load_test.py` does not read
  it. Both claims are corrected in `config.py`.
- `scripts/check_config_is_read.py` (gate 18) fails on any field that is
  neither consumed nor recorded in its `UNWIRED` table with a reason, and the
  count is pinned: it may shrink, never grow. The fields **stay** — the
  pipeline *should* honour `categorical_features`, so deleting the declaration
  would resolve the inconsistency in the wrong direction, removing the promise
  instead of keeping it.
- `config.yaml` carries a `⚠ NOT YET WIRED (ADR-050)` banner on each affected
  block. A gate protects the repository; the banner protects the adopter, who
  never runs the gate.
- **The first version of this gate would not have caught the bug it was written
  for**, and that was found by running it against `main` rather than trusting
  it on a fixed tree. It grepped for the field name, and `promote_to_mlflow.py`
  has a local variable called `tracking_uri` — so it reported the identical
  "38 read" on the broken tree. Detection is attribute access via AST now. It
  also counted **tests** as consumers: the new regression test's own stub has
  `self.tracking_uri`, which alone made the production field look consumed
  again. Excluding tests is what surfaced the two quality-gates entries above.
- See [ADR-050](docs/decisions/ADR-050-configuration-must-be-read.md).

### Changed — the served image installs only what it runs

- `requirements.txt` is what the Dockerfile installs, and it carried **mlflow,
  optuna, pytest, pytest-cov, locust and httpx** — none of which any code in the
  image imports. Measured:

  | | packages | fixable CRITICAL/HIGH | of which CRITICAL |
  | --- | ---: | ---: | ---: |
  | before | **151** | **24** | **7** |
  | after | **35** | **4** | **0** |

  The 20 that disappear are all MLflow. The 4 that remain (starlette ×3,
  pyarrow) are already accepted with dates in the security baseline.
- ADR-047 said *"`app/` does not import it, so none of these findings ever
  touched the inference path."* The first half is true and **the conclusion does
  not follow**: installed is not imported. A scan of the image reports what is on
  disk, an attacker who reaches code execution finds what is on disk, and the
  SBOM attests what is on disk. The advisories were in the pods the whole time.
- Three files now, one direction of inclusion: `requirements.txt` (serving),
  `requirements-train.txt` (+ mlflow, optuna), `requirements-dev.txt` (+ pytest,
  httpx, locust, jsonschema, linters). A training environment is a superset of a
  serving one; the reverse must never be true.
- **`scripts/check_dependency_partition.py`** (gate 17) walks the import closure
  of everything the image runs and fails on any unguarded import of a
  training-or-dev-only distribution. The entrypoints are **parsed out of the
  Dockerfile** — its smoke-import steps and its `CMD` — because a hand-kept list
  would drift from the image the first time someone added a CronJob.
- **This does not claim the CVEs are gone.** They are still reported against
  `requirements-train.txt` by this repo's own dependency scan, correctly: the
  risk left the inference pods, it did not evaporate. What changed is that a
  training-only advisory is no longer also a production-inference advisory — and
  the MLflow major-version decision (ADR-047) is no longer coupled to the
  serving image's security posture, so it can be taken on its merits.
- See [ADR-049](docs/decisions/ADR-049-runtime-training-dependency-partition.md).

### Added — the golden path now scans the image it builds

- The generated service's `ci.yml` has always run `trivy image` and failed on
  CRITICAL,HIGH. The golden path built an image and **never scanned it** — the
  lane that is meant to be the trust anchor applied a weaker bar than the
  service it generates. The scan runs *before* signing: an image that would fail
  the adopter's own gate must not be Cosign-signed, attested and Kyverno-admitted
  by this workflow.
- With the Python set down to 35 packages, the base image became the remaining
  source of fixable findings — `CVE-2025-47273` (setuptools 70.3.0) and
  `GHSA-6v7p-g79w-8964` (msgpack 1.1.2, vendored inside pip). The runtime stage
  now **removes** system pip, setuptools, wheel and pkg_resources. Removing beats
  upgrading: a serving image installs nothing at runtime, so a current setuptools
  would be the same unused surface carrying a later CVE. Verified before removal
  that every entrypoint imports cleanly with all three blocked from
  `sys.meta_path`.

### Fixed — the split broke two lanes, and the second one taught the gate a check

- `.github/workflows/template-context-tests.yml` installed only
  `templates/service/requirements.txt` and relied on httpx and locust arriving
  through it. Found by reading, fixed before pushing.
- **`scripts/test_scaffold.sh` had the same defect and CI found it — after the
  first fix had already shipped.** Its smoke chain installed the runtime set
  into a venv and then ran `pytest`, which resolved to the *system* interpreter
  outside that venv and reported `ModuleNotFoundError: No module named 'numpy'`
  — blaming a package that *was* installed, because the one that was not
  (pytest) never got as far as being missing.
- So `check_dependency_partition.py` gained a third check: **no lane may install
  the runtime set and then invoke a tool only `requirements-dev.txt` provides.**
  Scoped per *job*, not per file, because `validate-templates.yml` installs the
  service requirements in one job and runs ruff, mypy and bandit in others — a
  file-wide check flagged five pairs that never share a runner, and a gate that
  cries wolf five times gets deleted rather than obeyed. `examples/minimal` is
  excluded by path: it is its own co-installation group (ADR-048) and its
  `pytest ~= 9.1.1` is legitimately different.
- Both real failures are now regression-tested by reintroducing them.
- `copier.yml`'s closing message and the `README` quick start told the adopter to
  install and then train; training needs `requirements-train.txt`.

### Fixed — the split would have broken this repository's own test lane

- `.github/workflows/template-context-tests.yml` installed only
  `templates/service/requirements.txt` and relied on httpx and locust arriving
  through it. After the split that lane would have failed every TestClient test
  with `No module named 'httpx'`. It installs `requirements-dev.txt` now —
  verified in a clean venv: httpx, locust, jsonschema, pytest and `TestClient`
  all present, mlflow and optuna both absent.
- The `README` quick start told the adopter to install and then train; training
  needs `requirements-train.txt`. Same for the scaffolder's closing "next steps".

### Fixed — the CLI was dead, and `dvc repro` had never worked

- **`src/<slug>/evaluation.py` and `src/<slug>/evaluation/` both existed.** A
  package always beats a module of the same name, so `<slug>.evaluation`
  resolved to the package — whose `__init__.py` held one docstring. `cli.py`
  opens with `from .evaluation import ModelEvaluator` and therefore raised
  **`ImportError: cannot import name 'ModelEvaluator'`** on import. The whole
  CLI was unreachable and all 224 lines of `evaluation.py` were dead. It
  arrived when `champion_challenger.py` was added as a package beside a module
  already there; the package docstring has read "base metrics +
  champion/challenger comparison" ever since, describing a layout that only now
  exists. Nothing warns — this is specified import behaviour.
  `evaluation.py` → `evaluation/metrics.py`, re-exported.
  `tests/test_no_module_package_shadowing.py` scans the whole tree, not the one
  pair that broke.
- **`dvc.yaml` declared four stages and three could not run.** `validate` ran
  `training/validate_data.py` (never existed); `featurize` ran
  `training/features.py` (real file, no `__main__`, no parser — exits 0 having
  produced nothing, and DVC then fails on the missing output and blames the
  output); `train` ran `training/train.py` as a script (`ImportError`); and
  `evaluate` pointed at `training/evaluate.py`, also non-existent. `dvc repro`
  had never completed in any generated service. **No test read the file.**
  The two "missing" stages were never missing capability — `train.py` validates
  with `ServiceInputSchema` and applies `FeatureEngineer` itself. Now two
  stages, through the `cli.py` subcommands that already existed and were
  unreachable, both executed end to end. `tests/test_dvc_pipeline_is_runnable.py`
  checks that each stage's target exists, is invoked in a form that works, and
  has an entrypoint at all — the third check is the one that catches `featurize`.

### Fixed — the drift job silently ran with schema validation off

- `drift_detection.py` imports `..schemas.ServiceInputSchema` inside a
  `try/except ImportError`. Run as a **script** — which is what the shipped
  `drift-detection.yml` workflow and five agentic documents did — it does not
  crash. It binds the schema to `None`, and `validate_drift_dataframe` skips
  every check while logging a warning whose own text says that branch
  "exists only for template-level tests". Measured: script form →
  `ServiceInputSchema is None`; module form → the schema. So every generated
  service ran drift detection with input validation off, producing what that
  function's docstring calls **"a phantom alert (or worse, a phantom
  all-clear)"**. All invocations are now the module form.

### Fixed — the guard for this defect class was narrower than the defect

- `test_makefile_entrypoints.py` read the Makefile and nothing else. The
  Makefile was fixed, the guard went green, and the identical broken invocation
  stayed live in **thirteen other places** — the retraining workflow (whose
  *same file* used the correct form four steps later), `dvc.yaml`, both
  READMEs, the drift workflow, a runbook, and six agentic documents across
  their three copies. A control scoped to one file while the defect lives
  across workflows, pipeline definitions and documentation is the same shape as
  the bug it was written to catch.
- Now `tests/test_python_entrypoint_invocations.py`, over every shipped surface
  that can invoke a repository Python file: **39 invocations checked, up from
  3**. It asserts the scan was non-empty, because a glob that matches nothing
  reports as a pass.
- Found while writing it: the widened guard had a hole of its own. It discarded
  any path still containing `{` after expansion, and in the *template* repo the
  package directory is literally named `{@ service_slug @}`, so every
  Copier-token invocation expanded to itself and was thrown away — `dvc.yaml`
  was scanned and silently skipped, and the negative control passed when it
  should have failed. Existence on disk is now the only test.
- ADR-033 carries a dated correction: the `local-loop` recipe it records could
  never have run.

### Fixed — the EDA lane and the service could not be installed together

- `templates/service/requirements.txt` pinned `scikit-learn ~= 1.5.0` and
  `pandera ~= 0.23.0`; `templates/service/eda/requirements.txt` pinned
  `~= 1.9` and `~= 0.33`. Together: **`ResolutionImpossible`**.
- **pip never said so.** `docs/TUTORIAL.md` installs them in two separate
  commands, so the second silently *upgrades* the first. Measured on a clean
  venv following the documented sequence: scikit-learn 1.5.2 → **1.9.0**,
  pandera 0.23.1 → **0.33.1**. The adopter trained on 1.9 and served from an
  image built with 1.5.2 — the joblib version skew `~=` pinning (D-05) exists
  to prevent, through the one door nothing was watching.
- Dependabot merged the bump on the EDA lane (#116, #135) and it was closed on
  the service lane (#132, #134). Each call was defensible alone; **no gate
  compared the two files**, so the gap was invisible from the moment it opened.
- **`scripts/check_dependency_pin_coherence.py`** (gate 16) groups requirements
  files by the environment they are installed into and fails when a group
  disagrees on a shared pin — *identical* specifiers, not merely compatible
  ones, because `~=1.26` (>=1.26,<2.0) and `~=1.26.0` (>=1.26.0,<1.27.0) read
  alike and resolve differently. It also fails on any tracked requirements file
  that belongs to **no** group, so a new file cannot join unchecked. Tested by
  reintroducing the defect, not only by passing on a clean tree.
- The EDA lane declared eight packages and imported four of them. scipy,
  scikit-learn, pandera and matplotlib were dead weight — **two of those four
  dead pins were the ones that diverged**. The only mention of pandera in the
  pipeline is the string literal it writes *into* the generated schema file.
- **pyarrow was missing and nobody could have seen it statically.** The
  pipeline writes `baseline_distributions.parquet`; pandas reaches for pyarrow,
  so no `import pyarrow` exists to find. The lane only ever worked because the
  service set happened to carry it — installed standalone, as its own README
  presents it, it crashed in Phase 0. Found by running it. AST proves a package
  is *used*, never that the declared set is *sufficient*.
- See [ADR-048](docs/decisions/ADR-048-requirements-co-installation-groups.md).

### Verified — the full training pipeline runs end to end

- ADR-047 recorded "the full training pipeline end to end" as **not verified**;
  four configuration guards stood between the CLI and the MLflow call. All four
  were satisfied and the ~700-line path was run whole: EDA gate → split policy
  → Optuna HPO → cross-validation → fit → evaluation → quality gates →
  `model.joblib` + `training_manifest.json` → an MLflow run carrying metrics,
  params, tags and a registered model. The `roc_auc >= 0.8` gate correctly
  **failed** the run at 0.796 — the gate works, on a deliberately weak signal.
- Golden Path E2E (L3) was run on `main` for the first time since the registry
  fix (#137) merged: **green** — scaffold, build + sign by digest, kind +
  Kyverno admission + smoke, audit trail.

### Fixed — `make train` could not have worked in any generated service

- Three Makefile targets ran the trainer as a plain script:
  `python src/<slug>/training/train.py`. `train.py` opens with
  `from ..config import QualityGatesConfig`; a file inside a package run as a
  top-level script has no package context, so Python raises
  **`ImportError: attempted relative import with no known parent package`**
  before the first line of logic. Found by actually running it, while
  verifying something else.
- Now invoked as a module (`python -m src.<slug>.training.train`), which is
  what the imports require.
- **Nothing tested it**, because the suite tests the code the recipes call and
  never the calling. `test_makefile_entrypoints.py` closes that: for every
  Makefile recipe that runs a repository Python file, a *script* invocation is
  only valid if the file has no relative imports. Static — it does not run
  training, which needs data, EDA artefacts and a configured split.

### Added — the fifteen gates now have a test, and a floor they cannot slip below

- This repository relies on **fifteen gates** to detect its own drift, and
  **none of them was tested**: `grep -rl "import scripts"` over both test trees
  returned nothing. Every control that catches a regression could itself
  regress, silently.
- Not hypothetical. Twice in one week a gate narrowed and reported success at
  its smaller size: `check_control_claims.py` scanned **36** anti-patterns
  instead of 38 after two table rows gained a Jinja `raw` wrapper its pattern
  no longer matched, and `check_doc_coherence.py`'s overlay check matched
  `N overlays` but not `N overlay renders`. Both are the shape those gates
  hunt — a control narrower than the surface it guards.
- `test_gate_scope_ratchet.py` runs every gate, reads the count it reports and
  compares it against a **recorded floor** — a ratchet at the measured value,
  exactly like `fail_under` for coverage. Growth passes; shrinkage fails and
  has to be lowered deliberately, in a diff someone reviews.
- Two gates did not report a scope at all. `check_baselines_expiry` now says
  how many entries across how many files, and `check_vendored_runtime_drift`
  how many vendored files and directories it compared. **A gate that reports
  "OK" without saying how much it looked at cannot be told apart from one that
  looked at nothing.**
- The gate list is read from the Makefile, not retyped, and a new gate must
  declare a floor or a stated reason it has none — otherwise adding one would
  silently opt it out of the ratchet, narrowing the registry the same way.

### Fixed — a documentation sweep, and a gate I narrowed myself two PRs ago

- **`check_control_claims.py` was scanning 36 anti-patterns instead of 38.**
  Wrapping the D-32 and D-34 rows in a Jinja raw block — needed so Copier does
  not render the tokens they document — changed their prefix from `| D-34 |`
  to `| {% raw %}D-34 |`, and the row pattern no longer matched. The scan
  narrowed and said nothing. My change, two PRs ago.
- The pattern now tolerates the wrapper, and the gate **asserts it matched
  every D-NN up to the highest**: a scan that sees fewer rows than the table
  holds is now a failure rather than a smaller number. Recovering the two rows
  also surfaced a sixth path-shaped enforcer that had been invisible.
- **C8 missed the singular form.** It matched `N overlays` but not
  `N overlay renders`, so two living documents still said 6 — the check added
  to stop that count drifting missed an instance of it on the day it shipped.
  `README.md` and `CONTRIBUTING.md` now say "every overlay rendered", which
  cannot go stale.
- `README.md` §"Recent hardening (v0.14.0 → v0.15.x)" was **eleven minor
  versions behind** `VERSION` (0.26.0). The version range is gone; the section
  lists releases already.

### Fixed — L3 had been red for eighteen consecutive weeks and nobody was told

- `README.md` offers adopters **"L1 + L2 + L3 are your contract"**. The L3
  golden-path E2E — scaffold → build → sign → attest → deploy to kind →
  `/predict` 2xx — failed **every scheduled run from 2026-05-11 to 2026-09-07**.
  Last success: **2026-05-04**.
- It was invisible by construction: `golden-path.yml` runs on a weekly schedule
  and on demand, **never on a pull request**, so its red appeared in no PR's
  checks and blocked nothing.
- Its own step summary made it worse: five ✓ marks printed under
  `if: always()`, so a failed run rendered a summary claiming every stage
  passed. It now reports each job's real result.
- **A red L3 now opens an issue** — one issue, commented rather than
  duplicated, so eighteen weeks of failure is one thread instead of eighteen.
  Same mechanism the drift-detection workflow already used.

### Fixed — the two defects inside that failure

- **`cronjob-performance.yaml` shipped two containers with no `securityContext`
  at all** — no `allowPrivilegeEscalation: false`, no `runAsNonRoot`, no
  dropped capabilities — while every namespace this template creates is
  labelled `pod-security.kubernetes.io/enforce: restricted` (D-29). The
  golden path had been printing the violation weekly since May.
- **Nothing tested it.** `grep -rl allowPrivilegeEscalation` over the test
  trees returned nothing: the invariant was declared in `AGENTS.md`, labelled
  on every namespace, and asserted nowhere. New
  `test_pod_security_standards.py` checks every container and initContainer of
  every workload in `k8s/base`, discovered rather than listed, and fails when
  it discovers none.
- The apply-result check **decided on leftover lines instead of on meaning**,
  and that is the root cause of the eighteen weeks. It subtracted the lines it
  recognised and failed if anything remained — but `kubectl` always leaves
  something: it writes warnings to the same stream, and every
  `no matches for kind` error is followed by `ensure CRDs are installed first`,
  a continuation line carrying no kind name that survived every filter.
  My first attempt at this fix removed only the warnings and stayed red for
  exactly that reason. The check now collects the **kinds** that failed to map
  and tolerates the run only if every one is a CRD this cluster is known not
  to have.
- The golden path's own CI patch also **appended a second `ENVIRONMENT`** to a
  container whose overlay already set one, producing a
  `hides previous definition` warning that the old check counted as residue.

### Fixed — Dependabot could not satisfy the drift gate, and shipped three dead linters

- `dependabot.yml` had **one** `github-actions` entry, at `/`. `directory:` is
  resolved literally, so the nine workflows under
  `templates/service/.github/workflows/` were never scanned — the same blind
  spot the file already documents for terraform, unapplied to actions.
- That had a second effect: `check_cicd_template_drift.py` requires the runtime
  and template copies to pin identical versions, so **every action bump
  Dependabot opened was half a change and failed that gate by construction**.
  Scanning both directories lets one PR satisfy it. The three open bumps
  (checkov-action, hadolint-action, codeql-action) are applied here in both
  copies.
- **`black`, `isort` and `flake8` were still declared** as dev dependencies of
  every generated service. ADR-044 replaced all three with ruff in the
  pre-commit config and nobody removed them, so each scaffolded service
  installed three linters it never runs — and did **not** declare `ruff`, the
  one it does. Dependabot noticed before we did: it opened a PR bumping
  flake8. The three are replaced by `ruff ~= 0.15.15`, matching the version
  the service's pre-commit config already pins.

### Fixed — the supply-chain gate had never examined a Python dependency

- `Self-audit` §"Trivy filesystem scan" was blocking (`exit-code: 1`,
  `CRITICAL,HIGH`) and scanned **nothing**. Its own log said so on every run:

  ```text
  INFO  Number of language-specific files  num=0
  WARN  [report] Supported files for scanner(s) not found.  scanners=[vuln]
  ```

  Trivy reads a requirements file only when versions are pinned **exactly**.
  This repo mandates `~=` — deliberately, since numpy 2.x silently corrupts
  joblib models — and ships no lock file. A blocking gate that always passes
  is worse than no gate: it produces the assurance without the check.
- **Three nominal controls over Python dependencies were inert at once**: that
  scan, a `dependabot.yml` with no `pip` ecosystem despite four requirements
  files, and repository vulnerability alerts that were **disabled**.
- `scripts/resolve_python_dependencies.py` asks pip to *resolve* each tracked
  requirements file without installing (`--dry-run --report`) and writes the
  concrete versions for trivy. Resolving in CI rather than committing a lock
  file is deliberate — a lock under `templates/service/` would ship to every
  adopter and freeze their transitive tree to ours.
- **Switching it on surfaced 24 fixable CRITICAL/HIGH findings** (7 CRITICAL)
  that had been there all along: 20 in `mlflow`, 3 in `starlette`, 1 in
  `pyarrow`. None is fixable inside its declared pin range — every fix needs a
  major bump. They are accepted in `.security-baselines/trivy-fs.trivyignore`
  with a **2026-12-08 expiry** and a per-cluster review question, so any *new*
  finding blocks immediately.
- `.github/dependabot.yml` gains **four `pip` entries** (`directory:` is
  resolved literally, exactly as for terraform). Vulnerability alerts and
  Dependabot security updates are now **enabled** on the repository.

### Fixed — two smaller things the same investigation turned up

- The step referenced `.security-baselines/.trivyignore`, a file whose own
  header read *"Baseline starts empty. The first hard-fail run surfaces real
  findings."* That run never came. It is replaced by
  `trivy-fs.trivyignore`, matching its sibling's naming, and the single
  overloaded step is now two that each say what they do — a secret scan over
  `templates/`, and a dependency scan over the resolved versions.
- `check_baselines_expiry.py` rejected every **GHSA** identifier as malformed;
  its id pattern required digits. Advisory ids are how trivy reports findings
  with no CVE assigned yet — precisely the ones worth tracking.

### Fixed — twelve stale surface counts in AGENTS.md, and the gate that only watched CLAUDE.md

- The generated-adapter tree in `AGENTS.md` claimed **18 rules / 26 skills /
  18 commands** for each of the four surfaces. The live counts are
  **19 / 27 / 20**. Twelve wrong numbers in the repository's canonical
  architecture document — which is also vendored into every generated service.
- `README.md` stated the same surface as *"18 rule files, 26 skills, and 18
  workflows"*: three more, in the first document an adopter reads.
- Cause: **C4 reconciled `CLAUDE.md` and nothing else.** That is the same gap
  C4's own docstring describes closing one document over, for the service copy
  of `CLAUDE.md`.
- C4 now also reconciles `README.md` and the adapter tree in both `AGENTS.md`
  files. Counting had to be shape-aware: `.claude/skills/` and `.devin/skills/`
  use one directory per skill, `.cursor/` and `.codex/` use one file each, and
  two of the four carry an `INDEX.md` that is not a skill — naive counting
  gives 28 for one adapter and 27 for the next, which is how the numbers
  drifted apart.

### Fixed — a comment asserting the opposite of the file beside it

- `templates/service/.github/workflows/ci.yml` said *"mypy not in pre-commit
  hooks yet — kept separate for now. TODO: add mypy to
  `.pre-commit-config.yaml` once baseline clean."* The service's
  `.pre-commit-config.yaml` **has had a mypy hook all along**, so every
  scaffolded service shipped a TODO asking the adopter to do something already
  done.
- The step is kept, because it does earn its seconds — but for the real
  reason, now stated: `mirrors-mypy` runs in its own virtualenv with no
  project dependencies, so it infers far less than mypy running where pandas,
  fastapi and pydantic are importable. The two runs check the same files and
  do not find the same things.
- The same file's header listed *"black → isort → flake8 → mypy"*. **None of
  those three is in it** — ADR-044 replaced them with ruff, and the block four
  lines below says so.

### Fixed — a size-triggered prediction-log flush could be lost on shutdown

- `PredictionLogger.log_prediction` fired the buffer-full flush with
  `asyncio.create_task(...)` and **kept no reference to it**. Python's docs
  are explicit that the event loop holds only a weak reference, so such a task
  can be garbage-collected mid-execution.
- `close()` did not wait for it either. Measured against a backend taking
  300 ms: `close()` returned with **0 of 3 events written and `logged_count`
  at 0**, the batch still in an orphan task; all 3 arrived 0.5 s later, which
  during a real FastAPI shutdown is never. The events were already out of the
  buffer, so the final drain found nothing to write. **A full batch of
  prediction logs, lost silently** — on the closed-loop path D-20/D-22 exist
  to protect.
- In-flight flushes are now retained in a set and awaited by `close()`.
  Verified by removing the control: the new
  `test_close_waits_for_an_inflight_size_flush` fails with the old code and
  passes with the fix.

### Fixed — the flaky test that was hiding it, and the gate that let it hide

- `test_flush_on_buffer_full` slept `0.1 s` and hoped the background flush had
  run. It failed intermittently on CI (Python 3.11; green on rerun with
  identical code) — and the sleep was long enough to usually mask the
  production defect underneath. It now waits on an `asyncio.Event` the backend
  sets, with a 10 s timeout as a failsafe: **a timeout decides how long the
  suite waits before declaring something broken, a sleep decides whether the
  test is correct.**
- `test_backend_failure_does_not_raise` lost its `0.05 s` sleep outright —
  `close()` now guarantees the in-flight flush has been attempted.
- **`check_test_clock_isolation.py` matched only calls that *read* the clock**
  (`datetime.now`, `time.time`), while its own title says tests must not
  *depend* on it. Waiting on the clock is the same dependency wearing a
  different hat. It now also matches `time.sleep` and `asyncio.sleep`. Two
  remaining uses are allowlisted with reasons: a poll-with-timeout loop
  waiting on an external service (the correct shape), and a fake backend that
  simulates a slow write, which is the condition under test.

### Fixed — "6 overlays" was asserted in fifteen places while the tree held seven

- `batch-only` was consequently absent from **three CI lanes**
  (`validate-templates.yml` digest-pinning, `golden-path.yml` scaffold check,
  `pr-smoke-lane.yml` render + kubeconform), from `scripts/test_scaffold.sh`,
  and **from every test in the repository** — `grep -rl batch-only` over the
  test trees returned nothing.
- It ships `networkpolicy-batch.yaml`, a real egress control: the base
  NetworkPolicy selects `app: <service>` while the batch CronJob pod carries
  `app: <service>-batch`, so the base does not cover it at all. That control
  was correct and **entirely unverified**.
- Every loop now discovers the directory. `golden-path.yml` goes further and
  compares the scaffold against the template, which also catches an overlay
  *added* to the template and never rendered — something a list cannot do.
- One step was **deleted rather than fixed**: `validate-templates.yml`
  §"Validate Kustomize builds (all 6 overlays)" sat in the same job as the
  shared `scripts/validate_k8s_manifests.sh`, which already builds every
  overlay *and* runs kubeconform *and* validates the base. Fixing a duplicate
  in two places is how the two drift.

### Added — C8, so the count cannot drift back

- `check_doc_coherence.py` gains an eighth check: no living document may state
  an overlay count that is not the real one. It scans **tracked files only**
  (an ignored scratch file is not a claim the repository makes) and skips
  frozen records, which correctly describe the count at the time they were
  written.
- The egress test now **derives** its overlay list and asserts that *every*
  overlay controls egress — by patching the default-deny base, or by shipping
  its own policy — and that none opens `0.0.0.0/0`. It also fails when it
  discovers nothing: an empty parametrize set is reported as a pass.
- Found while adding C8: `check_doc_coherence.py` printed *"all 7
  cross-document checks pass"* from a **hardcoded 7** while `CHECKS` held 8 —
  a stale count inside the script whose job is catching stale counts. It is
  now derived from `len(CHECKS)`.

### Added — the generated service gets the Markdown gate the template holds itself to

- A scaffolded service had **no docs lane at all**: no markdownlint, no
  configuration. The template blocked on 0 findings across 560 files while
  shipping adopters nothing — a template holding itself to a bar it does not
  prescribe, which is the same credibility problem as a gate that does not run.
- `templates/service/.github/workflows/docs-quality.yml` — blocking, pinned by
  commit SHA (so the rule set changes only in a deliberate bump), and it
  asserts its own config exists first: without
  `.markdownlint-cli2.jsonc` the action finds no files, reports *"0 issues in
  0 files"* and exits 0.
- `templates/service/.markdownlint-cli2.jsonc` carries the **identical rule
  block**. It is not byte-identical to the repo's because the scope
  legitimately differs — a service has no `releases/` and no `templates/` —
  so `scripts/check_markdownlint_parity.py` compares the half that must match
  and ignores the half that must not.

### Fixed — a render-only defect no template-side check could see

- **`{% raw %}` alone on a line renders to nothing but keeps its newline.**
  `agentic/rules/15-template-lifecycle.md` had four such markers, so every
  generated service shipped **6 MD012 violations** in 2 files while the
  template itself linted clean. The markers are now inline with their content.
- This is the class the new **validation 7b in `scripts/test_scaffold.sh`**
  exists for: it lints the **rendered** service, not the template. Its four
  states are all verified — clean (301 files), lint findings, a missing
  config, and **zero files linted**, which exits 0 on its own and is the
  failure this validation was written to tell apart from a pass.
- Finding zero files was not hypothetical: the smoke test scaffolds *inside*
  the cloned template repo, whose `.gitignore` lists the scaffold directory,
  so `gitignore: true` correctly ignored the entire service. The harness now
  runs `git init` first — which is what Copier's own closing message tells the
  adopter to do as step 3.

### Fixed — two more exclusions and lists that were narrower than they looked

- The repository's markdownlint config excluded
  `templates/service/eda/notebooks/**`, **a directory that does not exist and
  never did** — inherited verbatim from the workflow's old inline globs in the
  previous release. An exclusion for a path that is not there is
  indistinguishable from one that is working, and `check_doc_path_refs.py`
  does not scan `.jsonc`. The parity gate now checks that every path-shaped
  exclusion resolves in its own tree.
- `scripts/test_scaffold.sh` validated **6 of 7** Kustomize overlays from a
  hardcoded list, omitting `batch-only` — the same gap closed in the CI
  workflows one release earlier, still open here. It now discovers the
  overlays and fails when it finds none.

### Fixed — a generated service shipped 19 test files about the template repository

- Measured on a freshly scaffolded service before this change:
  **116 failed, 567 passed, 56 skipped, 31 errors** — **147 of 764 tests**
  failing on the adopter's very first `pytest`. After: **517 passed, 30
  skipped, 0 failed, 0 errors.**
- Root cause: `templates/service/tests/` is the **Copier payload**, and it was
  also where the tests that guard *this repository* lived — its README
  wording, its own workflows, `templates/config/*.yaml`, its release notes.
  Nothing separated them. They were told apart only by
  `Path(__file__).resolve().parents[3]` versus `parents[1]`, which is
  invisible at a glance.
- 19 files moved to `templates/tests/governance/`. The move is **depth-neutral**
  — `parents[3]` resolves to the repository root from there too — so it needed
  no code change, and the suite passes unmodified from its new home.
- **`scripts/check_payload_test_scope.py`** holds the boundary. The safe depth
  is computed per file rather than fixed, because a file under
  `tests/integration/` legitimately needs one level more than one directly
  under `tests/`. Dual-perspective candidate lists — try the service root,
  fall back to the template layout — are explicitly allowed; that is the idiom
  `check_doc_path_refs.py` already uses.

### Fixed — three quieter versions of the same defect, found while fixing the loud one

- **The canonical data layout was never created.** `templates/service/.gitignore`
  carried `!data/raw/.gitkeep` negations and a comment promising "a fresh clone
  has the expected layout" — **the `.gitkeep` files did not exist**, so a
  generated service had no `data/raw`, `data/processed`, `data/reference`,
  `data/production`, `data/validated`, `models` or `reports`. A comment
  asserting a control that was never built, on the layout every training and
  drift path writes to. Two of the seven additionally needed a negation in the
  *repository's own* `.gitignore`: git does not descend into an excluded
  directory, so re-including the file requires re-including the directory
  first.
- **`test_context_files_hygiene` fell back to `parents[2]`** when it found no
  `.git` — two directories above the service. A freshly scaffolded service has
  no repository yet (Copier's own closing message lists "Initialize git" as
  step 3), so the fallback fired every time. The fallback is now the service
  root, and the two git-dependent checks state their precondition instead of
  dying with "not a git repository" about a directory the adopter never named.
- **`test_cluster_defaults_contract` skipped three checks inside a service.**
  Its root resolver is dual-perspective, but three assertions then hardcoded
  `REPO_ROOT / "templates" / "service" / "k8s"` with no fallback — so the
  deny-default NetworkPolicy contract was disarmed in the very service it
  protects, reporting green. Now 12 pass in both perspectives instead of 9
  passing and 3 skipping.

### Changed — the OpenAPI snapshot bootstraps instead of failing

- `tests/contract/openapi.snapshot.json` does not exist in the template — there
  is no service to snapshot until one is rendered — and the instruction to run
  `scripts/refresh_contract.py` was enforced by nothing. An adopter's first
  `pytest` was two hard failures, and until someone read the message the D-28
  contract was simply not checked; the service's own `ci.yml` only compares the
  snapshot when it *changes*, so a missing one is invisible there too.
- The first local run now writes the baseline from the running app and skips,
  saying to commit it. **In CI a missing baseline stays a failure** — a machine
  must not invent the contract it is guarding. Verified in all four states:
  bootstrap creates it, the second run really compares, injected drift fails
  with the removed path named, and `CI=true` with no snapshot fails.

### Added — a gate that the render root still parses as Copier templates

- `copier.yml` sets `_templates_suffix: ""`, so **every** file under
  `templates/service/` is a Jinja template, not only the ones that look like
  one. A file that merely *contains* Jinja's delimiters aborts `copier copy`
  outright — the adopter gets no service at all.
- Two landed in one afternoon, neither in a file anyone would call a template:
  a Markdown table row whose escaped `\{@` (inside a code span, inside
  documentation *about* Jinja tokens) opened an expression that never closed;
  and this release's own `validate_k8s_manifests.sh`, where bash's
  array-length syntax opens a brace immediately followed by a hash — Copier's
  `comment_start_string`. The scaffolder died with *"Missing end of comment
  tag"* pointing at a file whose bash was perfectly valid.
- `scripts/test_scaffold.sh` caught both, in CI, minutes into a full render.
  That is the right place for the behaviour and the wrong place to find a
  typo. `scripts/check_template_render_safety.py` reduces the class to a
  parse: it reads the delimiters from `copier.yml` `_envops` rather than
  hardcoding them, checks all 416 text files under the render root in under a
  second, and names file and line. Wired into pre-commit, `make verify` and
  CI.
- It parses, it does not render, so `test_scaffold.sh` remains the authority
  on whether a rendered service actually works.

### Fixed — the Kubernetes manifest gates validated a fraction of what they guarded, and the overlay half was advisory

- **Template repo**: `Kubernetes Manifests` named **7 base manifests
  literally**. `templates/service/k8s/base/` holds **14**. The seven that
  were never validated include `pdb.yaml` and
  `networkpolicy-deny-default.yaml` — core API types kubeconform checks in
  full. It validated **no overlay at all**.
- **Generated service**: `ci-infra.yml` looped over `k8s/overlays/gcp-*` and
  `k8s/overlays/aws-*`. There are **7** overlays; `batch-only` matches
  neither glob. Both steps carried `continue-on-error: true`, so the result
  was advisory — and a loop over a glob matching nothing validates zero
  overlays while reporting the same green as one that validated all of them.
- Measured before changing anything: **all 14 base manifests and all 7
  overlays already pass** `kubeconform -strict`. The gate was advisory
  guarding something that was clean, which is the cheapest kind of gate to
  make blocking and the easiest kind to leave advisory forever.
- Both are now one script, `scripts/validate_k8s_manifests.sh`, vendored
  byte-identical into the service (`check_vendored_runtime_drift.py`) so the
  template cannot hold itself to a different bar than it ships. It globs the
  directory instead of naming files, covers `overlays/*/` instead of two
  cloud prefixes, validates the *rendered* output as a check distinct from
  the input files, and **treats discovering nothing as a failure**.
- The service now installs the same pinned `kustomize v5.4.3` the template
  validates with, instead of `kubectl kustomize` — whose embedded version
  differs, a silent source of "passes there, fails here".
- `continue-on-error` is gone. The gate blocks.

### Fixed — three generators emitted Markdown their own lint rejects, and two pre-existing dead-link classes

- `sync_agentic_adapters.py`, `generate_adr_index.py` and `mcp_doctor.py`
  wrote unpadded table separators and stray double blank lines. Left alone the
  gates deadlock: regenerate to satisfy the index/registry contract, break the
  docs gate; fix the file by hand, break the contract. All three now emit
  lint-clean Markdown, and their contract tests still pass (11/11 for
  `test_mcp_registry_contract.py`).
- `docs/audit/AUDIT_R8_STAFF_LEAD.md` carried **9 `file:///home/<user>/…`
  links** — dead for every reader, and a local path published in a public
  repository. Now plain code spans.
- The Link Check ignore list matched `{ORG}` and `{REPO}` but **not `{org}`
  and `{repo}`**, so the service README template's CI badge — placeholders an
  adopter substitutes — was fetched and 404'd. Same defect class as an id
  pattern that only matched uppercase.
- Neither dead-link class was new. `check-modified-files-only` means Link
  Check only ever sees files a PR touches, so both had been invisible for as
  long as those files sat unmodified. Reformatting brought them into scope
  for the first time.

### Fixed — the Markdown gate reported 8,584 findings and blocked nothing

- `Markdown Lint (style)` shipped with `continue-on-error: true` and the
  comment *"warn-only first run; flip to false after triage"*. **There was no
  configuration file at all** — every default rule, at every default setting.
  It reported **8,584 issues in 474 files** on every run and blocked nothing.
- `.markdownlint-cli2.jsonc` is that triage, with a stated reason beside every
  rule that is off or retuned. Repository is now at **zero findings** and the
  gate **blocks**.
- **`markdownlint --fix` is not content-safe on this corpus**, which is the
  finding worth keeping:
  - MD004 rewrote a wrapped `+` — the arithmetic plus in *"FastAPI +
    `asyncio.run_in_executor` + `ThreadPoolExecutor`"* — into a `-` bullet,
    **14 times**.
  - MD060 inserted spaces *inside inline-code regexes*, turning
    `(v[0-9]|main|master)` into `(v[0-9] | main | master)` — a regex that no
    longer matches what it says.
  Both were caught by a token-stream check run against every file, not by
  reading the diff. The durable repair was to remove the ambiguity (escape
  the pipes, rewrap so no operator starts a line), not just revert the
  character.
- **Real rendering bugs found and fixed**, not formatting nits:
  - unescaped `|` inside inline code inside table cells — the table parser
    splits on it before inline code is recognised, so cells were being
    **silently dropped** (MD056 "Expected 4; Actual 6; extra data will be
    missing") in `18-audit-quality.md`, `rule-audit/SKILL.md`, `MIGRATION.md`;
  - `{% raw %}` markers on their own lines **splitting the D-34 table in two**
    in `AGENTS.md` and `rule-audit/SKILL.md`;
  - `<dataset>`, `<one-line title>` — angle-bracket placeholders the HTML
    parser **eats**, so they never rendered;
  - a wrapped sentence whose continuation began `# 5,` **rendering as an H1**
    in ADR-020;
  - `[0.23.0]` documented the **same fix twice** under an identical heading;
    the shorter write-up was a strict subset and is gone.
  - **236 code fences had no language**, so none were highlighted and none
    were copy-buttoned.
- **437 over-length lines** reflowed at 120 columns — matching
  `ruff line-length = 120`, so the repo has one line-length number rather than
  two. Split-only, never joined, so the diff shows exactly the lines that were
  too long. Nine single-command lines (up to **467 characters**) were promoted
  to fenced blocks instead: wrapping a command changes what it does.
- **Two generators emitted Markdown their own lint rejects**, which would have
  deadlocked the gates against each other — regenerate to satisfy one, break
  the other. `sync_agentic_adapters.py` and `generate_adr_index.py` now emit
  lint-clean tables.
- `"gitignore": true` and explicit `globs` in the config, and **no `globs:` in
  the workflow**: the action's input would shadow the config and let CI and a
  local run check different files. Before this, a local run linted every
  `LICENSE.md` under `.venv/`.
- The job asserts its own config exists. Without `.markdownlint-cli2.jsonc`
  the action finds no files and reports *"0 issues in 0 files"* — a pass
  because it checked nothing, which is the failure mode this repo keeps
  finding.

### Fixed — the shipped test suite could not even be collected in a scaffolded service

- Running `pytest` in a freshly scaffolded service failed at **collection**:
  `tests/policy/conftest.py` raised a `RuntimeError` looking for
  `templates/scripts/new-service.sh`, a template-repo path. The whole
  `tests/` tree was uncollectable, so every adopter's `make test` was broken
  from the first commit.
- Those tests **scaffold a service in order to inspect it**, so they need the
  scaffolder: they are template-repo tests that happen to live inside the
  payload directory. The package now skips cleanly there via
  `collect_ignore_glob` instead of raising.
- Two more modules failed rather than skipped in a service, for the same
  reason — `parents[3]` walks above the service root and lands outside the
  tree. Measured on a real scaffold: `test_dashboards_inventory.py` alone
  produced 1 failure and 3 errors about the *template's* documentation.
  Both now skip at module level with an explicit reason.

### Fixed — three tests that passed without checking anything

- **`test_dashboards_inventory.py`** parametrised over a path re-derived
  inline against the pre-ADR-030 layout, while the module's own
  `DASHBOARDS_DIR` constant was correct. The glob returned nothing, pytest
  reported "empty parameter set" as a *skip*, and the test validated **zero
  dashboards from June to September 2026**. Now uses the constant, and a new
  `test_dashboards_are_discovered` fails if the set is ever empty again.
- **`test_anti_pattern_count_consistency.py`** listed
  `.claude/rules/01-serving.md` and `09-mlops-conventions.md` — neither has
  ever existed under those names, and being thin adapter pointers those rules
  do not quote the catalog range at all. Two of its six subjects were
  decorative. Removed, and a missing subject now **fails** instead of
  skipping.
- **`test_closed_loop_workflow_contract.py`** skipped when the awk fallback
  it checks was absent — so it could only run when the property already held
  and could never fail for the one thing it existed to detect. Inverting it
  showed the fallback is genuinely gone, and the workflow says why: *"Closed-loop
  proof requires prediction_log_total. A request counter alone proves HTTP
  traffic, not prediction logging."* The test was checking a contract the
  workflow consciously replaced. Rewritten to the contract that holds: the
  workflow's awk selector must name a metric `fastapi_app.py` actually
  declares, or the proof sums nothing and passes while measuring nothing.

### Fixed — the guard that explained all of this away

- `scripts/test_scaffold.sh` treated a collection failure as a warning with a
  guessed cause: *"(expected — scaffolded deps not installed)"*. It was not a
  dependency problem, and the guess was plausible enough that nobody checked
  which it was. **A guard that guesses why it failed is a guard that stops
  failing.**
- Two attempts at classifying the errors are recorded in the script because
  both are traps worth knowing: `echo | grep -q` breaks under `set -o
  pipefail` (grep closes the pipe, echo takes SIGPIPE, the pipeline reports
  failure), and asking "does the output mention a missing module?" excuses a
  real defect whenever it co-occurs with an absent dependency.
- The reliable check needs no classification at all: it runs **after** the
  `SCAFFOLD_SMOKE` install step, where a collection error can only mean the
  scaffold is broken. CI sets `SCAFFOLD_SMOKE=1`, so CI gets the hard gate;
  the pre-install check stays an honestly-labelled warning.

### Fixed — the coverage standard was documented at 90% and measured at 40%, enforced nowhere

- `CLAUDE.md` promised *"Coverage: >= 90% lines, >= 80% branches"*. There was
  **no `fail_under`** in `pyproject.toml`, no root `codecov.yml`, and no
  `--cov-fail-under` in CI — the pipeline produced a coverage report and
  never looked at the number.
- Read from CI rather than estimated: the scoped source sits at **40%**,
  identically on Python 3.11, 3.12 and 3.13. A **50-point gap** between the
  documented standard and the code, invisible because nothing compared them.
- `fail_under = 40` — a **ratchet at the measured floor, not the target**. A
  threshold above reality fails every build and gets deleted within a week; a
  threshold at reality stops the number sliding backwards and makes every
  improvement permanent.
- Two limits are stated in the config rather than left to be discovered:
  - the measured scope is three paths, so the **26 modules under `scripts/` —
    including every gate this repo relies on — are not measured at all**.
    40% is 40% of a subset.
  - **branch coverage is not enabled**, so the "80% branches" half of the
    claim was never measurable. Enabling it moves the line number too, which
    makes it a separate decision rather than a silent flip.
- `CLAUDE.md` now states the enforced floor alongside the target, so the
  headline quality metric says what is true and what is aimed at.

### Changed — the scaffolded service gets the same IaC bar, and the machinery to hold it

- The service's gate ran at `CRITICAL,HIGH` while this repo ran at
  `CRITICAL,HIGH,MEDIUM,LOW`. The stated reason was that a service had
  nowhere to record an accepted finding, so a stricter gate would be deleted
  rather than obeyed. **That was a fixable gap, not a reason.**
- A scaffolded service now ships `.security-baselines/` and a vendored
  `scripts/check_baselines_expiry.py` — byte-identical to this repo's,
  enforced by `check_vendored_runtime_drift.py` so the two cannot diverge.
  Its `REPO_ROOT` resolves to the service root in both layouts, so the same
  file works unchanged in either.
- The service gate now runs at `CRITICAL,HIGH,MEDIUM,LOW` with the expiry
  check as its own CI step. **A baseline is only a control if its entries
  expire**; without that the ignore file is an ordinary suppression list that
  grows and never shrinks.
- Its baseline is seeded with the three log-sink acceptances the template's
  own modules carry, and says so — *"if you replace or remove those
  resources, remove the entries with them; a baseline that outlives its
  subject is a suppression nobody remembers making"*.
- The service `README` for that directory carries the two lessons this
  repository paid for: try to fix it first (**10 of 13 sub-threshold findings
  upstream were real defects**), and **verify the compensating control
  exists in the file you named** — a justification citing a control nobody
  built is worse than none, because it stops the next reviewer from looking.
- ADR-046 records the asymmetry as closed rather than deleting the paragraph
  that justified it. A template holding itself to more than it prescribes is
  a milder version of the credibility problem the `Self-audit` job exists to
  avoid, but it is the same problem.

### Added — control invariants are asserted now, not asserted about

- Three times in one review cycle a **comment claimed a control that was not
  there**: a tfsec suppression citing a `variables.tf` validation rule that
  did not exist (#93), `ci_sa_user` claiming an IAM condition with no
  condition block (#94), and `check_baselines_expiry.py` claiming to read
  `exclude:` blocks while filtering on uppercase ids (#88). Each survived
  because **reviewing meant reading the claim**.
- Four new assertions in `templates/service/tests/test_iam_least_privilege.py`
  turn the three Terraform claims into executable checks: node pools must
  bind the dedicated `nodes` identity, `roles/iam.serviceAccountUser` must
  never be granted project-wide, `master_authorized_networks_config` must be
  a static block, and the cluster must carry the precondition pairing
  `enable_private_endpoint` with a non-empty allowlist.
- **Each verified by removing the control and watching the test fail**, not
  by observing a green run. A test that passes but would not catch the
  regression is worse than none, which is the failure mode this whole series
  keeps finding.
- The suite also now requires **six** GCP service accounts. `nodes` was added
  in #94 and nothing asserted it, so deleting it would have passed.

### Added — `check_control_claims.py`, and why the cheap version was rejected

- Measured first: 69 "enforced by / scoped via / validated by" claims across
  the repo, 19 of them naming a path — and **all 19 resolve**. So a
  path-existence check would have caught **none of the three failures**: in
  every case the file existed and the control inside it did not. Building it
  would have been theatre.
- What is decidable, and valuable: the `D-NN` anti-pattern table in
  `AGENTS.md` names an enforcer per row. The gate asserts the named file
  exists **and mentions the `D-NN` it claims to enforce**, making the link
  bidirectional — refactor one side and it fails.
- Found two one-way links on first run: D-31 named a test that never
  mentioned D-31, and D-37 named a script that never mentioned D-37. Both
  closed.
- Wired as a CI step, a pre-commit hook and a `make verify` gate.

### Fixed — dead layout branch in the Terraform locator

- `_find_tf_root` tried a pre-ADR-030 path first. Harmless — the second
  candidate matched — but dead since June, and the docstring still claimed
  five GCP service accounts. Both corrected.
- Documented a genuine limit of the code-comment half of the path gate:
  Markdown can mark a dead path by dropping the code span, and **a code
  comment has no such distinction**, so a removed path must be described
  rather than spelled. The alternative, a per-line opt-out marker, would be a
  bigger hole than the one it closes.

### Fixed — the LOW triage: seven of eight were real

- The previous review left LOW unenforced on the grounds that *"an unenforced
  threshold is honest about untriaged findings in a way a suppressed one is
  not"*. True, and still an evasion: **"we have not looked" is a description,
  not a decision.**
- **`GCP-0066` ×4 — live buckets without customer-managed encryption.** The
  bootstrap *state* bucket has had CMEK since ADR-015 while `models`, `data`,
  `mlflow_artifacts` and `logs` held Google-managed keys. The asymmetry was
  backwards: the state file was better protected than the models and training
  data it describes. Fixed with a `storage` key on the existing ring, plus
  the GCS service-agent grant that CMEK requires.
- **`GCP-0054` ×2 — node image type not pinned.** COS is GKE's current
  default, which is exactly why it is pinned: a default is not a decision,
  and inheriting it means a future change lands a different node OS with a
  different attack surface without anyone choosing it.
- **`GCP-0051` — cluster without resource labels.** Every bucket, key and
  service account carries them; the cluster did not, so cost attribution had
  a hole precisely where the spend is.
- **`AWS-0089` — bootstrap state bucket without access logging.** Versioned,
  encrypted, public-access-blocked, and nothing recorded *who read it* — the
  interesting question for a Terraform state file. Implemented rather than
  accepted: the module has no CloudTrail, so there was no compensating
  control to point at, and pointing at one that does not exist is the failure
  this repo has now found three times.

### Changed — the IaC gate reaches the floor, after the triage rather than before it

- `CRITICAL,HIGH,MEDIUM,LOW`. The threshold walked up as each severity was
  triaged, never ahead of it.
- **Of the 13 findings that sat below a threshold across the two reviews, 10
  were real defects.** "Below the threshold" predicted nothing about whether
  a finding mattered — the argument against selective gates, and why this one
  is now exhaustive.
- Recorded plainly in `docs/audit/baseline-review.md`: **fixing a finding is
  not free of findings.** Adding the access-log bucket introduced three new
  ones, one of them HIGH (`AWS-0132`, SSE-S3 rather than a customer key —
  fixed with the existing bootstrap CMK and S3 Bucket Keys). A new resource
  inherits every check for its type; "close the finding" and "reduce the
  count" are different operations and the second is the wrong goal.
- The four residual findings are all log-sink buckets (versioning,
  self-logging), accepted with dated justifications. Those entries now state
  a limitation the earlier ones did not: the plain ignore format suppresses a
  check id **repo-wide**, not per resource, so each carries a review question
  about *which* buckets the check fires on rather than whether the prose
  still reads well.

### Added — a procedure for the node-pool replacement, not just a warning

- The ADR-017 identity change makes `terraform apply` **replace** both node
  pools. `MIGRATION.md` warned about it and told the adopter to "plan a
  window", which moves the problem rather than solving it. A warning is not a
  procedure.
- `docs/runbooks/gke-node-pool-replacement.md` is the blue/green procedure:
  stand up the new pool, cordon, drain through the API, verify on the new
  nodes, then remove the old one. Terraform never holds the cluster in a
  state with no nodes.
- **The correction that matters most in it**: PodDisruptionBudgets do **not**
  protect you on the naive path. A pool deletion is not an eviction — the
  budget has no node left to keep the pod on. They *are* honoured by
  `kubectl drain`, which is precisely why the procedure drains instead of
  letting Terraform delete. Anyone reading "check your PDBs are in place" as
  reassurance would have been wrong.
- Documents why the module does **not** use `create_before_destroy`: it needs
  `name_prefix` instead of `name`, which makes pool names non-deterministic
  (breaking `nodeSelector`, dashboards and cost queries), is itself a
  replacing change so it does not help with the migration at hand, and is
  capped at 31 characters in google provider v8 — which `${var.project_name}-workload-pool`
  exceeds for many project names.
- States plainly that **the procedure has not been run against a live
  cluster**. The template does not deploy to a real cloud by design, so the
  commands come from the provider's replace semantics and standard GKE
  practice, not a recorded run. First execution should be a rehearsal in dev,
  recorded in `docs/runbooks/drills/` — a runbook that has never been run is
  a hypothesis in the shape of a procedure.

### Fixed — GKE nodes ran as the default Compute Engine service account (D-31 in the module that defines D-31)

- Neither node pool set `node_config.service_account`, so GKE fell back to
  the **default Compute Engine service account** — typically `roles/editor`
  across the whole project. Trivy reports it as `GCP-0050`; it is a D-31
  violation inside the module that implements D-31's other five identities.
- Bounded rather than harmless: Workload Identity means pods impersonate
  `runtime`/`drift`/`retrain`, so this is what an attacker inherits from a
  compromised **node**, not from a compromised pod.
- A sixth identity, `nodes`, now carries Google's documented minimum —
  `logging.logWriter`, `monitoring.metricWriter`, `monitoring.viewer`,
  `stackdriver.resourceMetadata.writer` — plus `artifactregistry.reader`
  scoped to the repository rather than granted project-wide.
- `node_oauth_scopes` gained `devstorage.read_only`: a scope is an upper
  bound on what a service account may do, so the Artifact Registry grant
  would have been unusable without it and private pulls would fail.
  `cloud-platform` stays avoided, per the variable's own description.
- **Adopter-visible and disruptive**: `service_account` is replace-forcing,
  so Terraform will propose recreating both node pools. `MIGRATION.md` says
  to read the plan and check PodDisruptionBudgets first.

### Fixed — CI could impersonate every service account in the project

- `google_project_iam_member.ci_sa_user` granted `roles/iam.serviceAccountUser`
  **project-wide**, under a comment reading *"Scoped via condition (only
  acting on SAs in this project)"*. **There was no condition block.** CI could
  therefore act as `runtime`, `drift` and `retrain` — exactly the blast radius
  ADR-017 exists to bound. Trivy reports it as `GCP-0011`.
- Replaced with three per-account bindings: `deploy`, `runtime` and `nodes`
  (attaching a service account to a node pool requires `serviceAccountUser`
  on it). `drift` and `retrain` are reached through Workload Identity, never
  by CI, and are deliberately absent.

### Changed — the IaC gate runs at MEDIUM

- Raised from `CRITICAL,HIGH`. At HIGH the five MEDIUM findings were visible
  in the log and enforced by nothing, which is the same as not looking — two
  of them were the defects above.
- The two that remain are object versioning on **append-only access-log
  buckets**, accepted with a dated justification (2027-03-05) rather than
  fixed: versioning guards against overwrite and modification of existing
  objects, neither of which is a failure mode for a log sink, and it
  multiplies cost on the highest-volume bucket. The buckets holding state,
  models and MLflow artifacts do have versioning.
- LOW stays out. Eight findings whose triage has not been done, and an
  unenforced threshold is honest about that in a way a suppressed finding
  is not.
- **The scaffolded service stays at `CRITICAL,HIGH`** on purpose. The higher
  bar is affordable here because this repo has `.security-baselines/` and an
  expiry gate; a generated service has neither, and a MEDIUM gate with no way
  to record an accepted finding trains adopters to delete the step.
- ADR-046's count is corrected — it said 4 MEDIUM, there were 5.

### Fixed — the GKE control plane could be public with no allowlist, and the suppression cited a control that did not exist

- `master_authorized_networks_config` was a `dynamic` block gated on the list
  being non-empty. The variable defaults to `[]` and **nothing in the repo
  sets it** — no overlay, no tfvars — so the block never rendered, and GKE
  with no authorized-networks block applies **no restriction**.
- Harmless while `enable_private_endpoint = true`, which is the default. A
  **publicly reachable control plane with no allowlist** the moment an
  adopter takes the documented dev opt-out and sets it false.
- `GCP-0061` — and `google-gke-enable-master-networks` before it — had been
  suppressed for two releases as a scanner limitation, on a justification
  that read: *"enforced by the variable validation rule in `variables.tf`"*.
  **There is no such rule.** The finding was not a false positive; it was
  pointing at a real gap held open by a compensating control that was never
  built.
- **Fixed in the module, not re-suppressed:**
  - the block is now unconditional, so an empty list means *"enabled, no
    external CIDR allowed"* — the restrictive reading — rather than *"not
    configured"*;
  - a `precondition` on `google_container_cluster.gke` rejects
    `enable_private_endpoint = false` together with an empty list, at **plan**
    time. It lives on the resource rather than in a `validation` block
    because the condition spans two variables, which variable validation
    could not do before Terraform 1.9 and this module targets `>= 1.7`.
  - Verified across all three combinations: private endpoint passes, public
    with CIDRs passes, public without CIDRs fails.
- `.security-baselines/trivy-config.trivyignore` is now **empty**.
  Suppressions across the whole repo: **zero**, and the 2027-01-01 expiry no
  longer exists. `docs/audit/baseline-review.md` carries the third dated
  review, including what the review procedure itself missed: reviewing a
  suppression had meant reading its rationale, not verifying it. Step 3 now
  says to confirm the compensating control exists in the file the
  justification names.
- Adopter-visible; both rows are in `MIGRATION.md`. A plan that starts
  failing with *"public control plane must be constrained"* is the fix
  working.

### Changed — the security check is named for what it does, not for its tools

- `Self-audit (gitleaks + tfsec + checkov + trivy fs)` →
  **`Self-audit (secrets + IaC + supply chain)`**. The old name enumerated
  its tools, so ADR-046 falsified it the moment tfsec was swapped for Trivy —
  and because the job is one of the six required status checks, correcting it
  was not a one-line edit.
- **A required check is identified by its job-name string.** Rename the job
  and the ruleset waits for a context that will never report, so the PR
  containing the rename blocks itself. ADR-046 therefore shipped with a name
  it knew to be wrong, and said so.
- Executed as a three-step transition: drop the context from the ruleset by
  direct API call (transient, never committed), merge the rename with all
  three canonical sources moving together — `docs/governance/branch-protection.md`,
  ADR-026 and `scripts/setup_branch_protection.sh` — then re-apply the
  ruleset from the committed payload. The procedure is now written down in
  `docs/governance/branch-protection.md` §"Renaming a required check",
  including the caveat that the check is unrequired in between and that
  re-adding one while red blocks every subsequent PR.
- The new name describes the **classes** of check rather than the tools, so
  the next tool swap touches no contract at all. That is the actual fix; the
  rename is just this instance of it.

### Changed — Terraform IaC scanning moves from archived tfsec to Trivy config (ADR-046)

- **tfsec is archived upstream** and was pinned to its final release,
  v1.28.14. The binary announces it on every run; the workflow carried a
  `TODO: migrate to trivy config (tfsec successor) per ADR-TBD`. ADR-046 is
  that document.
- **Measured before deciding**, at the gate's own `HIGH,CRITICAL` threshold,
  suppressions removed: tfsec found **4** findings (all GCP), Trivy found
  **1**. Two of the three suppressions dissolve because Trivy evaluates what
  tfsec could not — it has no PodSecurityPolicy check (PSP was removed in
  Kubernetes 1.25) and it correlates node pools to their cluster. The third,
  `master_authorized_networks` inside a `dynamic` block, survives as
  `GCP-0061`: no static analyser evaluates dynamic blocks.
- **Three suppressions become one.** `docs/audit/baseline-review.md` carries
  the second dated review recording exactly what dissolved and why.
- **Coverage widens from two root modules to five.** tfsec only ever scanned
  `gcp` and `aws`; the two bootstrap layers and the Cloudflare edge module
  were never scanned at all — the same gap #78 closed for `terraform validate`.
- The scaffolded service's `ci-infra.yml` migrates too. Leaving adopters on
  `aquasecurity/tfsec-action` would have shipped the dead scanner onward.

### Fixed — Trivy's own ignore expiry is not honoured, and our guard named its files literally

- Trivy 0.71.0 accepts `expiredAt:` in its YAML ignore format and **does not
  act on it**. Measured here: an entry dated `2020-01-01` still suppressed
  its finding, in all three date formats tried, with nothing on stderr.
  Delegating expiry to the tool would have produced exactly the failure
  `.security-baselines/` exists to prevent. `check_baselines_expiry.py`
  stays the authority.
- That guard named its three baseline files literally, so replacing
  `tfsec.yml` with a new file would have left the new file **unwatched while
  the gate reported green** — the same defect class as #79, #86 and #89. It
  now discovers every file in `.security-baselines/`, and an unrecognised
  format is a failure rather than a silent skip.
- Its id pattern required a four-digit year followed by a second number, so
  misconfiguration ids (`GCP-0061`) matched nothing. Widened, and the plain
  scanner now accepts an annotation on the line above the entry, matching
  the YAML scanner — a justification that needs a paragraph could not fit
  inline.
- The `Self-audit` job kept its now-inaccurate name in that change: it is one
  of the six required status checks in the ADR-026 ruleset, and renaming it
  there would have blocked that very change, because the required context
  would never report. **Since carried out** — see the rename entry below.

### Changed — baseline entries that can be verified now are verified, not dated

- The two `runtime-artifact` entries in `.doc-path-baseline.yml` carried a
  one-year expiry. That was ceremony, and it was my own design mistake: the
  condition never changes, so the date could only ever be bumped. **A date
  that can only be postponed trains reviewers to postpone dates**, which
  degrades the mechanism for the entries where the deadline is the point.
- Entries now declare an explicit `kind:`, and the two kinds are verified
  differently because they are not the same claim:
  - `unimplemented` claims *"we intend to build this"* → `expiry:`, because a
    deadline is the only honest check on an intention.
  - `runtime-artifact` claims *"this resolves at runtime, and X creates it"*
    → `created-by:`, because that is **checkable now**. The gate asserts the
    named creator exists and still references the path.
- So if the skill that writes `docs/concept_drift_log.md` is deleted, or
  simply stops mentioning it, the entry **falsifies itself on the next run**
  instead of sitting valid until 2027. A `runtime-artifact` carrying an
  `expiry:` is rejected outright so the two mechanisms cannot be mixed.
- The classification also moves out of the `reason:` prose, where it was a
  string prefix nothing enforced.

### Added — the path gate checks internal Markdown link targets

- A link target resolves **relative to the file that contains it**, not the
  repo root. That distinction produced the only broken link this repo had:
  `templates/service/README.md` pointing at
  `templates/service/docs/CCDS_MAPPING.md`, which from inside that directory
  means a doubled path. It was introduced in #84 and caught by CI, which is
  the argument for checking it locally.
- The `Link Check` job is a real gate but has a shape worth stating: it
  triggers only on `pull_request` with `paths: **/*.md`, and on a PR passes
  `check-modified-files-only: yes`. **A link breaks when its target moves,
  not when the linking file changes** — so that case is invisible at PR time
  and surfaces up to seven days later in the Monday scan, on `main`, as a red
  scheduled run that blocks nobody. Long enough for someone to suppress it
  instead of fixing it, which is precisely what happened to `../SECURITY.md`
  until #86.
- The split is by what each check *needs*: internal links are deterministic,
  need no network and run in pre-commit; external URLs, `mailto:` and
  site-root `/` targets stay with Link Check, off the merge critical path.
- Code spans are stripped before link matching — `[text](path)` inside
  backticks is documentation *about* links. Typographic `…` now counts as an
  ellipsis alongside `...`.
- Measured before enabling: **one** broken relative link across the repo, and
  it was that false positive in the gate's own governance doc.

### Fixed — the service CLAUDE.md described the template repo, not the service

- `templates/service/CLAUDE.md` ships into every scaffolded service and
  still described the **pre-migration template repo**: a `templates/` tree
  with `cicd/`, `monitoring/` and `common_utils/` that ADR-030 dissolved in
  June, commands telling the adopter to scaffold a service from inside
  their service, and a surface claim of "18 rules + 26 skills + 18
  workflows" against a live 19/27/20.
- Rewritten to the service's own layout and command set, with the
  template-repo audit history replaced by an actionable **Upstream
  template** section.
- **C4 now reconciles both `CLAUDE.md` files.** The service copy was only
  ever checked *inside* a generated service — by which point it had already
  shipped wrong. `check_doc_coherence.py` reconciles each against the
  agentic surface beside it.

### Fixed — `make scaffold-update` ran `copier update` unpinned, defaulting to `main`

- The service `Makefile` ran `copier update --trust --defaults` with no
  `--vcs-ref`. `/scaffold-update` was pinned in #71; the Makefile target —
  the other entry point to the same operation — was not.
- Worse, the target reused `REF`, which the `ci-green` target defines as
  `REF ?= main`. A bare `make scaffold-update` therefore updated the service
  from the **moving development branch**, not a release.
- Now uses `TEMPLATE_REF` with **no default**: the target refuses to run
  unpinned (exit 2, without invoking copier).
- **`check_adopter_scaffold_ref.py` could not see it.** Its scan was keyed
  on `SCAN_EXTENSIONS`, and a `Makefile` has no extension — while its own
  comment warned that "a guard whose coverage is a literal list is only ever
  as complete as the moment someone last remembered to edit it". Scoping by
  extension was the same mistake. Extensionless build files are now scanned.
- The guard's failure message also described a catastrophe ADR-045 already
  removed (the v1.x downgrade). Corrected: unpinned now means jumping to a
  release nobody chose, with the catastrophic form prevented by the tag
  namespace staying clean rather than by the command.

### Fixed — four sys.path bridges and a contract test that self-disabled

- Three bridges in `templates/service/tests/` probed directories where
  `common_utils` has not lived since ADR-030 — dead safety nets that could
  never fire, masked because the import resolves by another route in the
  contexts CI exercises. All now use `parents[1]`, which is the service root
  in **both** layouts.
- `test_drills_reproducible.py` carried an unreachable `DRILL_PYTHONPATH`
  fallback for a layout split that the migration closed; replaced by an
  assertion that would notice if the layouts ever diverge again.
- `test_memory_contracts.py` resolved the service root as
  `REPO_ROOT/"templates"/"service"`, which does not exist inside an
  adopter's service — so the invariant *"serving code must not import
  `common_utils.memory_types`"* **silently skipped in exactly the
  environment it protects**. Now context-adaptive, and verified to fail on
  a planted violation.

### Changed — the path gate now reads code comments

- Scan extended from `.md`/`.txt` to tracked `.py`, `.yml`, `.yaml`, `.sh`
  and `Makefile` comments. Measured before widening: 15 unresolved paths
  across 309 code files — including `.security-baselines/tfsec.yml`
  justifying three suppressed HIGH findings against a deleted directory.
  All 15 resolved.
- Three filters the first run demanded, each pinned by a test: glob and
  brace shorthands (`deploy-*.yml`, `deploy-{gcp,aws}.yml`) no longer
  report a truncated prefix; uppercase stand-ins (`ADR-XXX.md`) and
  `*.local.*` paths are not claims; and punctuation stripping no longer
  scrubs `templates/templates/...` into a clean-looking one.
- String literals stay out of scope: a path built at runtime is program
  logic, not a claim.

### Fixed — two baseline entries were misclassified, not unimplemented

- **`scripts/smoke_test.py` was never missing.** The release-checklist
  skill says in so many words that *"the template does not ship a
  `scripts/smoke_test.py` script (would compete with `deploy-common.yml`
  as SSOT)"*. It is a deliberate, documented non-existence. The path
  reference gate flagged it only because the sentence wrapped it in a code
  span, and the baseline then recorded a correct design decision as a
  defect. Rewritten as plain prose per the convention in
  `docs/governance/doc-path-references.md`: a code span asserts *this
  resolves*, so a path you are talking about is not one.
- **`scripts/load_test_services.py` was a wrong path, not a missing
  file.** The artefact exists as `templates/service/tests/load_test.py`,
  run via `make load-test`. The load-test workflow gave three
  `locust -f scripts/load_test_services.py` commands that fail as written.
  Corrected to `tests/load_test.py`, the form that is right from inside a
  generated service.
- Baseline down from 7 entries to 5.

### Fixed — a broken link was suppressed rather than repaired

- `templates/service/docs/ADOPTION.md` linked `[SECURITY.md](../SECURITY.md)`,
  which resolves to `templates/service/SECURITY.md` in this repo and to the
  service root in a generated one. Neither exists.
- It never failed CI because `.github/markdown-link-check.json` carried a
  dedicated `ignorePatterns` entry, `^\.\./SECURITY\.md$`, silencing that
  exact link. The sentence is about *this template's* disclosure SLA, so
  the link now points at the upstream `SECURITY.md` absolutely, and the
  suppression is deleted. The repo-root copy of the same document was
  always correct and is untouched.

### Added — `make verify`, and the eight CI gates that had no local hook

- Eight jobs in `validate-templates.yml` had no pre-commit hook at all:
  doc-coherence, cicd-template-drift, vendored-runtime-drift,
  common-utils-drift, dashboard-inventory, baselines-expiry,
  test-clock-isolation and agentic-adapter-sync. There was no way to run
  the full gate set locally, so the only feedback loop was push-and-read-CI.
- All eight are now pre-commit hooks, path-filtered so each fires only on
  the files it guards. This is deliberately **not** a revival of the
  pre-push stage retired in R5-L4: that decision was about a 60-second
  scaffold hook training `--no-verify`, and all thirteen gates together
  run in 0.9 s — inside the < 5 s budget this config targets. The
  reasoning is recorded next to the original decision.
- `make verify` runs the same thirteen in one command for the
  sweep-before-PR case, reporting **every** failure rather than stopping at
  the first. Slow end-to-end stays in `make smoke`.
- Verified negatively: introducing a dead path fails `check_doc_path_refs`
  through `make verify`, and breaking the byte-identity of a vendored
  runbook — the exact mistake that reached CI on #84 — is now caught by the
  `vendored-runtime-drift` hook at commit time.

### Added — the context-file hygiene test that was documented but never written

- `docs/agentic/contextualization.md` §7 states that
  `templates/service/tests/test_context_files_hygiene.py` "enforces at
  every PR" four properties of the agentic context files. It was cited as
  the enforcing control and did not exist, so the properties were
  documented and unchecked: a contributor could commit a real AWS key
  inside `company_context.example.yaml` and nothing would object.
- The test now exists, 32 cases across the seven `*.example.yaml` files
  and their three schemas: no `*_context.local*.yaml` tracked (and the
  pattern is actually gitignored, not merely untracked today), every
  example validates against the schema that governs it, no real-looking
  secret in the raw text, and placeholders in the `{PlaceholderName}` form.
- **Two places where §7 was looser than code can be**, resolved
  explicitly in the test's docstring rather than silently:
  - §7 names `context.schema.json`, but two siblings carry their own
    schemas. Each example is validated against the schema that governs
    it, discovered by filename.
  - Properties 2 and 4 contradict each other on placeholder-bearing
    fields: `service_spec` declares `service_slug` with
    `pattern: ^[a-z][a-z0-9_]*$` while the example sets `{service_slug}`.
    An example file is by construction not filled in, so the resolution
    is **structure enforced, placeholder values exempt** — a schema error
    is tolerated only when the failing instance is exactly a
    `{Placeholder}` string.
- Secrets are scanned in raw text (a key leaked in a comment is still
  leaked); placeholder style is checked against parsed values only
  (comment prose naming a file pattern is documentation, not an
  unreplaced value).
- The secret patterns are themselves pinned by a test, because a scanner
  that silently stops matching passes everything.
- Baseline down from 5 entries to 4.

### Fixed — the baseline expiry gate could not see the entries it exists to watch

- `scripts/check_baselines_expiry.py` reported `OK — no expired or
  unannotated entries` while **three HIGH-severity GKE checks sat
  suppressed** in `.security-baselines/tfsec.yml`. It was not lying about
  the entries it saw; it saw none.
- Its `yaml_entry` pattern required a suppression id to start with an
  uppercase letter. That matches checkov (`CKV_AWS_18`) and misses every
  tfsec id (`google-gke-enable-master-networks`). Zero matches, zero
  expired, pass.
- Widening it to lowercase alone would have been wrong: `framework:
  [terraform, kubernetes, dockerfile]` in `checkov.yml` is a sequence too.
  The scanner is now **block-aware** and treats only items under
  `exclude:` / `skip-check:` as entries — which is what the code's own
  comment had claimed since it was written.
- The three entries also carried `Review-by: 2027-01`, a format the gate
  does not parse. Normalised to `# expiry: 2027-01-01` with the original
  justification prose kept intact.
- Verified: all three are now seen and in-date, `--as-of 2027-06-01` fails
  all three as expired, and `framework:` still produces no false positive.

### Added — `docs/audit/baseline-review.md`, the review record that was promised

- `.security-baselines/README.md` step 4 has instructed reviewers to
  update this file since the baselines were introduced. It did not exist,
  so three HIGH suppressions had a justification in a YAML comment and no
  review record anywhere.
- The first dated review records all three, each with its compensating
  control and expiry. All three are **tool limitations, not accepted
  risks**: PSP was removed from Kubernetes 1.25 and PSS covers it via
  namespace labels; `master_authorized_networks_config` exists as a
  `dynamic` block tfsec cannot evaluate; the metadata attribute lives on
  the node pools rather than the cluster tfsec inspects.

### Added — a generated ADR index, and the Art. 11 claim it backs

- `docs/COMPLIANCE_MAPPING.md` cited an ADR index as EU AI Act Art. 11
  evidence. The index did not exist, and the same row claimed "ADRs (37)"
  against 45 on disk. A compliance mapping that points at a missing
  artefact and miscounts the one it has is worse than none: it is an
  assertion an auditor falsifies in one command.
- `scripts/generate_adr_index.py` generates `docs/decisions/README.md`
  from the files themselves and `--check` fails CI when it goes stale, so
  the next ADR cannot reintroduce the drift. Wired as a CI step and a
  pre-commit hook. It parses both heading conventions in use
  (`# ADR-001: Title` and `# ADR-045 — Title`).
- The Art. 11 row now links the index instead of restating a count, which
  removes the drift surface rather than correcting one instance of it.
- Baseline down to 2 entries once the three PRs in this series land, both
  `runtime-artifact` — no `unimplemented` entries remain.

### Fixed — the Copier migration relocated the template tree and the prose never followed

- **Root cause, traced.** ADR-030's migration (commit `fe89e92`,
  2026-06-30) moved the scaffolder payload from
  `templates/{cicd,k8s,monitoring,docs,eda,infra,common_utils,scripts}`
  to `templates/service/*`. It updated all 14 references that *execute*
  — runtime workflows, `Makefile`, `.github/CODEOWNERS`, Kustomize
  overlays, two contract tests, `verify_enterprise_adoption.py` and
  `check_cicd_template_drift.py` itself — because those break loudly.
  It updated **none of the prose**: 30 documents kept naming directories
  that no longer existed.
- **The failure compounded.** Two audit rounds later, commit `f219895`
  copied the dead `templates/cicd/` path *into the audit tooling*: the
  glob scope of `agentic/rules/18-audit-quality.md` and the Q-01
  unpinned-action sweep in `agentic/workflows/audit-quality.md`, the
  latter with `2>/dev/null` so the missing directory was silent. Both
  propagated across three adapter surfaces and reported green.
- **84 dead path references found; 76 fixed.** The mechanical class was
  resolved perspective-aware: repo-facing documents now point at
  `templates/service/X`, documents that ship into a generated service at
  the service-relative `X`. `CLAUDE.md`'s File Structure block, which
  described a tree that had not existed since June, was rewritten against
  the real layout.
- **Two real defects surfaced by widening the Q-01 sweep** from
  `templates/service/.github/workflows` to all of `templates/`:
  `agentic/rules/05-github-actions.md` mandates SHA pinning on line 199
  and demonstrated `actions/github-script@v7` unpinned on line 92 — the
  rule contradicted itself, and the narrow sweep never looked. The sweep
  also never covered `templates/governance/`, which is exactly where the
  floating references fixed in #79 were hiding. Same root cause as the
  drift gate's scope, found the same way.

### Added — Documentation Path Reference Gate

- `scripts/check_doc_path_refs.py` fails CI when living documentation
  names a repo path that does not resolve. Wired as the `doc-path-refs`
  job and as a pre-commit hook. This is the check whose absence let all
  of the above happen: the repo enforced byte-level drift on vendored
  scripts, digest pinning on images and SHA pinning on actions, but never
  verified the claims its own documents make about its own layout.
- **Dual-perspective resolution.** A reference is valid if it resolves
  from the repo root *or* from `templates/service/`, because `agentic/**`
  is mirrored byte-for-byte into the service tree and `AGENTS.md` /
  `AGENT_CONTEXT.md` are vendored verbatim — one text serves two readers.
- **Frozen records are excluded** (`docs/decisions/`, `docs/audit/`,
  `releases/`, `CHANGELOG.md`, `VALIDATION_LOG.md`). An ADR is supposed
  to name the tree as it stood; rewriting it would falsify the record.
- `.doc-path-baseline.yml` carries the 7 residual references, each with a
  reason and an expiry, in the same shape as `.security-baselines/`. The
  gate fails on a new dead path, on an expired entry, and on an entry
  that starts resolving — so the baseline can only shrink.
- Five of the seven are classified `unimplemented`: documented workflows
  reach for `scripts/smoke_test.py`, `scripts/load_test_services.py`,
  `templates/service/tests/test_context_files_hygiene.py`,
  `docs/runbooks/drills`, `docs/audit/baseline-review.md` and an ADR index
  that `docs/COMPLIANCE_MAPPING.md` cites as EU AI Act Art. 11 evidence.
  None exist. They carry short expiries so the gap forces a decision
  rather than aging quietly.
- Contract test: `templates/tests/unit/test_doc_path_refs_contract.py`,
  38 cases pinning token classification, frozen-record exclusion,
  dual-perspective resolution and all three baseline failure modes.
- Rationale and the full contract: `docs/governance/doc-path-references.md`.

### Fixed — the Kyverno admission smoke tested whatever Kubernetes version kind happened to bundle

- `scripts/test_kyverno_admission.sh` called `kind create cluster` with no
  `--image`, so the Kubernetes version the shipped ClusterPolicies were
  proven against was whatever the kind binary bundled at that moment.
  That version moves with every `helm/kind-action` bump, which means a
  routine dependency PR could silently change the platform the admission
  contract is validated on — and the smoke would still report green.
- Surfaced while reviewing #81 (`kind-action` 1.14.0 → 1.15.0), whose
  release notes carry `chore: bump default kind and kubectl`. The
  golden-path workflows were unaffected because they pass an explicit
  `node_image`; this script was the one place that did not.
- Pinned to `kindest/node:v1.30.0` via `KIND_NODE_IMAGE`, the same image
  `KIND_IMAGE` pins in `golden-path.yml` and `golden-path-extended.yml`,
  so admission and end-to-end now test one platform. Overridable by
  environment variable, matching the existing `KYVERNO_VERSION` idiom.

### Fixed — ADR-026 branch protection was documented but never deployed

- **The contract existed on paper only.** `docs/governance/branch-protection.md`
  and `docs/decisions/ADR-026-branch-protection.md` had specified two
  rulesets since 2026-05-15, and `scripts/setup_branch_protection.sh` was
  written to apply them — but nobody had run it. `GET /rulesets` returned
  `0` entries and `GET /rules/branches/main` returned `0` rules, so `main`
  accepted direct pushes, force-pushes and deletion for the entire period
  the repository advertised itself as protected.
- Both rulesets are now active: `main-branch-baseline` (deletion,
  non-fast-forward, linear history, pull request, six required status
  checks) and `tag-immutability-v`.
- Verified the six required contexts resolve: `ci-examples.yml` and
  `validate-templates.yml` both trigger on every `pull_request` to `main`
  with no `paths:` filter, so none of the six can ever fail to report and
  deadlock a PR — the failure mode ADR-026 flags as its critical
  invariant.

### Fixed — the ruleset payload left three parameters to GitHub's defaults

- Applying the ruleset surfaced a second problem: GitHub fills any
  parameter a payload omits with its own default and stores the result.
  `allowed_merge_methods`, `require_extra_approval_for_unattributed_changes`
  and `required_reviewers` were all being set that way, so the deployed
  ruleset carried three settings that `docs/governance/branch-protection.md`
  — the declared single source of truth — never mentioned. A future change
  to a GitHub default would have moved the contract silently.
- All three are now declared explicitly in
  `scripts/setup_branch_protection.sh` and documented.
- `allowed_merge_methods` is narrowed to `[squash, rebase]`. A merge commit
  cannot satisfy the `required_linear_history` rule that sits beside it, so
  offering the merge button only to reject the merge afterwards is a worse
  failure mode than not offering it.

### Fixed — Dependabot never saw three of the five Terraform root modules

- **`directory:` is a literal path, not a prefix.** `.github/dependabot.yml`
  declared `terraform` entries for `/templates/service/infra/terraform/gcp`
  and `.../aws` only. Dependabot does not recurse, so `gcp/bootstrap`,
  `aws/bootstrap` and `cloudflare` — each a separate root module with its
  own `required_providers` block — were never scanned and aged silently.
- The blast radius was measurable: `gcp/bootstrap` sat on
  `hashicorp/google ~> 5.0` while the live layer had already moved to
  `~> 8.0` (three majors of divergence), and `aws/bootstrap` on
  `hashicorp/aws ~> 5.0` against a live `~> 6.43`. Both provision the
  Terraform state bucket, the KMS keys that encrypt it, and the container
  registry — the layer an adopter runs *first* and then never revisits
  (ADR-015 PR-A2), which is exactly why it cannot rely on manual bumps.
- This is the same blind-spot class the CI/CD Template Drift Gate was
  built to catch (`docs/governance/cicd-templates-drift.md`): a scanner
  whose scope is narrower than the surface it is trusted to cover.
- Added the three missing `terraform` ecosystems and brought both
  bootstrap layers onto the same provider surface as their live
  counterparts (`google ~> 8.0`, `aws ~> 6.43`).
- Upgrade impact audited against the google 6/7/8 and aws 6 upgrade
  guides. Nothing in the bootstrap inventory is affected: no
  `lifecycle_rule.condition.no_age` (removed in google 6), no
  `retention_period` on `google_storage_bucket` and no
  `public_repository` on `google_artifact_registry_repository` (both
  changed in google 7), no `region` argument on `aws_s3_bucket` (repurposed
  by aws 6 Enhanced Region Support). The one behavioural change that does
  land is additive: google 6 attaches a `goog-terraform-provisioned` label
  to newly created resources by default.
- Verified with `terraform init -backend=false && terraform validate`
  across all five root modules, resolving `google 8.1.0`, `aws 6.63.0` and
  `cloudflare 5.24.0`. All green.

### Fixed — the Cloudflare edge module had no CI validation at all

- `Terraform Validate` covered four root modules and skipped
  `templates/service/infra/terraform/cloudflare`, one of the three
  accepted edge-protection components under ADR-042 / D-38. A provider
  bump could have broken it with CI staying green. Added the fifth
  validate step.

### Fixed — the CI/CD Template Drift Gate had a narrower scope than the surface it guards

- **The gate scanned one directory and was trusted to cover a tree.**
  `scripts/check_cicd_template_drift.py` walked
  `templates/service/.github/workflows/` only, on the assumption that the
  scaffolder's workflow directory was the sole place shipping GitHub
  Action references to adopters. It was not.
- `templates/governance/promote-with-approval.yml` is copied into the
  adopter's own `.github/workflows/` by hand
  (`templates/governance/README.md`) and carried a floating
  `actions/setup-python@v5` and `actions/checkout@v4` — unpinned against
  the SHA-pinning policy the rest of the repo follows, and two and three
  majors behind runtime respectively. Both sat there for the entire life
  of the gate, reported green, precisely because they were outside the
  scan scope.
- The scan scope is now the whole `templates/` tree. Widening it costs
  nothing in false positives: the comparison is an intersection over
  action names, so template-only actions (the cloud-auth actions an
  adopter needs but this repo never runs) stay ignored exactly as before.
  Re-running the widened gate against the pre-fix tree fails on
  `actions/checkout` with `template-only versions to remove or upgrade:
  ['v4']`, which is the evidence that the old scope was the bug.
- `actions/checkout@v4` is now pinned to the same SHA runtime uses
  (`3d3c42e5…`, v7.0.1). The `setup-python@v5` half was pinned in #76.
- A tag-versus-SHA divergence is caught as a side effect of the subset
  rule, since a floating tag is a version runtime does not use. Documented
  as such; a dedicated `--require-sha` mode remains separate hardening.

### Fixed — the drift gate's governance doc pointed at a directory that no longer exists

- `docs/governance/cicd-templates-drift.md` described the gate in terms of
  `templates/cicd/`, a path removed in the Copier migration (ADR-030). The
  script had moved to `templates/service/.github/workflows/` and the doc
  never followed, so the canonical record of an enforced contract
  described a directory that had not existed for several releases. Paths
  corrected throughout and the scope change documented.

## [0.26.0] — 2026-08-08

Closes the root cause behind four consecutive releases of pinning
(ADR-045). The `v1.x` frozen audit snapshots now live under `archive/v1.x`,
outside the version namespace.

### Changed — frozen audit snapshots archived out of the version namespace

- **The defect was never the numbering; it was the namespace.** This repo
  used one mechanism — git tags — for two incompatible purposes: release
  markers that tooling resolves automatically, and frozen audit artifacts
  that must never be resolved. `v1.12.0` outsorted every `v0.x` release, so
  it won every unpinned resolution.
- `v1.0.0`…`v1.12.0` → `archive/v1.0.0`…`archive/v1.12.0`, **same commits,
  same trees, same signatures**. Verified 15/15 by SHA, with `git diff`
  confirming byte-identical trees.
- **Why it works**: Copier filters tags through a PEP 440 check before
  sorting (`copier/_vcs.py:get_latest_tag`). `archive/v1.12.0` is not a
  valid PEP 440 version, so it is discarded before the sort. The same
  filter protects `sort -V`, `git describe` heuristics, "latest release"
  queries, and dependency bots — this is not a Copier-specific patch.
- **Measured, before and after**, on the bare unpinned commands that caused
  every prior defect:

  | Command | Before | After |
  | --- | --- | --- |
  | `get_latest_tag()` | `v1.12.0` | **`v0.25.0`** |
  | `copier copy` (no ref) | 435 files, no answers file | **627 files, `_commit: v0.25.0`** |
  | `copier update` (no ref) | 627 → 435, 582 deleted, answers file gone | **627 → 627, 0 deleted, answers file present** |

- **Pins and guards are retained.** `--vcs-ref` pinning is correct practice
  for any Copier template regardless of this repo's history, because
  silently resolving to the highest tag is surprising in general. This
  release removes the trap; the pin stays as hygiene.

### Changed — the immutability rule, reinterpreted explicitly (ADR-045)

- `agentic/rules/18` and `docs/RELEASING.md` §2 amended. The rule protects
  **the commit and its content**, not the reference name used to reach it:
  a tag must never move to a different commit, and history must never be
  re-signed to imply verification that did not happen. Renaming a reference
  while preserving commit, tree and signature is a tooling-namespace
  decision.
- Deleting an archived snapshot outright remains **forbidden** — archiving
  preserves provenance, deletion destroys it, and the two must not be
  confused.
- Renumbering the active line past `v1.12.0` was considered and **rejected**:
  `RELEASING.md` §2 reserves `v1.0.0` for cloud E2E evidence, and inflating
  the version to outrank a dead tag would make the version number misstate
  the project's maturity to satisfy a sort order.

### Fixed — stale rule text asserting a removed gitleaks workaround

- `agentic/rules/18` still required `.gitleaks.toml` to mirror the legacy
  singular `[allowlist]` alongside `[[allowlists]]`. That mirror was removed
  in `v0.22.0` because gitleaks >= 8.25 refuses to load a config containing
  both. The rule was mandating the exact state that breaks the scanner.

### Known follow-ons

- **The 15 `v1.x` GitHub Releases were re-pointed to their `archive/` tags**
  and remain public. Deleting a tag drafts its release; each was re-pointed
  and un-drafted. Any external link to `/releases/tag/v1.12.0` now 404s —
  acceptable given the template has no adopters beyond the maintainer, and
  the content is duplicated in `releases/v1.*.md`.
- Carried forward: un-rehearsed MIGRATION recovery procedures, `copier
  update` across a real version gap, clock-allowlist brittleness keyed by
  `file:line`, ruff `UP`/`B`/`S`, `mypy` → `pyright`, shadow-lane data,
  codecov still linked to the pre-rename slug.

## [0.25.0] — 2026-08-08

Both defects in this release were found by an adopter **consuming** the
template, not by the template's own CI. Both were partially fixed in
`v0.24.0` — which is the more useful finding.

### Breaking for adopters

| Change | Manual action required |
| --- | --- |
| **`/scaffold-update` workflow now pins `--vcs-ref`** | If you scaffolded under `v0.24.0` or earlier, the `agentic/workflows/scaffold-update.md` in **your service** carries an unpinned `copier update`. Running it downgrades the service and deletes `.copier-answers.yml`. Pull this release, or edit the two commands in that file by hand before invoking `/scaffold-update`. |
| **Repository renamed to `ml-service-template` in live instructions** | None — GitHub 301-redirects the old path (verified). Update any bookmarks or forks pointing at `ML-MLOps-Production-Template` at your convenience. |

### Fixed — `v0.24.0` pinned three of four surfaces

- `v0.24.0` pinned `--vcs-ref` on `copier.yml`, the `scaffold-update`
  **skill**, and rule `15-template-lifecycle`. It missed
  **`agentic/workflows/scaffold-update.md`** — the one an operator actually
  executes as `/scaffold-update`, and which is vendored into **every
  generated service**. Unpinned there, the destructive downgrade documented
  in `v0.24.0` was still one command away, in the exact place an operator
  would run it.
- **Root cause is the guard, not the omission.**
  `check_adopter_scaffold_ref.py` carried a hand-written list of three
  files. A guard whose coverage is a literal list is only ever as complete
  as the moment someone last remembered to edit it — the same defect class
  as the six-copy exclude list closed in `v0.23.0` and the gitleaks
  three-site pin closed in `v0.22.0`.
- **The guard now scans the tree** for executable `copier update` commands
  instead of enumerating files, skipping historical records. Re-run against
  the pre-fix tree it finds all six occurrences (canonical, vendored, and
  the `.devin` adapter) that the enumerated version missed.

### Fixed — generated services inherited a repository name that no longer exists

- Live adopter instructions, JSON Schema `$id` values, and files vendored
  into every generated service still carried `ML-MLOps-Production-Template`.
  The repo is `ml-service-template`.
- **The audit finding was right to flag it and wrong about the reason.**
  This is not a private-repo leak: the repository is public and GitHub
  301-redirects the old path (verified: `301 → .../ml-service-template`).
  It is a stale identifier that every generated service inherited.
- Renamed in live instructions, runbook examples, schema `$id`s, and the
  files vendored into the render root. Verified: zero occurrences in a
  freshly generated service.
- **The codecov badge deliberately keeps the old slug.** Codecov does not
  follow GitHub's redirect and is keyed on the pre-rename name — fetching
  both confirms the old path returns `40%` and the new one returns
  `unknown`. "Fixing" it to match the other badges would break a working
  badge. A comment in `README.md` says so, next to the badge.
- Historical records (`CHANGELOG.md`, `releases/`, `docs/audit/`, ADRs) are
  **not** rewritten. They record what was true when written.

### Fixed — a broken link in `RUNBOOK.md` (pre-existing)

- `RUNBOOK.md:138` pointed at `templates/docs/CHECKLIST_RELEASE.md`; the
  file lives at `templates/service/docs/CHECKLIST_RELEASE.md`. Present on
  `main` and surfaced only because this release touched `RUNBOOK.md` and
  re-triggered the Link Check job on it — the job runs on changed markdown,
  so a stale link in an untouched file can sit indefinitely.

### Known follow-ons

- **The `v1.x` tag-sort collision remains unresolved** — now four defects
  and four pins. Still needs its own ADR.
- **Codecov project is still linked to the old repo slug.** Re-linking it
  is a codecov-side action outside this repo; until then the badge URL and
  the repo URL disagree by design.
- Carried forward from `v0.24.0`: un-rehearsed MIGRATION recovery
  procedures, `copier update` across a real version gap, clock-allowlist
  brittleness, ruff `UP`/`B`/`S`, `mypy` → `pyright`, shadow-lane data.

## [0.24.0] — 2026-08-07

Hotfix for a **destructive** defect in `v0.23.0` and earlier, found by
running `copier update` on a freshly generated service instead of assuming
it worked.

### Breaking for adopters

| Change | Manual action required |
| --- | --- |
| **`copier update` now requires `--vcs-ref`** | Use `copier update --vcs-ref=v0.24.0 …`. A bare `copier update` **downgrades your service to a frozen April 2026 snapshot and deletes `.copier-answers.yml`**, removing the update path itself. If you already ran one, see `MIGRATION.md`. |

### Fixed — a bare `copier update` destroyed the service it was meant to upgrade

- `v0.23.0` pinned `--vcs-ref` on every documented `copier copy`, but left
  **`copier update`** unpinned — in `copier.yml`'s `_message_after_copy`,
  the `scaffold-update` skill, and rule `15-template-lifecycle`. Same tag
  resolution (highest-sorting tag wins, and the frozen `v1.x` audit
  snapshots sort above every `v0.x`), far worse consequence.
- `copier copy` unpinned gives you a stale scaffold. **`copier update`
  unpinned rewrites a current service backwards.** Measured on a real
  `v0.23.0` service:

  | | Unpinned | Pinned `--vcs-ref=v0.23.0` |
  | --- | --- | --- |
  | Files after | **435** | 627 |
  | Files deleted | **582** | 0 |
  | `.copier-answers.yml` | **deleted** | present |

  The deleted answers file is the sharp edge: it is the record `copier
  update` needs, so the service cannot recover on its own afterwards.
- `_message_after_copy` was actively telling every adopter to run the
  destructive form, as the last thing they read after scaffolding.
- `scripts/check_adopter_scaffold_ref.py` now covers `copier update` as
  well as `copier copy`. Verified in both directions.

### Known follow-ons

Unchanged from `v0.23.0`, plus:

- **The `v1.x` tag-sort collision keeps producing defects** — three so far
  (`copier copy` docs, `copier update` docs, and the release that reached
  nobody). Each fix has been a pin. The structural options — moving the
  snapshots out of the tag namespace, or advancing the active line past
  `v1.12.0` — remain open and need their own ADR. Pinning is correct under
  all of them, but it is mitigation, not resolution.

## [0.23.0] — 2026-08-07

`v0.x.0` bump carrying **full MAJOR paperwork** per the newly-added
[`docs/RELEASING.md`](docs/RELEASING.md) §2.1. The pre-commit hook set
changed, which §1.3 classifies as MAJOR — but §2 reserves `v1.0.0` for
cloud E2E evidence, so a MAJOR-class change had nowhere to go. §2.1
resolves that gap: on the pre-GA channel the *number* is smaller, the
*obligations* are identical.

### Breaking for adopters

| Change | Manual action required |
| --- | --- |
| **`black`, `isort`, `flake8` pre-commit hooks removed; `ruff-check` + `ruff-format` added** (ADR-044) | Run `pre-commit install --install-hooks`. Adopters with a custom `.pre-commit-config.yaml` overlay must merge the swap in both the repo config and the scaffolded service's. Custom `[tool.black]` / `[tool.isort]` sections should move to `[tool.ruff]`. |
| **`ruff format` output differs from `black`** | Expect a one-time reflow of your Python tree (55 of 134 files here). Apply it as an isolated commit and register it in `.git-blame-ignore-revs`. |
| **Adopter scaffold command now requires `--vcs-ref`** | Use `copier copy --vcs-ref=v0.23.0 …`. Without it Copier resolves to the highest-sorting tag, which is a frozen `v1.x` audit snapshot. |

### Fixed — generated services carried 33 unresolvable ADR references

- The render root cites **39** template ADRs and vendors **6**. The other
  33 were dangling: a consuming repo's reference checker flagged them as
  missing files, and the 6 that do ship collide with the adopter's own ADR
  numbering.
- Renaming to `template-ADR-NNN` — the obvious fix — is **structurally
  blocked**: `check_vendored_runtime_drift.py` holds
  `templates/service/agentic`, the shipped ADR files, and the config
  schemas byte-identical to their root counterparts. Rewriting identifiers
  there would break that gate or fork the generated service from upstream,
  making every future `copier update` a conflict.
- Shipped a **resolution layer** instead:
  `templates/service/docs/decisions/README.md` names the collision, lists
  exactly what vendors in, points upstream for the rest, and recommends
  `template-ADR-NNN` for the adopter's *own* prose — where nothing is
  drift-gated. Enforced by `scripts/check_service_adr_references.py`.

### Changed — lint/format toolchain consolidated into ruff (ADR-044)

- `black` + `isort` + `flake8` → **ruff** (`check` + `format`), pinned
  `v0.15.15`, configured once per `pyproject.toml`.
- **Speed was not the reason and is not claimed as one.** The previous
  suite already ran `--all-files` in **2.62 s**, inside the config's own
  `< 5 s` target. The case was six copies of one exclude list (three tools
  × two configs) and a lint coverage hole: `flake8`'s `files:` pattern
  excluded `scripts/` and `templates/tests/`, so the repo's own tooling was
  type-checked and security-linted but never style-linted.
- **Rule scope is parity-only** (`E,W,F,I`). Ruff's `UP`/`B`/`S` rulesets
  were measured at **90 additional findings** and deliberately not enabled:
  mixing them in would turn a toolchain swap into a code change.
- **Ruff does not replace `mypy`** (no type checking) and only partially
  overlaps `bandit`; both retained. Pyright considered and rejected here.
- Formatter reflow (55 files) isolated in its own commit and registered in
  `.git-blame-ignore-revs`. Equivalence verified: the collectible test
  suite returns an **identical** result before and after.

### Fixed — two real defects surfaced by the new lint coverage

- **`test_different_cache_keys_isolated` asserted nothing about isolation.**
  It created `ctx1`, never read it, and only checked `ctx2`. It would have
  passed with caching absent entirely. Fixed by adding the missing
  `assert ctx1 is not ctx2` — not by deleting the variable, which is what
  the linter literally suggested.
- Unused import and two over-length lines in `scripts/`. The regex edit was
  verified to compile to a byte-identical pattern.

### Known follow-ons

- **Clock-isolation allowlist is keyed by `file:line`**, so any reformat
  invalidates it (it did, and was remapped 1:1 after verifying same call
  count, same APIs, same order). Keying on the enclosing test name would
  survive that churn.
- **Ruff `UP`/`B`/`S` rulesets** — 90 findings, deferred to their own ADR.
- **mypy → pyright** — considered in ADR-044, not ruled out later.
- **Shadow-lane precision data** — the ADR-019 lane fires now but has
  classified nothing; Phase 2 still needs 14 days of real data.

### Fixed — the documented scaffold command served a stale template

- **`README.md`, `QUICK_START.md`, `docs/TUTORIAL.md`, `docs/PROGRESSION.md`**:
  every adopter-facing `copier copy` example omitted `--vcs-ref`. Copier
  resolves an unpinned git source to the **highest-sorting tag**, and this
  repo carries the frozen `v1.0.0`–`v1.12.0` audit snapshots (ADR-014)
  alongside the active `v0.x` line. `v1.12.0` sorts above every `v0.x`
  tag, so the documented command served the **April 2026 snapshot**.
- Nothing errored. The adopter received a complete, plausible, stale
  scaffold. Measured at `v0.22.0`: **435 files and no
  `.copier-answers.yml`** unpinned, versus **626 files with a correct
  answers file** when pinned. This means the `v0.22.0` copier-update fix
  did not reach anyone following the documentation.
- Found by executing the adopter path against the published tag rather
  than assuming it — the same method that surfaced the four `v0.22.0`
  defects. The R11 audit noted the adjacent symptom (*"confuses `sort -V`
  and any tooling that assumes monotonicity"*) without connecting it to
  Copier's tag resolution.
- **New `scripts/check_adopter_scaffold_ref.py`**: fails the build if any
  adopter-facing command is unpinned or pins a version other than
  `VERSION`. Wired into pre-commit and CI, and added to the
  `docs/RELEASING.md` §3 checklist so a release cannot ship with the docs
  pointing at the previous one.
- The `v1.x` tags were **not** deleted or renamed. `agentic/rules/18` and
  ADR-014 both declare them immutable; changing that is a governance
  decision requiring its own ADR, not a side effect of a docs fix.

## [0.22.0] — 2026-08-07

MINOR release under [`docs/RELEASING.md`](docs/RELEASING.md) §1.2. The
additions are backward-compatible; the fixes are not behaviour changes to
any shipped contract but repairs to contracts that were **documented and
never actually working**.

The theme of this release is unflattering and worth naming: four separate
capabilities were asserted in documentation, guarded by tests that passed,
and did not function. In each case the test passed *because* of how it was
written, not because the capability worked.

| Capability | Asserted in | Reality |
| --- | --- | --- |
| `copier update` path | ADR-003 + 4 other places | No answers file was ever emitted |
| Secret-scan parity local↔CI | `.pre-commit-config.yaml` header | Two different allowlist dialects |
| Phase-1 disclosure guard | `test_phase0_disclosure.py` | 6/6 skipped, 0 enforced |
| ADR-019 shadow lane | README §"Agentic CI self-healing" | 0 runs in its entire life |

### Fixed — secret-scan parity between local and CI (security)

- **`.gitleaks.toml`, both `.pre-commit-config.yaml` files,
  `validate-templates.yml`**: the pre-commit hook pinned `v8.21.2` while CI
  delegated to `gitleaks-action`'s bundled version. gitleaks changed its
  config dialect at **8.25** — below it the plural `[[allowlists]]` tables
  are silently ignored, at or above it the singular `[allowlist]` form is
  rejected outright. Local and CI therefore scanned the same tree under
  **different allowlists**, silently, in both directions. On a secrets gate
  the local run is the one contributors trust, so the failure mode is a
  false green at exactly the moment a secret would enter history.
- The AUDIT R11 "L-2" compatibility shim (a duplicate singular
  `[allowlist]` block) had become the blocker: modern gitleaks refuses to
  load a config carrying both dialects. Removed — every entry was a strict
  duplicate of the plural tables, so no scan outcome changes on >= 8.25.
- CI now installs an **explicitly pinned binary** rather than inheriting
  one, and passes `--config` explicitly instead of relying on
  auto-discovery that could silently degrade to default rules.
- **New `scripts/check_gitleaks_pin.py`**: fails the build if the three
  declaration sites drift, or if the pin falls below the 8.25 dialect
  floor. Wired into pre-commit and CI.
- Verified: full-history scan (342 commits) under 8.30.1 → `no leaks
  found`. Guard verified in both directions.

### Fixed — a disclosure guard that had silently disarmed itself

- **`templates/service/tests/test_phase0_disclosure.py`**: every check
  opened with `if not _is_phase_0(ADR): pytest.skip(...)`. Both ADRs
  advanced to Phase 1, so all six invariants skipped and the README
  statements they protect became deletable with **no test failing**. A
  guard that disarms itself the moment its subject changes state is worse
  than none: it reports green while doing nothing, so nobody looks.
- Rewritten to be **phase-aware rather than phase-pinned** — it derives
  the requirement from the ADR's declared phase, **fails rather than
  skips** when it cannot determine one, and lifts only when an ADR
  explicitly declares its runtime live. Advancing 1 → 2 → 3 keeps it armed
  with no test edit required.
- **6 skipped / 0 enforced → 10 passed / 0 skipped.**

### Fixed — the ADR-019 shadow lane could never fire

- **`.github/workflows/ci-self-healing-shadow.yml`**: the
  `workflow_run.workflows` list held **filename stems**
  (`validate-templates`, `policy-tests`, …). GitHub matches that field
  against a workflow's `name:` value (`Validate Templates`, `Policy Tests
  (D-XX anti-patterns)`, …). Nothing matched, and GitHub reports no error
  for an unmatchable entry — so the workflow recorded **zero runs across
  its entire life** while reporting `state=active`.
- Consequence beyond the workflow: ADR-019's Phase 1 → Phase 2 gate
  requires *"14 days of shadow precision data"*. Nothing was producing it.
  The roadmap was blocked **by construction, not by choice**, while the
  README claimed the classifier *"observes failures"*.
- **`test_shadow_workflow_phase1.py`** guarded "the workflow is read-only"
  but never "the workflow can run". Added that assertion.
- Verified post-merge: run count **0 → 2**, `event=workflow_run`,
  `conclusion=skipped` (correct — upstream CI succeeded, and the job is
  gated on `conclusion == 'failure'`). The shadow clock is running for the
  first time.

### Changed — adopter-visible

- **gitleaks pre-commit pin `v8.21.2` → `v8.30.1`** in both the template's
  own config and the scaffolded service's. Adopters maintaining a custom
  `.pre-commit-config.yaml` overlay must merge the bump; adopters with a
  custom `.gitleaks.toml` carrying a singular `[allowlist]` table must
  migrate it to `[[allowlists]]`. See `MIGRATION.md`.
- **Scaffolded services now contain `.copier-answers.yml`.** New services
  get it automatically. Existing ones need the recovery procedure in
  `MIGRATION.md`.

### Known follow-ons

Deferred deliberately, not overlooked:

- **Ruff migration** (`black` + `isort` + `flake8` → `ruff`). Scoped: a
  43-file cascade touching `CLAUDE.md` and `agentic/rules/01`, which are
  normative documents mandating the toolchain — so it needs its own ADR
  and its own MIGRATION rows. Measurement removed the urgency argument:
  the current hook suite runs `--all-files` in **2.62 s** warm, inside the
  `< 5 s` target the config header claims. The remaining case is
  maintenance surface (the 8-line `exclude:` block is triplicated), not
  speed.
- **Template ADR namespacing.** Generated services ship six template ADRs
  (`ADR-010`, `-014`, `-018`, `-019`, `-023`, `-043`) with no namespace
  prefix, colliding with the adopter's own ADR numbering. Renaming to
  `template-ADR-NNN` cascades into every cross-reference in the generated
  service.
- **Shadow-lane precision data.** The lane can now fire but has produced
  no failure classifications yet. The Phase 1 → Phase 2 decision stays
  blocked on 14 days of real data — now obtainable rather than impossible.

### Added — Agent-QualityGuardian: audit-grade quality preservation (ADR-043)

- **`docs/decisions/ADR-043-audit-quality-guardian.md`**: charters
  `Agent-QualityGuardian` as a Layer 3 maintenance agent whose scope is
  the enterprise-audit bar itself — running the recurring audit and
  watching for standards that erode silently (license drift, unpinned
  actions, evidence-free releases, weakened gates).
- **Anti-patterns Q-01…Q-08** in the new always-on rule
  `agentic/rules/18-audit-quality.md` — a deliberately separate namespace
  from `D-01→D-38` (see ADR-043 §2 for the rationale: different review
  audience, avoids a 4-document renumber cascade per addition).
- **`enterprise-audit` skill**: recurring 23-domain audit procedure
  (governance → observability → architecture → technical debt → DX),
  composing `rule-audit` and `doc-coherence` and adding the domains
  neither covers. Scan is AUTO, fixing findings is CONSULT, weakening any
  gate is STOP.
- **`/audit-quality` workflow**: operational sequence for the skill above
  — deterministic-gate check, Q-pattern quick sweep, full domain sweep,
  findings triage, report, audit-trail entry, chains to
  `/document-changes`.
- **`/document-changes` workflow**: `Agent-DocUpdater`'s first operational
  entry point (it previously had a Layer-3 charter but no invokable
  procedure) — collects the change surface, writes the CHANGELOG entry,
  propagates the rule-16 cascade, verifies the coherence gate, and
  records an audit-trail entry.
- Surface counts: 18→19 rules, 26→27 skills, 18→20 workflows (both root
  and `templates/service/` manifests, adapters regenerated via
  `sync_agentic_adapters.py`).

### Fixed — R11 enterprise/ISO audit remediation (`docs/audit/AUDIT_R11_ISO_ENTERPRISE.md`)

- **M-2 (supply chain)**: `release-on-tag.yml` gained a
  `supply-chain-evidence` job — source SBOM (CycloneDX + SPDX via syft)
  and a keyless Sigstore signature over the checksum manifest, attached
  to every GitHub Release. The template's own releases now carry the
  same evidence class its scaffolded services already produce for
  images.
- **M-3 (reproducibility)**: `templates/service/Makefile` gained a
  `make lock` target (`uv pip compile --generate-hashes`) producing
  `requirements.lock.txt` for byte-identical rebuilds; `~=` pinning still
  states intent, the lockfile guarantees the rebuild.
- **L-2 (gitleaks false positives)**: `.gitleaks.toml` — added
  `__pycache__` to the redaction-fixture allowlist, and mirrored all
  path/regex exemptions into a legacy singular `[allowlist]` block
  (root cause: gitleaks < 8.25 silently ignores the plural
  `[[allowlists]]` tables, so local scans diverged from CI regardless of
  entry count).
- **Doc drift**: `llms.txt` license badge corrected from `MIT` to
  `Apache-2.0` (matching `LICENSE`); ADR and agentic surface counts
  synced across `llms.txt` and `CLAUDE.md` (rule 16 cascade).
- **L-1 (working-tree hygiene)**: removed a locally-cluttered
  Alertmanager release tarball and extracted directory (never tracked by
  git; local-only clutter).
- **`scripts/audit_record.py` import path** (discovered while exercising
  the Audit Trail Protocol for this change): the `common_utils` import
  pointed at `templates/` — stale from before the `templates/service/`
  nesting; `templates/common_utils` never existed, only
  `templates/service/common_utils`. `scripts/generate_report.py` already
  used the correct path. Every root-level invocation of
  `audit_record.py` (including the `golden-path.yml` `audit-trail` job)
  was silently failing with `ModuleNotFoundError`. One-line fix.

### Fixed — copier post-copy validation required an uninstalled dependency (#56)

- **`scripts/validate_agentic_manifest.py` and its vendored copy under
  `templates/service/scripts/`**: the `jsonschema` import guard hard-exited
  with status 2 when the package was absent. `_tasks` runs the validator
  immediately after copy — *before* the generated service's dependencies
  (which do declare `jsonschema`, in `templates/service/pyproject.toml` and
  `requirements.txt`) have been installed — so copier rolled the entire
  generation back and left **zero files** for any adopter whose ambient
  interpreter lacked the package. The scaffold was unusable, not degraded.
  A post-copy task cannot depend on a package that the copy step itself
  only makes available later.
- **Why it stayed invisible**: this repository's environment happens to
  carry `jsonschema` globally, so the scaffolder E2E job passed. That is
  the failure shape — green for the author, broken for every adopter. It
  surfaced only by actually exercising `copier copy` against a clean
  interpreter rather than trusting the declared dependency.
- **The fix**: the bootstrap guard no longer exits. Every check that does
  not need JSON Schema still runs; the two schema-validation call sites
  record themselves in `skipped_schema_checks` and the count is reported on
  stderr (`JSON Schema validation SKIPPED for N file(s)`), so a skipped
  check can never read as a passed one. CI always installs `jsonschema`,
  so strictness there is unchanged.
- Applied to **both** copies. `_tasks` runs in the destination directory,
  so the script actually executed is the vendored one — patching only the
  root copy would have left the scaffold broken while looking fixed.
  `check_vendored_runtime_drift.py` caught exactly that partial fix.

### Fixed — generated services had no `copier update` path (ADR-003 blocker)

- **`templates/service/{@ _copier_conf.answers_file @}`** (new): the template
  never shipped an answers-file template, so `copier copy` produced services
  with **no `.copier-answers.yml`**. Without it `copier update` cannot run,
  which made every generated service "a fork with extra steps" — the exact
  outcome ADR-003 §"Generation, not copying" exists to prevent. The template
  promised the capability in three places while structurally lacking it:
  ADR-003, the `scaffold-update` workflow ("required for `copier update`"),
  and `_message_after_copy` ("re-run `copier update` in this project").
- **Root cause of the silent failure**: `copier.yml` sets custom `_envops`
  (`variable_start_string: "{@"`). The stock Copier filename form
  (`{{ _copier_conf.answers_file }}`) therefore does not render here — Copier
  emits it as a *literal* filename instead of erroring, so the omission
  produced no signal at generation time.
- **`scripts/test_scaffold.sh`**: the E2E suite carried a comment asserting
  that `.copier-answers.yml` "is only created by `copier update`, not
  `copier copy`" and that its absence "is expected". That is false, and it
  encoded the defect as intended behaviour — which is why a template that
  could never be updated passed its own scaffolder suite for its entire
  life. Replaced with a real assertion covering file presence, `_commit`
  (the revision `copier update` diffs against), and answer persistence.
  Verified in both directions: the guard exits 1 with the answers template
  removed and passes with it present.
- **Verified end-to-end**: `copier copy` → `git init` → template commit →
  `copier update` pulls the change into the generated service and advances
  `_commit`. This is the first time the ADR-003 update path has been
  executed rather than assumed.
- **Adopter impact**: services generated before this fix have no answers
  file and cannot be updated in place. See `MIGRATION.md` for the recovery
  procedure (write the file by hand with the originating `_commit`).
- **Reaches adopters only on the next tag.** `copier copy` against a git
  source resolves to the latest *tag*, not `HEAD`, so this fix is inert
  until a release is cut.

## [0.21.0] — 2026-07-06

MINOR release under [`docs/RELEASING.md`](docs/RELEASING.md) §1.2
(backward-compatible additions: new ADRs, new agentic surface, new
scaffolded-service files and Terraform modules, no renamed/removed
contract). This release closes five initiatives that completed
sequentially since `v0.20.0` without a version being cut for any of
them: the R8 Staff/Lead dual-repo audit, R9 Wave A enterprise-benchmark
remediation, R10 documentation-coherence hardening, ADR-041's agentic
skill/domain expansion, and this release's own six-station monitoring
audit + native-cloud-first edge-protection introduction.

### Added — Native-cloud-first edge protection (ADR-042, D-38)

- **`docs/decisions/ADR-042-native-cloud-edge-protection.md`**: Cloud
  Armor (GCP) and AWS WAFv2 + Shield Standard are the DEFAULT,
  per-cloud edge-protection implementation; Cloudflare is a fully
  optional third module for genuinely concurrent multi-cloud
  deployments or a zero-cloud-account learning path — never the
  default, since it would add a third-party account + DNS delegation
  for the common single-cloud deployment this template targets.
  Includes a verb-separated mode assignment mirroring ADR-039:
  `terraform apply` of edge resources is CONSULT in every environment
  including dev (public exposure + cost do not shrink because the
  label says "dev"); disabling or loosening an existing WAF/rate-limit
  rule is STOP in every environment, no exceptions.
- **Anti-pattern D-38** + rule `17-edge-protection` + skill `edge-audit`
  (AUTO, read-only coverage scanner, mirrors `rule-audit` for one
  invariant domain) + workflow `/edge-setup` (CONSULT). Surface counts:
  rules 17→18, skills 25→26, workflows 17→18, anti-patterns
  D-01..D-37→D-01..D-38 — cascaded through `AGENTS.md`, both
  `CLAUDE.md` files, `README.md`, `llms.txt`, and
  `templates/config/agentic_manifest.yaml`.
- **`k8s/components/edge-{gcp,aws}/`**: opt-in Kustomize Components
  (not wired into any overlay by default, matching D-35's local-first
  philosophy) — a GCE/ALB Ingress carrying an
  `edge-protection.mlops-template.io/implementation` annotation, the
  one signal `edge-audit` and the new D-38 policy test trust.
- **Terraform**: `infra/terraform/gcp/security.tf` (Cloud Armor +
  `MODERN`-profile SSL policy), `infra/terraform/aws/security.tf`
  (WAFv2 Web ACL; Shield Standard is automatic/free, no resource
  needed), `infra/terraform/cloudflare/` (optional DNS + managed
  ruleset + rate-limit module) — all three validated with
  `terraform validate` against their real, currently-published
  provider schemas. `ci-infra.yml`'s matrix gains a `cloudflare` leg.
- **`dashboard-edge.json`** + two new alerts
  (`EdgeProtectionMissing`, `EdgeAuditHeartbeatMissing`) — deliberately
  does NOT duplicate Cloud Armor's / WAFv2's / Cloudflare's own
  per-rule hit-rate analytics; tracks only coverage + audit freshness,
  the one signal none of those consoles can answer.
- **`docs/runbooks/edge-protection-setup.md`**: setup steps per cloud +
  a cloud equivalence matrix. The three implementations' rate-limit
  windows/semantics genuinely differ (Cloud Armor: configurable,
  default 60s; AWS WAFv2: fixed 5-minute window, block-only, no native
  throttle; Cloudflare: fixed 10s) — documented so nobody copies a
  threshold number across clouds assuming equivalent behavior.

### Added — Monitoring-stations audit closure (Inference, Business KPIs, Logs & Traces)

- **`docs/observability/monitoring-stations.md`**: a full coverage map
  against six operational stations (Edge, Infrastructure, Inference,
  Models, Logs & Traces, Business KPIs) with file:line evidence,
  delegation notes, and explicit N/A-with-revisit-trigger notes (e.g.
  GPU metrics — N/A while the stack is CPU sklearn/XGBoost/LightGBM).
- **Inference — saturation**: new `inference_in_flight` /
  `inference_executor_capacity` Prometheus gauges (wrapped around both
  `run_in_executor` call sites), a new dashboard panel, and the
  `{@ service_slug @}ExecutorSaturated` alert — the saturation ratio
  reaching 1.0 is itself the queueing signal, deliberately not read
  from `ThreadPoolExecutor._work_queue.qsize()` (a private attribute).
- **Business KPIs**: `dashboard-business.json` (request volume, SLA
  compliance reusing the existing SLO recording rule, cost vs. budget,
  risk-level mix, error-rate impact) +
  `docs/observability/business-kpis.md`. The `cost-audit` skill now
  pushes `<service>_monthly_cloud_cost_usd` to Pushgateway (Step 2b).
- **Logs & Traces — a real gap closed**: `RequestIDMiddleware` now
  emits a structured access-log line (`request_id`, `trace_id`,
  `method`, `path`, `status_code`, `duration_ms`) on every non-probe
  request. Previously `request_id` only reached the log stream on the
  unhandled-exception path — a successful request produced no
  correlatable log line at all. `docs/observability/log-trace-correlation.md`
  documents the fix and how to wire a real tracing-backend derived
  field once an adopter enables OTel.
- **Loki + Promtail** added to the docker-compose demo stack (`monitoring`
  profile) — demo-only by design; production log aggregation is the
  platform's job (Fluentd/Fluent Bit DaemonSet), not this template's.
  Grafana datasource auto-provisioning also fixes a pre-existing gap:
  Prometheus itself was never auto-provisioned before this.

### Fixed — AUDIT R10 (documentation language + private-reference leak)

- **Four `docs/audit/*.md` files translated from Spanish to English**
  (`ACTION_PLAN_LLM_AGENT.md`, `ARCH_REVIEW_LLM_AGENT.md`,
  `AUDIT_R8_STAFF_LEAD.md`, `ACTION_PLAN_R9_ENTERPRISE_BENCHMARK.md`, plus
  the small `ACTION_PLAN_ADR028.md` stub and 38 bilingual section titles in
  `feedback-may-2026-triage.md`): this repo's documentation is English-only
  everywhere else; these were leftover interactive-session artifacts.
- **A private, personal companion repo de-referenced by name from 6
  files** in this repo (`docs/audit/{ACTION_PLAN_LLM_AGENT,
  ACTION_PLAN_R9_ENTERPRISE_BENCHMARK,AUDIT_R8_STAFF_LEAD}.md`,
  `docs/decisions/{ADR-028,ADR-037}.md`, `CHANGELOG.md`) and one in the
  sibling `agent-local` repo (`docs/decisions/ADR-008-*.md`) — one instance
  was a live clickable URL pointing at it, which 404s for any public
  reader since that repo is private. `ADR-037` and `ADR-028`
  were re-generalized (not just redacted): the "L-2b pedagogical RAG"
  design now describes "the adopter's own long-form onboarding corpus"
  rather than a hard-coded reference to the author's private repo — a
  strictly more reusable design for a public template.
- **A pre-existing D-36 rollout gap**: `CLAUDE.md`'s compact anti-pattern
  table (root and vendored `templates/service/CLAUDE.md`) had never
  actually gained a D-36 row when D-36 shipped (R9 Wave A) — it still
  ended at D-35 and claimed "35 invariants." Backfilled alongside this
  round's D-37.
- **Stale `Windsurf` references** in currently-live docs (`QUICK_START.md`,
  `README.md` badge + 2 prose mentions, `docs/ide-parity-audit.md`,
  `docs/agentic/runtime-monitoring-companion.md`,
  `docs/runbooks/mcp-config-hygiene.md`,
  `templates/config/mcp_registry.yaml`): the template's agentic surface
  moved to Devin/Cursor/Claude Code/Codex a while ago; these had not been
  updated to match and one (`ide-parity-audit.md`) still described the
  pre-ADR-027 architecture where the canonical source was literally named
  "Windsurf" instead of the current vendor-neutral `agentic/`.

### Added — ADR-041 (agentic skill and domain expansion)

- **Four new skills**: `pr-review` (dual-axis Standards + Spec review,
  evaluated in isolation), `diagnose-bug` (systematic non-ML-serving
  bug diagnosis: reproduce → minimize → hypothesize → instrument → fix →
  regression-test), `new-service-spec` (captures the ML problem spec —
  label, fairness attribute, cost asymmetry — before scaffolding, via a
  new `templates/config/service_spec.schema.json` +
  `service_spec.example.yaml` pair), and `incident-postmortem`
  (blameless post-incident review: timeline from primary sources,
  5-whys, owned action items). Skill count 21 → 25.
- **`domain:` taxonomy** on every skill's `agentic_manifest.yaml` entry
  (`ml-data` / `platform-infra` / `security-compliance` /
  `sre-operations`) for role-based filtering/discoverability, validated
  by a new `_validate_domain_enum` check in
  `validate_agentic_manifest.py --strict`. Orthogonal to AUTO/CONSULT/STOP.
- **Explicit model-invoked (skill) vs. user-invoked (workflow) framing**
  documented in `AGENTS.md`'s "How to Invoke Skills and Workflows" —
  the distinction already existed structurally; it is now named.
- **Fixed a pre-existing drift**: the agentic-surface ASCII tree in
  `AGENTS.md` had been missing `ci-green-verify`/`ci-green` since
  ADR-039 shipped (a hand-maintained block, not covered by the C4
  live-count gate). Corrected alongside the new entries.
- Full rationale, and the external repos evaluated and explicitly
  rejected (sandcastle, BMAD's full persona system, spec-kit's full
  pipeline, `find-skills` as a running tool) with reasons and revisit
  triggers: **ADR-041**.

### Added — AUDIT R10 (documentation coherence hardening)

- **`check_doc_coherence.py` C7 + anti-pattern D-37 + ADR-040**: a new
  deterministic check — `check_doc_language_and_privacy` — scans every
  git-tracked `docs/**/*.md` and root `*.md` file for non-English prose
  (a curated Spanish word list, accent required — an early draft made the
  accent optional and flagged the English word "decision" repo-wide,
  caught in local testing) and for a private-repo denylist (seeded with the
  repo named above, see ADR-040). This is the gate that should have caught
  the R10 finding above and now will, going forward. Vendored into
  `templates/service/scripts/check_doc_coherence.py`; adapters
  (`.devin/.claude/.cursor/.codex`) resynced via
  `sync_agentic_adapters.py`.

### Added — AUDIT R9 Wave A (enterprise benchmark remediation)

- **OpenSSF Scorecard** (`.github/workflows/scorecard.yml`, R9-01): weekly +
  on-push supply-chain scoring, published and SARIF-uploaded to the Security
  tab — makes the template's existing signing/SBOM/branch-protection posture
  independently verifiable instead of just claimed.
- **All GitHub Actions pinned by commit SHA** (R9-02): every third-party
  action across all 13 workflow files now resolves to an immutable SHA
  (`uses: owner/action@<sha> # vX.Y.Z`), resolved live against the GitHub
  API. Closes the tag-mutability supply-chain gap Scorecard's
  Pinned-Dependencies check flags (the class of exposure behind the 2025
  tj-actions incident). `dependabot.yml` already tracks the
  `github-actions` ecosystem, so SHA bumps keep flowing as PRs.
- **`docs/COMPLIANCE_MAPPING.md` + ADR-038** (R9-03): maps existing template
  artifacts (quality gates, fairness DIR gate, drift monitoring, audit
  trail, human-in-the-loop promotion, signed supply chain) to NIST AI RMF,
  ISO/IEC 42001, and EU AI Act Arts. 9–15 control questions — explicitly
  NOT a certification claim (see the document's own non-claims section).
  Includes a verified Digital Omnibus timeline note (Annex III high-risk
  obligations move 2026-08-02 → 2027-12-02).
- **Portability & escape-hatches matrix** in `docs/ADOPTION.md` §4 (R9-04):
  a per-dimension swap table (cloud, tracking/registry, serving backend,
  model framework, data validation, drift tooling, scaffolding engine, IaC,
  agentic host) stating exactly what changes and what stays fixed.
- **CI-green verification agentic gate** (R9-05, ADR-039): new skill
  `ci-green-verify` (AUTO to check, STOP to override red/missing — verbs
  separated on purpose, mirroring GitHub branch-protection's own
  view-vs-bypass split) + `/ci-green` workflow + anti-pattern **D-36**.
  Wired as a hard precondition into `agentic/workflows/release.md` step 1
  (was a non-blocking `gh run list`) and into `deploy-gke`/`deploy-aws`
  before `staging`/`prod` (Step 0, `dev` exempt). Surface counts: skills
  20→21, workflows 16→17, anti-patterns D-01..D-35→D-01..D-36 — cascaded
  through `AGENTS.md`, both `CLAUDE.md` files, `llms.txt`, `README.md`, and
  `templates/config/agentic_manifest.yaml`; `sync_agentic_adapters.py` +
  `validate_agentic_manifest.py --strict` + `validate_agentic.py` all green.

### Fixed

- **AUDIT R8-05 — root `pytest -q` collects again** (`pyproject.toml`):
  the three sibling test packages all named `tests`
  (`templates/service/{tests,eda/tests,monitoring/tests}`) collided in
  `sys.modules` under pytest's default `prepend` import mode and aborted
  collection from the repo root. Fixed with `--import-mode=importlib` in the
  root `addopts` (verified: zero cross-test `from tests…` imports exist, so
  importlib is safe). A new **"Root suite collects" guard step** in
  `template-context-tests.yml` runs `pytest --collect-only -q` from the root
  so this supported dev flow can never break without CI signal again.
  Second layer, exposed by CI run 28552562658: invocations with paths under
  `templates/service/` resolve rootdir to the SERVICE `pyproject.toml`, so
  the flag is set there too — and it ships to scaffolded services, which
  carry the same three-sibling `tests` layout.
- **Alertmanager routing contract revived** —
  (`templates/service/monitoring/tests/test_alertmanager_routing.py`): the
  module anchored its config paths at `parents[3]` from before the Copier
  Stage-2a relocation, resolving to a nonexistent `templates/templates/…` —
  it had been **uncollectable (dead) since the move**, masked by the R8-05
  collision. Paths are now file-relative (`parents[1]`, correct in template
  context AND in a scaffolded service), the local amtool unpack is
  discovered by ancestor walk with an `X_OK` guard (a non-executable unpack
  means *skip*, never *fail*), and the module is now executed in CI: the
  template-context lane runs `monitoring/tests` + `eda/tests` explicitly.
  All 14 tests pass locally including the 6 amtool-authoritative ones.
- **AUDIT R8-08 — async tests execute from the root env**
  (`pyproject.toml`): `asyncio_mode = "auto"` + `pytest-asyncio` in the
  template-context lane install; previously a root run without the plugin
  collected `@pytest.mark.asyncio` tests as unknown-marked no-ops.
  Full root suite after all fixes: **963 passed, 0 failed**
  (`pytest -q -p no:locust -m "not scaffold_context"`).

### Added

- **AUDIT R8 — Staff/Lead dual-repo audit (template + agent-local)**
  (`docs/audit/AUDIT_R8_STAFF_LEAD.md`): first audit round covering
  `agent-local` as a full audit subject alongside this template. Verdicts:
  template 9.1/10 (no new architecture/security/supply-chain findings; two
  local-DX findings — root `pytest -q` collection collision between three
  sibling `tests` packages, and missing `pytest-asyncio` in the root env),
  agent-local 7.9/10 (12 findings, worst: synchronous multi-LLM loop inside
  an `async def` endpoint — the D-24 class — plus exception-detail leak,
  discarded `reflect()` output, and a triple version drift with no coherence
  gate). Includes a governance-parity matrix (every discipline that traveled
  to agent-local without its gate has a live finding), per-dimension scoring
  for both repos, structural knowledge-graph verification of D-24 across all
  10 `.predict*` call sites, and a prioritized P0/P1/P2 action plan.
- **ADR-037 — Dual-namespace retrieval separation (operational memory vs.
  pedagogical RAG)**
  (`docs/decisions/ADR-037-dual-namespace-retrieval-separation.md`):
  canonicalizes a new `L-2b` maintenance-plane lane in
  `docs/audit/ACTION_PLAN_LLM_AGENT.md` — a pedagogical/onboarding RAG over
  the adopter's own long-form documentation + ADR prose, built as a
  namespace-disjoint sibling of the existing `L-2` operational memory plane
  (ADR-018), never the same index or script. Two disjoint scripts (`scripts/memory_query.py` vs. the new
  `scripts/pedagogy_query.py`), two hard-coded disjoint corpus-root
  allow-lists, two independent `BM25Index` objects, one shared *stateless*
  agent-local tier endpoint (justified by agent-local's new ADR-008), and a
  citation-path validator that discards (and logs) any answer whose citation
  resolves outside its own namespace. Both scripts are specified, not yet
  shipped — gated behind the same "P2 INTEGRATION" timeline as the rest of
  the memory-plane lane.
- **ADR-036 — Batch-only deployment topology**
  (`docs/decisions/ADR-036-batch-only-deployment-topology.md`): a new
  `templates/service/k8s/overlays/batch-only/` Kustomize overlay for
  adopters who never run the live `/predict` API — only scheduled batch
  scoring. Includes the full `../../base` and removes the online-serving
  resources (Deployment, Service, HPA, PDB, both `AnalysisTemplate`s,
  performance/drift `CronJob`s + their `PrometheusRule`s, the
  online-shaped `NetworkPolicy`) via one-resource-per-file
  `$patch: delete` — a multi-document patch file panics the bundled
  kustomize/kyaml version (verified locally); ships a working
  `cronjob-batch.yaml` and a dedicated `networkpolicy-batch.yaml` (the
  base policy's `podSelector` would not match the batch pod's label,
  silently leaving it with no egress at all).
- **`docs/EXPORTING.md`**: the Vertex AI / SageMaker "export surface"
  documentation promised by `README.md`'s non-claims list — what travels
  (the signed container image, API/data contracts, quality evidence) vs.
  what doesn't (K8s manifests, Terraform, Kyverno policies), with worked
  `gcloud ai models upload` and SageMaker Model Package registration steps
  for the *same* image this template's CI already builds and signs.
- `agentic/skills/batch-inference/SKILL.md`: cross-references the new
  `batch-only` overlay for adopters who need scheduled scoring with no
  live API at all (previously the skill only covered adding batch
  scoring *alongside* an existing online Deployment).
- `docs/ADOPTION.md`: two new maturity-matrix rows (batch-only topology,
  export surface); corrected a pre-existing citation (`/onboard` was
  attributed to ADR-035 — it is ADR-029 Wave 3).

---

## [v0.20.0] — 2026-07-01

### Added

- **ADR-033 — Local-first stack profiles**
  (`docs/decisions/ADR-033-local-first-stack-profiles.md`): a `local`
  profile that requires no Docker, K8s, Terraform, or cloud credentials,
  enabling a laptop-only inner loop. Stack profiles are selectable at
  scaffold time via Copier `profile` question and switchable post-scaffold
  via `/stack-switch` workflow.
- **ADR-034 — CCDS-aligned generated layout**
  (`docs/decisions/ADR-034-ccds-aligned-generated-layout.md`):
  documentation-only mapping from the template's production directory
  layout to the Cookiecutter Data Science (CCDS) vocabulary. No directory
  rename — the mapping lives in `templates/service/docs/CCDS_MAPPING.md`.
- **ADR-035 — uv adoption + Copier index publication**
  (`docs/decisions/ADR-035-uv-adoption-copier-index.md`): `uv sync` as
  a first-class install option alongside pip (requirements.txt retained
  as compatibility export). Template is indexable via
  `copier copy https://github.com/DuqueOM/ML-MLOps-Production-Template.git`.
- **D-35 — `local` stack profile accepts cloud credentials or targets a
  cluster**: Anti-pattern forbidding cloud credentials in the `local`
  profile. Enforced by `tests/contract/test_d35_local_profile_no_cloud_deps.py`.
- **Stack profile configs**: `templates/config/stack-profiles/local.yaml`,
  `staging.yaml`, `production.yaml` with `requires.*` and `deploy.enabled`
  flags.
- **Makefile `local-loop` + `switch-profile` targets**: local training →
  serving → drift check loop; profile switching with validation.
- **`stack-switch` skill + `/stack-switch` workflow** (CONSULT mode):
  switch a scaffolded service between stack profiles.
- **`template-onboard` skill + `/onboard` workflow** (AUTO mode):
  interviews the adopter and emits a validated `*_context.local.yaml`
  (gitignored, no secrets).
- **`docs/TUTORIAL.md`**: narrated "from notebook to production" arc
  covering 8 anti-patterns tied to concrete failures each prevents.
- **`templates/service/docs/CCDS_MAPPING.md`**: CCDS → production layout
  mapping table for recognizability.
- **Makefile `install-uv` target**: `uv sync` as faster alternative to
  `pip install -r requirements.txt` (ADR-035).
- **`docs/ADOPTION.md` "Scaffolding & local-first" maturity matrix section**:
  7 new capability rows (Copier scaffolding, copier update, local-first
  profile, stack switching, CCDS mapping, adopter context, uv sync).
- **`docs/PROGRESSION.md` Stage 2 updated**: `copier copy` as primary
  scaffolding command, `uv sync` as recommended install option.

### Changed

- Anti-pattern count: 34 → 35 (D-35 added).
- Agentic surface counts: 19 skills → 20, 15 workflows → 16
  (`template-onboard` + `/onboard` added).
- README comparison table: "Entry friction" lowered from "higher" to
  "medium (local-first profile, `copier copy`)"; "Pedagogy / learning
  arc" updated from "roadmap" to "narrated tutorial + anti-pattern
  walk-through".
- README quick-start: `copier copy` as primary, `new-service.sh` as
  fallback. TUTORIAL.md linked from README and QUICK_START.
- QUICK_START Track B: `copier copy` as primary scaffolding command.
- CONTRIBUTING: `uv sync` option documented; `pyproject.toml` noted as
  source of truth for dependencies.
- `templates/config/agentic_manifest.yaml`: `template-onboard` skill
  (AUTO) and `onboard` workflow (AUTO) added.

### Fixed

- **Documentation coherence drift**: README anti-pattern badge 34→35;
  llms.txt range D-01 to D-34 → D-01 to D-35; CLAUDE.md surface counts
  18 skills / 14 workflows → 20 skills / 16 workflows; AGENT_CONTEXT.md
  D-01..D-34 → D-01..D-35; rule-audit and debug-ml-inference skill
  descriptions updated from D-01..D-34 to D-01..D-35 in canonical and
  generated surfaces.

### Added (documentation coherence system)

- **Documentation Coherence System (rule 16 + ADR-031)**: a single-source-of-truth
  contract for facts restated across documents, enforced like every other
  invariant — a deterministic gate plus an agentic surface that fixes drift.
  - `scripts/check_doc_coherence.py` — blocking gate (sibling of the
    `check_*_drift.py` family): version SSoT, `llms.txt` version, anti-pattern
    count, agentic surface counts, ADR traceability/no-silent-gaps.
  - `agentic/rules/16-doc-coherence.md` (SSoT register + cascade map),
    `agentic/skills/doc-coherence/` (the agent that applies the cascade with
    CONSULT/STOP boundaries), `agentic/workflows/doc-coherence.md` (`/doc-coherence`).
  - CI job `doc-coherence-gate` in `validate-templates.yml` (blocking, seeded green).
- **ADR-031 — Documentation Coherence System**
  (`docs/decisions/ADR-031-documentation-coherence-system.md`): records why the
  contract is composed in-repo (no single external tool spans cross-document
  coherence) and based on Keep a Changelog, towncrier/changesets,
  release-please, MADR/Log4brains, Vale, and Diátaxis patterns.

### Fixed (documentation coherence drift)

- **Documentation coherence drift (audit R7, `docs/audit/AUDIT_R7_STAFF_LEAD.md`)**:
  `VERSION` 0.18.0 → 0.19.0 to match the latest released CHANGELOG heading;
  rewrote a `llms.txt` frozen in the v1.3.0 era (advertised "12 anti-patterns",
  "5 rules", "8 skills") to current reality (D-34, 17 rules, 18 skills, 14
  workflows, v0.19.0); corrected both `CLAUDE.md` files ("32 invariants" → 34,
  added the D-33/D-34 partition row, surface counts 15/16/12 → 17/18/14);
  `Dockerfile` header comment "Python 3.11 slim" → 3.13 to match its base image.
- **Python 3.13 CI coverage (audit R7 F-4)**: `ci-examples.yml` test matrix
  extended `["3.11", "3.12"]` → `["3.11", "3.12", "3.13"]` so the runtime the
  Docker image ships (3.13-slim-bookworm) is actually exercised in CI.

### Added (R7 follow-up)

- **ADR-032 — BentoML as an Optional Alternative Serving Backend** (Proposed)
  (`docs/decisions/ADR-032-bentoml-alternative-serving-backend.md`): records
  the invariant contract (D-01/D-02/D-11/D-23/D-25/D-04) any future BentoML
  backend must satisfy; no code shipped — documentation-only seam per the R7
  audit recommendation ("evaluate, don't mandate").
- **README §"How this compares"**: added a Kubeflow column (full-platform
  reference point) and a "Tools we compose with, not against" subsection
  covering BentoML (→ ADR-032) and Evidently report artifacts.

### Fixed — Wave 2–4 implementation audit (this pass)

An independent Staff/Lead review verified every file the Wave 2–4 ADRs
(033/034/035) claimed to ship, ran the full validator + test suite, and
exercised a real `copier copy` render end-to-end (scaffold → `make deploy`
blocked on `local` → `make onboard` → `make switch-profile PROFILE=staging` →
`make deploy` proceeds). The following defects were found and fixed:

- **Broken `/onboard` schema validation (functional bug)**: `template-onboard`
  SKILL.md / `/onboard` workflow validated their adopter-infra YAML
  (`cloud_provider`, `container_registry`, `mlflow_tracking_uri`, …) against
  `context.schema.json` — the ADR-023 company/project risk-context schema,
  which requires `{version, company}` or `{version, project, kpis}` with
  `additionalProperties: false`. Validation would have failed on every
  invocation. Fixed by adding a dedicated `config/adopter_context.schema.json`
  (+ `config/adopter_context.example.yaml`) and repointing the skill/workflow
  at it. Verified end-to-end against a real scaffold.
- **Missing `templates/service/.gitignore` (never existed, pre-dates Waves
  2-4)**: a scaffolded service had no `.gitignore` at all — `.terraform/`,
  `*.tfstate*`, secrets, model artifacts, and `mlruns/` were all committable,
  and the `/onboard` precondition check (`grep "_context.local.yaml"
  .gitignore`) would always fail. Added a full `.gitignore` (Python, testing,
  venvs, ML artifacts with `.gitkeep`-preserved data dirs, Terraform state,
  secrets, and the ADR-023/029 `*_context.local.yaml` pattern).
- **Unenforced ADR-033 invariant**: ADR-033 §2.5 states the `local` profile's
  `deploy` must be **blocked**, but `make deploy` had no guard. Added a check
  reading `configs/profiles/active_profile.yaml` that refuses to proceed
  when the active profile is `local`. Verified: blocks on `local`, proceeds
  on `staging`.
- **`make deploy` / `scripts/deploy.sh` CLI contract mismatch** (pre-existing,
  surfaced by the end-to-end verification): the Makefile called `deploy.sh`
  with positional args; `deploy.sh` only accepts `--service/--version/--cloud`
  flags. Fixed the Makefile invocation; added `VERSION`/`CLOUD` variables.
- **Missing non-agentic on-ramp for two new workflows**: `/onboard` and
  `/stack-switch` had no `make` target registered (PR-R2-12 violation,
  caught by `test_adoption_boundary_contract.py`). Added `make onboard`,
  mapped `/stack-switch` → the pre-existing `make switch-profile`, and
  documented both in `docs/ADOPTION.md`.
- **Vendored-runtime drift (7 files)**: `stack-switch`/`template-onboard`
  skills and the `onboard`/`stack-switch` workflows were added to canonical
  `agentic/` but never propagated into `templates/service/agentic/`
  (`scripts/check_vendored_runtime_drift.py` was red). Re-synced.
- **Stale anti-pattern range citations left over from the D-35 bump**: the
  weaker-model wave updated most but not all citations when D-35 was added.
  Fixed: `docs/decisions/ADR-014-gap-remediation-plan.md` (`D-01..D-34` →
  `D-01..D-35`, caught by `test_anti_pattern_count_consistency.py`);
  `agentic/skills/rule-audit/SKILL.md` heading (same file already said D-35
  twice elsewhere); `templates/service/docs/ADOPTION.md` (3 instances);
  `docs/TUTORIAL.md` (cited a non-existent test name
  `test_d32_drift_cronjob_module_exists` → corrected to
  `test_d32_drift_cronjob_python_path`); `README.md` D-35 row (cited a
  non-existent path `tests/contract/test_d35_local_profile_no_cloud_deps.py`
  → corrected to the real
  `tests/policy/test_anti_patterns.py::test_d35_local_profile_no_cloud_deps`).
- **Stale ADR-count claims**: `llms.txt` and `docs/ADOPTION.md` /
  `templates/service/docs/ADOPTION.md` said "17 ADRs" / "30 ADR files
  (ADR-001 → ADR-031)"; the repo now has 35 (`ADR-001` → `ADR-035`, `012`
  tombstoned). Corrected all four call-sites.
- **`new-service.sh` had no `--profile` passthrough** despite ADR-033's own
  acceptance criterion ("scaffold with `--profile local`"). Added a 5th
  optional positional arg, validated against `local|staging|prod`, forwarded
  to Copier as `--data profile=$PROFILE`.
- **`new-service.sh` didn't create `.gitkeep` for `data/validated/` or
  `reports/`** even though it created the directories. Fixed.

### Known follow-ons

- `templates/service/docs/ADOPTION.md` is a hand-maintained near-copy of
  `docs/ADOPTION.md` and had drifted in more ways than the citations fixed
  above (it is missing the `doc-coherence` rows this release adds to the
  root copy). Not currently a registered vendored pair, so
  `check_vendored_runtime_drift.py` does not catch this class of drift.
  Tracked for a future pass: either register it as a vendored pair or
  formally scope it as an independent, service-specific document.
- The on-ramps recommended by the R7 audit for batch-only pipelines and a
  Vertex AI / SageMaker "export surface" doc are not part of this release
  (out of scope for the Wave 2–4 verification pass); still open.
- `check_doc_coherence.py` validates anti-pattern counts and agentic surface
  counts but not free-text "N ADRs" claims (the class of drift found in
  `llms.txt`/`ADOPTION.md` this pass) — a candidate C6 check, deferred to
  avoid rushing a repo-wide regex against historical/point-in-time documents
  (CHANGELOG, VALIDATION_LOG, past `ACTION_PLAN_*` audits) that must NOT be
  "corrected".

---

## [v0.19.0] — 2026-06-30

### Added

- **Copier scaffolding migration (Wave 1)**: Replaced manual `cp -r` + `sed`
  scaffolding with [Copier](https://copier.readthedocs.io/) using custom Jinja2
  delimiters `{@ @}` to avoid conflicts with Python f-strings and shell
  variables. `templates/scripts/new-service.sh` is now a thin wrapper around
  `copier copy`. All template files use `{@ service_slug @}`, `{@ service_name @}`,
  `{@ service_kebab @}`, `{@ SERVICE_NAME @}` tokens instead of legacy
  `{service}`, `{ServiceName}`, `{SERVICE}` placeholders.
- **D-33 — Manual file copying or sed-based placeholder substitution in the
  scaffolder**: Anti-pattern forbidding `cp -r` + `sed -i` in favor of
  `copier copy`.
- **D-34 — Unquoted Jinja tokens in YAML lists**: Anti-pattern requiring all
  `{@ @}` tokens in YAML list items to be quoted (`- "{@ service_name @}"`)
  because unquoted `- {@ service_name @}` is invalid YAML.
- **ADR-029 — Agentic Adoption Contract & Interoperability Strategy**
  (`docs/decisions/ADR-029-agentic-adoption-contract.md`): ratifies the
  five-condition gate that routes all industry-adoption improvements (Copier,
  local-first stack profiles, recognizable layout, tutorial) *through* the
  vendor-neutral canonical agentic store (ADR-027) instead of around it.
- **README §"How this compares"**: honest positioning table vs Made With ML,
  Cookiecutter Data Science, and ZenML, with explicit "borrow ideas, never fork"
  framing and a link to the adoption tracker.
- **`docs/audit/ACTION_PLAN_ADAPTABILITY.md`**: living, enterprise-grade tracker
  for the adaptability program (Waves 0–4), including a license/provenance
  guardrail (§1.1) confirming the repo's Apache-2.0 license is unaffected.

### Fixed

- **Copier Jinja2 token handling in tests**: Dynamically construct `{@ @}`
  strings in Python test files to prevent Jinja2 from parsing them as template
  expressions during Copier render. Updated `_normalise_metric_name` in
  `test_metrics_contract.py` to handle Copier tokens in PromQL expressions.
  Updated `test_red_team_regression.py` paths to match new
  `templates/service/` prefixed protected_paths in `ci_autofix_policy.yaml`.
- **tfsec archival workaround**: Pinned tfsec to v1.28.14 (last working release)
  in `.github/workflows/validate-templates.yml` after aquasecurity archived the
  project and `/releases/latest` began returning 404. Long-term migration to
  `trivy config` (tfsec's official successor) tracked in TODO comment.
- **CI/CD template drift**: Synchronized GitHub Actions versions in `templates/cicd/`
  with `.github/workflows/` to resolve drift gate failures on Dependabot PRs:
  - `actions/checkout` v4 → v7 (13 references across 8 template files)
  - `codecov/codecov-action` v4 → v7 (1 reference in `ci.yml`)
  - `bridgecrewio/checkov-action` v12.3105.0 → v12.3107.0 (1 reference in `ci-infra.yml`)
- **Link checker**: Ignore `../SECURITY.md` relative link (returns 400 from
  GitHub link checker).

### Changed

- **Dependency updates** (Dependabot PRs #39, #40, #41):
  - `actions/checkout` v4 → v7
  - `codecov/codecov-action` v4 → v7
  - `bridgecrewio/checkov-action` v12.3105.0 → v12.3107.0

---

## [0.18.0] - 2026-06-10

R6 audit closure ([`docs/audit/ACTION_PLAN_R6.md`](docs/audit/ACTION_PLAN_R6.md)).
Full release notes: [`releases/v0.18.0.md`](releases/v0.18.0.md).

### Added

- **Template-context CI lane** `.github/workflows/template-context-tests.yml` —
  runs the full `templates/service/tests` suite (`-m "not scaffold_context"`)
  on every push/PR; previously NO lane executed it and 8 contract tests were
  silently red on `main` (R6 S0-2). `scaffold_context` marker registered in
  `templates/service/pyproject.toml`.
- **MCP registry**: `docker` (CONSULT — local daemon inspection/builds, never
  registry pushes) and `postgres` (CONSULT — read-only closed-loop SQL) in
  `templates/config/mcp_registry.yaml`; `docs/agentic/mcp-portability.md`
  re-rendered; placeholder-only `.mcp.json.example` for Claude Code project
  scope (live `.mcp.json` gitignored).
- **Surface-loadability validation** in `validate_agentic_manifest.py`:
  claude skill pointers must parse as SKILL.md frontmatter with a non-empty
  description (R6 S2-1).
- **ADR-028 (Accepted)** — LLM-assist integration for maintenance/Day-2 ops;
  accepted as written, including its recommendation *against* fine-tuning
  dedicated models at this scale. Local-model plane realized in the sibling
  repo `agent-local`; unified plan in `docs/audit/ACTION_PLAN_LLM_AGENT.md`.
- `releases/README.md` — explains legacy `v1.x` vs active `v0.x` lines and
  the re-reserved `v1.0.0` L4 gate (R6 S1-3).
- Autouse `os.environ` snapshot/restore fixture in service-test conftest
  (R6 S1-2).

### Changed

- **`.claude/skills/` layout**: flat `<id>.md` pointers → `<id>/SKILL.md`
  directories with frontmatter so Claude Code actually discovers the 16
  skills (R6 S0-1); rendered by `sync_agentic_adapters.py`, validated by
  manifest strict mode; AGENTS.md parity matrix updated.
- `rule-audit` skill: catalogue and frontmatter extended D-27 → D-32
  (R6 S0-3); anti-pattern count consistency test now covers skill bodies.
- AGENTS.md §MCP Integrations: stale `~/.codeium/windsurf/` setup path
  replaced with the per-surface config table (R6 S1-1); docker/postgres rows
  added to the recommended-MCP table.
- `test_networkpolicy_egress_hygiene.py` aligned with the May-2026
  default-deny base contract (every overlay patches egress; dev permissive
  by design); base `networkpolicy.yaml` carries the `OVERLAY-OVERRIDE
  REQUIRED` banner.
- CLAUDE.md: rule count corrected to 15; releases pointer updated (R6 S1-4).

### Fixed

- `docs/observability/dashboards-inventory.md` now lists
  `dashboard-dora.json` (+ panels section) — inventory contract test green.
- `## Known follow-ons` sections backfilled into `releases/v0.16.0.md` and
  `releases/v0.16.1.md`.
- `test_k8s_name_vocabulary.py` no longer false-positives on
  `src/{service}/…` Python module paths in K8s manifests (D-32 vocabulary).
- **Locust/gevent deadlock isolated**: importing locust monkey-patches
  ssl/socket and deadlocks anyio `TestClient` lifespans sharing the process
  (observed as a 55-min hang idle in `gevent/hub.py`, 0 CPU). Crucially the
  patch fires at pytest COLLECTION time, so `-m` deselection is NOT enough —
  exclusion happens at collection: `load_test.py` and
  `test_load_payload_matches_schema.py` are `collect_ignore`d (the latter
  re-enabled via `RUN_LOCUST_PARITY=1` in its own pytest invocation in CI,
  new `locust_parity` marker), and the lane runs `-p no:locust`.
- **`app/main.py` import-order fragility**: `from app.fastapi_app import
  load_model_artifacts` froze the binding at import order, so patches on
  `app.fastapi_app` silently missed `main.py` — `/model/reload` failed only
  when another module imported `app.main` first during collection. `main.py`
  now resolves `load_model_artifacts` / `warm_up_model` / prediction-logger
  hooks through the module at call time (behavior-identical in production).
- **Dual-layout `common_utils` shim** in service-test conftest: in the
  template repo `common_utils` lives at `templates/common_utils` (not on
  the rootdir pythonpath), which collection-errored 52 tests; the shim adds
  `templates/` to `sys.path` only when the package is not already importable
  (scaffolded services keep their vendored copy).

---

## [0.17.0] - 2026-06-08

Vendor-neutral agentic surface (ADR-027) plus the Dependabot GitHub Actions
bumps that landed on `main` after the untagged `0.16.2` working entry. The
agentic refactor is the headline change: it removes the last vendor name
(`.windsurf/`) from the canonical layer so the template survives IDE rebrands
without touching rule/skill/workflow bodies.

### Added

- **`ADR-027` — Vendor-Neutral Canonical Agentic Surface** (`Status: Accepted`).
  Establishes `agentic/{rules,skills,workflows}/` as the single human-authored
  canonical body store and demotes every IDE directory to a *generated*
  surface. Amends `ADR-023` invariant I-4 (canonical source is now `agentic/`,
  not `.windsurf/`).
- **`.devin/` mirror-surface** — a generated, byte-identical copy of
  `agentic/` bodies, because Devin Desktop ingests directory bodies and cannot
  follow pointers. CI enforces byte-parity via
  `scripts/sync_agentic_adapters.py --check`.
- **`.devin_context.md`** context pointer for the Devin surface.
- **Mirror/pointer surface model** in `scripts/validate_agentic_manifest.py`:
  `canonical` (`agentic/`), `mirror` (`.devin/`, full bodies + byte-parity),
  and `pointer` (`.cursor/`, `.claude/`, `.codex/`, thin pointers).

### Changed

- **Canonical agentic store moved `.windsurf/` → `agentic/`** via `git mv`
  (15 rules, 16 skills, 12 workflows). `AGENTS.md` remains the sole behavior
  authority; `agentic/` is the body store; `templates/config/agentic_manifest.yaml`
  is the cross-surface index. This is an **internal/governance** surface — it
  does NOT change the output of `templates/scripts/new-service.sh`, so it is a
  MINOR per `docs/RELEASING.md` §1.2 (new Accepted ADR + backward-compatible
  surface), not a MAJOR.
- **`.cursor/`, `.claude/`, `.codex/`** regenerated as thin pointer-surfaces;
  `.windsurf/` and `.windsurf_context.md` dropped.
- Propagated the canonical-path rename across `surface_capabilities.yaml`,
  `mcp_registry.yaml`, `AGENTS.md`, `.pre-commit-config.yaml`,
  `.github/CODEOWNERS`, `validate-templates.yml`, contract tests, docs, and
  in-code comments.
- **Bump `docker/setup-buildx-action` from `v3` to `v4`** (#29) in
  `.github/workflows/`.
- **Bump `azure/setup-kubectl` from `v4` to `v5`** (#32) in the golden-path
  workflows. Runtime-only action (not mirrored in `templates/cicd/`), so the
  CI/CD Template Drift Gate (ADR-026) is unaffected.
- **Bump `bridgecrewio/checkov-action` from `v12.3102.0` to `v12.3105.0`**
  (#33) in `.github/workflows/validate-templates.yml` **and** mirrored into
  `templates/cicd/ci-infra.yml` to satisfy the drift gate.
- **Bump `gitleaks/gitleaks-action` from `v2` to `v3`** (#34) in
  `.github/workflows/validate-templates.yml` **and** mirrored into
  `templates/cicd/ci.yml`. v3 is a Node 20 → Node 24 runtime migration with no
  input/output/behavior change; `v2` stops working once GitHub removes Node 20
  (2026-09-16), so this keeps scaffolded services on a supported action.

### Fixed

- **Broken relative link in `.github/ISSUE_TEMPLATE/feature_request.md`** —
  `../AGENTS.md` resolved to `.github/AGENTS.md`; corrected to
  `../../AGENTS.md`. Pre-existing bug surfaced by the Link Check once the file
  was edited in this release.

### Known follow-ons

- **`codex/fastapi-template-hardening`** is a separate WIP initiative branched
  from `0.15.2` that still references `.windsurf/` and conflicts on
  `CHANGELOG.md`/`VERSION`. It must be rebased onto post-ADR-027 `main` (with
  the `.windsurf/` → `agentic/` rename applied) before it can ship; it is NOT
  included in this release.
- The untagged `0.16.2` working entry (cosign `v4.1.2` pin + Kyverno schema
  fix + `azure/setup-helm v5`) is retained below for the audit trail; its
  content is already on `main` and is therefore included under the `v0.17.0`
  tag history.

## [0.16.2] - 2026-05-25

CI hardening pass to unblock the open Dependabot PRs (#28
`sigstore/cosign-installer 3.7.0→4.1.2`, #30 `azure/setup-helm 4→5`)
and to fix a latent bug in the supply-chain policy. Three independent
fixes shipped together because all three were blocking the same CI
matrix:

### Changed

- **Bump `sigstore/cosign-installer` from `v3.7.0` to `v4.1.2`** in
  `templates/cicd/{ci,deploy-aws,deploy-gcp,retrain-service}.yml` and
  `.github/workflows/golden-path.yml`. This satisfies the CI/CD
  Template Drift Gate (ADR-026) for the runtime bump landing via
  Dependabot in `.github/workflows/`.
- **Pin `cosign-release: v2.4.0`** on the `templates/cicd/ci.yml`
  installer step. The other workflows were already pinned. Without an
  explicit pin, cosign-installer v4 installs Cosign **v3** by default,
  which is a breaking CLI change (renamed flags, mandatory tlog
  inclusion proofs, OCI registry default changes). The Cosign v2→v3
  migration is deferred to a dedicated ADR; this release adopts only
  the installer's security/maintenance patches.

### Fixed

- **Kyverno image-verification policy schema bug**
  (`templates/k8s/policies/kyverno-image-verification.yaml`).
  Replaced the single `subjectRegExp:` entry with three explicit
  `subject:` entries (one per trusted signing workflow:
  `ci.yml`, `deploy-gcp.yml`, `deploy-aws.yml`). The `subjectRegExp`
  field is rejected by the Kyverno CRD shipped in chart 3.2.6 (strict
  decoding error: `unknown field`), which broke the
  `Kyverno Admission Smoke / Reject :latest in production namespace`
  job on every PR. The new form preserves the exact trust set, is
  Kyverno-version-forward-compatible (uses only documented fields),
  and Kyverno OR's `entries` within a single attestor so semantics
  are identical.

### Why these landed together

`Kyverno Admission Smoke` was failing on `main` before the Dependabot
bumps arrived, so every Dependabot PR inherited a red required check
unrelated to its diff. Bumping `cosign-installer` in templates without
the explicit Cosign release pin would also have broken adopters'
deploy pipelines silently the first time they regenerated from the
scaffolder. Shipping the three fixes in one merge keeps the audit
trail tight: one PR, one CHANGELOG entry, and Dependabot's open PRs
auto-rebase onto a clean `main`.

## [0.16.1] - 2026-05-15

CI/CD template drift gate. Closes a Dependabot blindspot: the
`github-actions` ecosystem only scans `.github/workflows/`, so action
references in `templates/cicd/*.yml` (the scaffolder inputs copied
verbatim into adopter services) age silently after every Dependabot
bump that lands in runtime. This release adds an enforcement script,
fixes the four drifts that already existed, and wires the gate into
`validate-templates.yml`.

### Added

- `scripts/check_cicd_template_drift.py` — fails when any GitHub
  Action used in both `.github/workflows/` and `templates/cicd/`
  has a version in templates that is not present in runtime
- `cicd-template-drift` job in `.github/workflows/validate-templates.yml`
  (runs on every PR; not in required-checks list per ADR-026, same
  posture as `common-utils-drift`)
- `docs/governance/cicd-templates-drift.md` — full rationale,
  enforcement scope, what it does NOT cover, revisit triggers

### Changed

- `templates/cicd/*.yml` — bumped four lagging action references
  to match runtime (`actions/upload-artifact` v4→v7,
  `aquasecurity/tfsec-action` v1.0.0→v1.0.3,
  `bridgecrewio/checkov-action` v12.2752.0→v12.3102.0,
  `sigstore/cosign-installer` floating `v3` → pinned `v3.7.0`)
  across 6 template files

## [0.16.0] - 2026-05-15

SCM governance feature release. Adds branch-protection and tag-immutability
enforcement at the Git-server layer via GitHub Repository Rulesets, with a
canonical configuration doc, ADR with rejected options + revisit triggers,
and an idempotent `gh api` applier wired into three Make targets. No changes
to scaffolded service surface, K8s identifiers, prediction schema, or any
existing template contract — opt-in capability per the CONSULT-class entry
in `AGENTS.md`. Full notes: `releases/v0.16.0.md`.

### Added

- ADR-026 — Branch Protection & Tag Immutability via GitHub Rulesets
  (`docs/decisions/ADR-026-branch-protection.md`)
- Canonical SCM governance config table (`docs/governance/branch-protection.md`)
- Idempotent applier `scripts/setup_branch_protection.sh` with `--dry-run`
  and `--check` modes; no hard `jq` dependency (`jq` → `python3 -m json.tool`
  → `cat` fallback at startup)
- Make targets `setup-github-preview`, `setup-github`, `setup-github-check`
- `AGENTS.md` operations matrix: 2 rows for branch-protection apply/modify
- `scripts/bootstrap.sh` Next Steps step 4 surfaces SCM protection
- `QUICK_START.md` "Protect your fork" section
- `README.md` references ADR-026 in Release-and-operate

### Changed

- `docs/ADOPTION.md` CC7.2 SOC 2 row points at the real enforcement
  artifacts (ADR-026 + `make setup-github`), no longer the prior
  aspirational "branch protection" string
- `scripts/verify_enterprise_adoption.py` tracking constants bumped
  to `RELEASE = "0.15.3"` + new `RELEASE_DATE` constant; still passes

## [0.15.3] - 2026-05-15

ML/Data Scientist template hardening release. This patch aligns the
training path with the EDA and fairness contracts the template already
advertised, without changing the `/predict` API surface or scaffolded
directory layout.

### Changed

- `train.py` now fails closed when
  `quality_gates.require_eda_artifacts=true` unless the full canonical
  EDA packet is present and loadable: `eda_summary.json`,
  `schema_ranges.json`, `baseline_distributions.parquet`,
  `feature_catalog.yaml`, and `leakage_report.json`.
- Training fairness now runs at the same optimal decision threshold
  selected during evaluation, writes `fairness.json`, fails closed for
  missing configured protected attributes, and blocks automatic quality
  gate pass-through when DIR is in the ADR-021 consultation band
  `[0.80, 0.85)`.
- The canonical EDA rule, skill, workflow, README, and ADR references
  now use `baseline_distributions.parquet` and `feature_catalog.yaml`
  as the source-of-truth names. Legacy names remain documented only as
  transition outputs.

### Added

- `templates/service/tests/test_training_fairness_gate.py` — contract
  tests for operational-threshold fairness, missing protected
  attributes, and the DIR consultation band.
- Additional EDA gate coverage proving a partial canonical artifact
  packet fails when EDA artifacts are required.

### Known follow-ons

- Generate service-specific `FeatureEngineer` skeletons from
  `feature_catalog.yaml` once at least one adopter-provided catalog is
  available as a concrete fixture.
- Extend `fairness.json` promotion evidence into model-card rendering
  so subgroup findings are copied automatically into service docs.

---

## [0.15.2] - 2026-05-06

FastAPI template contract hardening release. This does not add a second
serving framework or a parallel API template; it makes the existing
FastAPI scaffold more explicit, easier to review, and harder to drift.

### Added

- `docs/FASTAPI_TEMPLATE_CONTRACT.md` — concise contract for the
  scaffolded serving layer: required endpoints, non-negotiable
  invariants, customization order, and reviewer evidence.
- `templates/service/tests/test_fastapi_template_contract.py` —
  structural contract tests for OpenAPI surface, async
  `run_in_executor` usage, train/inference feature parity,
  readiness gating, auth/admin guards, observability hooks, and
  restricted modelless startup.

### Changed

- `.windsurf/rules/04a-python-serving.md` now treats the generated
  FastAPI scaffold as a first-class contract and documents the current
  `app/main.py` + `app/fastapi_app.py` split.
- Agentic guidance for `new-service`, `debug-ml-inference`, cloud
  deploy smoke tests, `/load-test`, and `/incident` now uses the
  schema-valid scaffold payload and checks `/ready` alongside
  `/health`.
- `scripts/test_scaffold.sh` now includes
  `tests/test_fastapi_template_contract.py` in the full
  `SCAFFOLD_SMOKE=1` pytest chain.
- `README.md` and `QUICK_START.md` now point adopters to the FastAPI
  contract and make the scaffolded serving surface explicit.
- `releases/v0.14.0.md` now uses the canonical
  `## Known follow-ons (scoped, not regressions)` heading, preserving
  release-note consistency.

---

## [0.15.1] - 2026-05-04

Patch release that closes 3 of the 5 pending items from the v0.15.0
audit-remediation entry (VALIDATION_LOG.md Entry 007).

### Added

- `scripts/check_baselines_expiry.py` — enforces that every entry in
  `.security-baselines/{tfsec.yml,checkov.yml,.trivyignore}` carries an
  `# expiry: YYYY-MM-DD` annotation AND that no entry is past due.
  Wired into `validate-templates.yml` as the `security-baseline-expiry`
  job. Closes the v0.15.0 pending item "baseline drift over time".
- `model-verifier` init container in `templates/k8s/base/deployment.yaml`:
  cosign keyless `verify-blob` of `model.joblib` against the workflow
  identity used by `retrain-service.yml`. Two modes:
  `MODEL_SIGNATURE_VERIFY=warn` (base default) and `=true|enforce`
  (production overlays). Closes the v0.15.0 pending item "cosign
  verification at deploy time".

### Changed

- `scripts/test_scaffold.sh` (`SCAFFOLD_SMOKE=1` path) now runs
  `tests/integration/` against the freshly-scaffolded service.
  `test_train_serve_drift_e2e.py` ships in CI. Closes the v0.15.0
  pending item "E2E integration test not executed in CI".
- `templates/k8s/overlays/{gcp,aws}-prod/patch-deployment.yaml`:
  `model-downloader` now also fetches `model.joblib.sig` and `.pem`;
  `model-verifier` patched to `MODEL_SIGNATURE_VERIFY=true` (enforce).
  Production pods that find a missing or tampered model fail at init
  rather than serving an unsigned model.
- `docs/runbooks/deploy-gke.md`: new "Model signature verification
  (init container)" section with the verifier commands, modes, and
  failure-path triage that chains to `secret-breach.md` on
  `no matching signatures`.

### Pending after v0.15.1

- L4 real-cluster execution (gates `v1.0.0`).
- OpenTelemetry tracing under load (adopter-side; opt-in middleware
  ships with the template).

---

## [0.15.0] - 2026-05-04

May 2026 enterprise-audit remediation release. Closes **4 critical,
9 high, 8 medium, and 2 low** findings from the Staff-level audit.
Template posture tightens from "Production-ready by design" to
**"Designed-ready (L1+L2+L3)"** with an honest L4 gap disclosure.

### Added

- `.security-baselines/` directory with `tfsec.yml`, `checkov.yml`,
  `.trivyignore`, and `README.md`. Baselines enforce the flip from
  `soft_fail: true` to hard-fail for supply-chain scans (HIGH-1).
- `templates/common_utils/tracing.py` — opt-in OpenTelemetry FastAPI
  middleware, wired from `app/main.py`; no-op + warning log when OTel
  packages are not installed (MED-6).
- `templates/service/constraints.txt` — optional pip-compile lockfile
  contract for adopters requiring bit-identical builds (MED-5).
- `templates/service/tests/integration/test_train_serve_drift_e2e.py`
  — real end-to-end integration test (LogisticRegression + FastAPI
  TestClient + real PSI) covering the train → serve → drift chain
  without mocks (MED-1).
- Drift `CronJob` init-containers that fetch `reference.csv` and
  `latest.csv` from the cloud bucket into a shared `emptyDir` before
  the detector runs (CRIT-3).
- Cosign blob signing of `model.joblib` + companion `.sig`/`.pem`
  publication to the model bucket in `retrain-service.yml`;
  verification command documented (HIGH-8).
- `Emit audit entry` step on every retrain run (success/failure/halt),
  with model SHA256, archive stamp, and C/C decision in the audit
  entry (HIGH-7).
- PSS-restricted `securityContext`, workload-pool `tolerations`, and
  per-container resource limits on `cronjob-drift.yaml` (CRIT-2).
- `{ORG}/{REPO}` placeholder substitution in `new-service.sh`
  (resolves from CLI args → env vars → `git remote get-url origin`
  → explicit warning) so Kyverno `subjectRegExp` policies render
  with a real GitHub identity, not a literal placeholder (MED-10).
- Maintainership disclosure in `.github/CODEOWNERS`, plus aligned
  notes in `deploy-gcp.yml` and `deploy-aws.yml` on the
  "2 reviewers" production gate being aspirational with a single
  CODEOWNER (HIGH-2).
- Dev overlay NetworkPolicy patch files (`gcp-dev/patch-networkpolicy.yaml`,
  `aws-dev/patch-networkpolicy.yaml`) so the base NetworkPolicy can
  default-deny public egress without breaking dev scaffolding (MED-11).

### Changed

- `templates/k8s/base/kustomization.yaml` now includes
  `slo-prometheusrule.yaml` in `resources:` — previously shipped under
  `base/` but never rendered. SLO burn-rate alerts now ship with every
  scaffolded service (CRIT-1).
- `templates/k8s/base/argo-rollout.yaml` rewritten with full security
  parity to `deployment.yaml`: PSS-restricted pod + container
  `securityContext`, workload-pool tolerations, `DEPLOYMENT_ID` via
  Downward API, `SERVICE_METRIC_PREFIX`, symbolic `cloud-cli-image`
  init container (no more literal `google/cloud-sdk:slim`), probes
  split. The file remains OPT-IN (not in base kustomization) so it
  does not collide with the Deployment — header documents the
  enablement patch (CRIT-4).
- `templates/k8s/base/networkpolicy.yaml` default-deny egress: the
  permissive `0.0.0.0/0` fallback moved from base into the dev
  overlays. Staging/prod overlays now APPEND their narrow CIDR rule
  (previously replaced index 3 which no longer exists). Contract
  test updated (MED-11).
- `.github/workflows/validate-templates.yml`: tfsec, checkov, trivy
  flipped from `soft_fail: true` to hard-fail with baseline config
  files (HIGH-1).
- `templates/service/app/fastapi_app.py`:
  - `ThreadPoolExecutor` size now derived from `INFERENCE_CPU_LIMIT`
    and `os.cpu_count()` with explicit override via
    `INFERENCE_THREADPOOL_WORKERS`; sizing logged at startup
    (MED-2).
  - `ALLOW_MODELLESS_STARTUP=true` is REFUSED in `staging`,
    `production`, and any non-dev `ENVIRONMENT`. Misconfigured
    deploys fail fast instead of serving a synthetic model to real
    traffic (HIGH-6).
  - `/metrics` endpoint carries explicit documentation about its
    NetworkPolicy-based access control (MED-4).
- `templates/service/app/main.py`: `/model/info` protected by
  `verify_api_key` so model type/path/version are not publicly
  fingerprintable when `API_AUTH_ENABLED=true` (MED-3).
- `templates/common_utils/risk_context.py`: Prometheus query now
  requires scheme validation (http/https only), optional Bearer auth
  via `PROMETHEUS_BEARER_TOKEN`, optional custom CA bundle via
  `PROMETHEUS_CA_BUNDLE`, and explicit opt-in for
  `PROMETHEUS_INSECURE_SKIP_VERIFY` (refused outside dev/local)
  (HIGH-9).
- `templates/service/pyproject.toml` version bumped from `1.0.0` down
  to `0.1.0` to match the `v0.x` template posture; scaffolded
  services own their own version (MED-8).
- `examples/minimal/serve.py` now implements the canonical warm-up +
  `/ready` gating pattern (D-23) with CPU-aware ThreadPool sizing,
  aligning the example with the full template (MED-9).
- README "Production-ready by design" wording replaced with "Designed-
  ready (L1+L2+L3)" across the maturity matrix. Numeric self-rating
  table removed. Memory Plane and CI self-healing demoted from hero
  capabilities to "Roadmap — Phase 1 contracts only" (HIGH-3/4/5).

### Security

- Baselines in `.security-baselines/` document accepted findings with
  rationale + expiry. Every exception is reviewable in a PR; the path
  to zero-baseline is explicit.
- Model artifacts now carry a cosign signature chain verifiable via
  Rekor; bucket compromise no longer silently substitutes a model.
- Dynamic-risk protocol queries Prometheus over authenticated TLS;
  an attacker cannot downgrade the agent's risk posture by
  misconfiguring the signal source (the fallthrough always escalates,
  never relaxes).

### Fixed

- Drift detection `CronJob` no longer crash-loops in PSS-restricted
  namespaces — missing `securityContext` caused admission rejection,
  the `DriftDetectionHeartbeatMissing` alert fired forever, and
  operators chased a phantom "drift pipeline broken" incident instead
  of the real "the manifest was always broken" cause (CRIT-2/3).
- Staging/prod NetworkPolicy patches no longer reference the removed
  index `/spec/egress/3`; they now APPEND their narrow rule using
  `op: add /spec/egress/-`.
- Deploy workflow comments no longer imply a 2-reviewer gate that
  the single-CODEOWNER setup cannot actually satisfy (HIGH-2).

### Documentation

- `README.md` §"Production-ready scope" completely rewritten with
  the L1/L2/L3/L4 layer matrix and an honest L4-gap disclosure.
- `README.md` §"Recent hardening" replaces the numeric self-rating
  with per-release, file-level evidence against the most recent
  audit.
- Runbooks expanded: `docs/runbooks/rollback.md`,
  `docs/runbooks/deploy-gke.md`, `docs/runbooks/deploy-aws.md`,
  `docs/runbooks/secret-breach.md` — each now includes trigger
  criteria, pre-flight, procedure, verification table, audit + comms,
  exit criteria, failure paths, and anti-patterns (LOW-5).
- `docs/decisions/ADR-024-audit-may-2026-remediation.md` — ADR
  recording the 23-finding remediation, the move from
  "Production-ready by design" to "Designed-ready", and the
  baseline-to-zero roadmap.
- `VALIDATION_LOG.md` Entry 005 records the per-file evidence for
  every CRIT/HIGH fix in this release.

---

## [0.14.0] - 2026-05-03

Enterprise adoption remediation release. This closes the highest-impact
gaps from the Staff-level audit without changing the template's public
positioning: production-ready by design, pre-GA until real cloud L4
evidence is captured.

### Added

- Runbooks referenced by the non-agentic adoption guide and scaffolded
  Makefile: drift detection, model retrain, release checklist, rollback,
  incident response, performance review, cost review, secret breach,
  inference debugging, performance RCA, concept drift analysis, and
  GKE/AWS deploy procedures.
- Enterprise adoption verifier in `scripts/verify_enterprise_adoption.py`
  covering runbook links, release documentation, scaffolded CI/CD layout,
  and train/inference parity contract text.

### Changed

- Generated CI now treats the scaffolded service as a single-service repo
  rooted at `app/`, `src/`, `requirements.txt`, and `Dockerfile`.
- GCP/AWS deploy workflows now build from repo root and publish
  `{service-name}-predictor` images, matching the Kustomize overlay
  vocabulary and digest-pinning reusable workflow.
- Python packaging now discovers packages under `src/`; pytest also gets
  `pythonpath = [".", "src"]` for scaffolded test ergonomics.
- Template pre-commit versions now match the root quality chain.
- README now records the post-remediation score delta and explicitly keeps
  the L4 cloud rollout caveat.

### Fixed

- First scaffold CI no longer points at a non-existent `${ServiceName}/`
  subdirectory.
- Deploy image names no longer mix PascalCase service names with
  kebab-case Kubernetes image names.
- API inference now loads the training `FeatureEngineer` and applies
  `transform_inference()` before prediction unless an operator explicitly
  opts out with `FEATURE_ENGINEERING_REQUIRED=false`.
- The skipped train/inference feature parity test is now an executable
  contract in scaffolded services.
- D-01..D-32 documentation references are reconciled in README,
  adoption docs, and policy workflow summaries.

### Known follow-ons

- Real GKE/EKS cloud golden-path execution remains the release gate for
  future `v1.0.0`.
- Adopter-specific egress allowlists and cloud credentials still require
  environment-local validation.

## [0.13.0] - 2026-05-03

Pre-GA hardening release. This release deliberately moves the public
signal back to a `v0.x` channel while preserving historical `v1.x` tags.
The template remains production-oriented, but GA enterprise readiness is
reserved for the first verified cloud golden path.

### Added

- `scripts/sync_agentic_adapters.py` renders Cursor, Claude, and Codex
  as thin manifest-driven adapter pointers instead of hand-maintained
  rule/skill/workflow forks.
- Codex parity now covers the full canonical set: 15 rule files, 16
  skills, and 12 workflows through `.codex/{rules,skills,workflows}/`.
- `make agentic-sync` and targeted CI verification now check adapter
  drift with `scripts/sync_agentic_adapters.py --check`.
- Root Day-2 operations runbook index in `docs/runbooks/day-2-operations.md`.
- Strict CI verifier scripts for YAML, workflows, and targeted policy
  gates used by the autofix policy.

### Changed

- Agentic manifest surfaces now include `codex` for all canonical rules,
  skills, and workflows.
- `AGENTS.md`, `docs/ide-parity-audit.md`, `.codex/README.md`, and
  ADR-023 now describe the enterprise adapter pattern: `AGENTS.md` as
  authority, `.windsurf/` as canonical body store, YAML as index, and
  generated pointers as IDE adapters.
- Golden-path workflows are stricter: scaffold smoke must fail on
  skipped install/test/snapshot paths, and deploy smoke requires real
  `/health`, `/ready`, `/predict`, and metrics evidence.
- Quality gates can require EDA artifacts before training/promotion.
- GCP/AWS infra defaults were tightened around private endpoints,
  node OAuth scopes, state-lock naming, artifact IAM, and bucket-scoped
  runtime identities.
- Codex MCP example includes the optional Playwright MCP entry.

### Fixed

- Missing CI autofix verifier commands are now real scripts.
- `mcp_doctor.py` validates workflow MCP capabilities, not only skills.
- `AGENT_CONTEXT.md` stale training path and ADR references were corrected.

### Known follow-ons

- Terraform MCP uses Docker; this workstation could not pull
  `hashicorp/terraform-mcp-server:latest` because the Docker daemon was
  not running.
- Real cloud GKE/EKS E2E validation remains the release gate for
  future `v1.0.0`.

---

## [1.12.0] - 2026-04-29

Closes 4 external-audit Round-3 findings and hardens pre-commit as the first filter. Root cause of the R3 findings:
contributor clones shipped without actually installing git hooks, so black / flake8 drift, a closed-loop workflow with a
payload that didn't match the live schema, and a kebab-vs-snake path bug in the drift CronJob all reached CI as the last
line of defense. This release makes the first filter non-optional.

### Breaking for adopters (post-R4 audit re-classification)

Under the versioning policy ratified in `docs/RELEASING.md` (introduced post-tag in response to R4 finding C3), the
following items in this release WOULD have required a MAJOR bump. Tag `v1.12.0` is immutable; this block documents the
contract change so adopters can migrate explicitly. See `MIGRATION.md` for the `v1.11 → v1.12` row.

- **Closed-loop schema realignment** (HIGH-1, PR-R2-9): the `golden-path-extended.yml` workflow now POSTs `entity_id` +
  `feature_a/b/c` + `slice_values` to `/predict`, replacing the previous `feature_1/2/3` payload that returned 422
  against the live schema. Adopters who copied or extended that workflow MUST update their payload to the canonical
  schema. Metric fallback also changed from `requests_total{endpoint="/predict"}` to `requests_total{status=~"2xx|4xx"}`
  because the counter only carries a `status` label.
- **Drift CronJob Python path** (HIGH-2, D-32): scaffolded manifest now uses
  `src/{service}/monitoring/drift_detection.py` (snake) instead of `src/{service-name}/...` (kebab). Adopters whose
  CronJob applied cleanly but exploded with `ModuleNotFoundError` at runtime must redeploy after re-scaffolding or apply
  the snake-case path manually.
- **Pre-commit hook contract**: hook count went 9 → 14 with `default_install_hook_types: [pre-commit, pre-push]`.
  Adopters who maintained custom `.pre-commit-config.yaml` overlays MUST run `pre-commit install --overwrite` (now
  wrapped in `scripts/dev-setup.sh` and `make verify-hooks`).

### HIGH — closed (pre-commit + audit R3)

- **HIGH-1 (PR-R2-9)** Stage 1 of `golden-path-extended.yml` was posting `feature_1/2/3` against a live schema that
  required `entity_id` + `feature_a/b/c` — every "valid" request was 422'd. The metric fallback
  `requests_total{endpoint="/predict"}` matched NOTHING because the counter only has a `status` label (see
  `fastapi_app.py:176`). Fixed: payload uses the canonical schema fields including `slice_values`; fallback awk pattern
  matches against `status="2xx|4xx"` which actually exists. NEW `test_closed_loop_workflow_contract.py` (3 tests) parses
  both sides and fails LOUD if workflow and schema drift.
- **HIGH-2 (D-32)** Drift `CronJob` referenced `src/{service-name}/monitoring/drift_detection.py` (kebab) but the
  scaffolder renames `src/{service}` → `src/<snake_slug>`. Manifest applied cleanly, then exploded at runtime with
  `ModuleNotFoundError: fraud-detector is not a valid Python package name`. Fixed: manifest now uses `{service}`
  (snake). NEW **D-32** entry in `AGENTS.md` formalizes the rule with inline rationale. NEW
  `test_d32_drift_cronjob_python_path` regression test (placeholder leak guard + snake-case check + on-disk directory
  existence + `drift_detection.py` existence).

### Pre-commit as mandatory first filter

Discovered this clone had ZERO hooks in `.git/hooks/` — which is why this session shipped 5 commits with black drift and
F541 caught ONLY by CI. The fix makes hooks non-optional.

- `default_install_hook_types: [pre-commit, pre-push]` in `.pre-commit-config.yaml` so a single `pre-commit install`
  covers both stages (previously `--hook-type pre-push` had to be passed separately and almost nobody did — the
  scaffold-smoke pre-push hook silently never ran).
- NEW `scripts/dev-setup.sh` — idempotent bootstrap: installs pre-commit if missing, validates config, installs both
  stages with `--overwrite`, verifies hooks actually landed in `.git/hooks/`, runs `pre-commit run --all-files` as
  sanity check. Fails LOUD with the fix command.
- NEW `make verify-hooks` target — fails non-zero if either hook is missing or doesn't reference the pre-commit framework.
- NEW hooks (ported from the portfolio): **mypy 1.13** narrowed to `common_utils/` + `examples/` + `scripts/` (template
  service territory is placeholder land and explodes mypy), **bandit 1.7.10** with `-ll -i` threshold.
- NEW LOCAL hooks: **validate-agentic** (runs `scripts/validate_agentic.py --strict` when AGENTS.md or agent runtime
  config changes) and **ci-autofix-policy-contract** (runs the 10-invariant contract test from ADR-019 when policy YAMLs
  change). These are the project's OWN gates; contributors no longer wait for CI to discover drift.

Result: **14 hooks** (was 9), all 14 green on `main`.

### LOW — release hygiene

- `releases/v.1.11.0.md` → `releases/v1.11.0.md` (`git mv` preserves history). The previous filename broke alphabetical
  sorting between v1.10 and v1.11.

### MEDIUM — catalog reconciliation

- README said "30 production anti-patterns" while AGENTS table ended at D-31 and code/tests already referenced D-32
  without a canonical definition. Fixed: `D-01..D-32` across `README.md`, `AGENTS.md` (4 callsites reconciled), and D-32
  formalized in the anti-pattern table.

### CI hardening

- **CI parity for local hooks**: the two new local hooks need pytest/pyyaml/jsonschema in the lint lane. Added to the
  `python-quality` job so CI matches the same set of hooks as local pre-commit.
- **ci-autofix-policy-contract** uses `--noconftest --rootdir=.` so pytest doesn't pick up the service's numpy-heavy
  conftest (unnecessary for a self-contained contract test). Fast + self-contained is the whole point.
- **mypy fix** in `risk_context.py:301`: removed a redundant `ctx: RiskContext` annotation that re-declared a
  cache-unpacked variable. `[no-redef]` was the only mypy error blocking the new hook from going green on the existing
  tree.
- **bandit fix** in `risk_context.py:215`: explicit `# nosec B310` on the Prometheus urlopen with a 3-line comment
  explaining the URL comes from deployment config, not request input.

### Known follow-ons (scoped, not regressions)

- MEDIUM legacy-service migration guide for adopters that need to temporarily set `require_eda_artifacts=false`.
- MEDIUM Windows CI matrix for `validate_agentic.py` (new lane; script currently works on Linux/WSL/macOS).
- MEDIUM NetworkPolicy egress allowlist by cloud (per-overlay work).
- MEDIUM `load_test.py` schema sync to `feature_a/b/c` (low-risk but clean review as separate PR).
- ADR-018/019 runtime implementation remains staged: Phase 1 shadow/read-only capabilities are shipped; write-enabled
  runtime remains deferred pending shadow precision evidence.

---

## [1.11.0] - 2026-04-28

Closes the ADR-016 external-audit R2 remediation backlog (7-day, 30-day, and 90-day windows all materially shipped) and
lays the policy foundation for two new agent capabilities (Operational Memory Plane, Agentic CI Self-Healing). Also
closes the OSS-packaging gap (NOTICE, DCO, CODEOWNERS) and tightens cloud parity through an additional 33 contract
tests.

### Breaking for adopters (post-R4 audit re-classification)

Under the versioning policy in `docs/RELEASING.md`, the following items in this release WOULD have required a MAJOR
bump. Tag `v1.11.0` is immutable; this block documents the contract change explicitly.

- **GCP IAM split surface**: `gcp/iam.tf` introduced per-service identity bindings that change the IAM model. Adopters
  with existing GCP deployments must apply the `terraform plan` carefully and confirm no privilege downgrade for
  in-flight workloads.
- **`templates/config/ci_autofix_policy.yaml` and `model_routing_policy.yaml` introduced**: these become the canonical
  source of truth for autofix routing. Adopters who previously relied on undocumented defaults must opt in by enabling
  the (Phase 0) policy contract tests; no runtime behavior change yet.
- **New `make` targets in non-agentic on-ramp**: 12 targets added (`scaffold`, `validate`, `deploy-dev`, etc.). Adopters
  who maintained custom Makefiles must merge the new targets per `docs/ADOPTION.md`.

### ADR-016 R2 remediation — closed

- **PR-R2-1..R2-5** (7-day window): shipped earlier in 1.10.0.
- **PR-R2-6** AWS parity: storage, registry, IAM, secrets, logging — shipped.
- **PR-R2-7** Quality-gate config externalized per service — shipped.
- **PR-R2-8** EDA artifacts as machine-readable contract — shipped (delivered as part of PR-B2 stages 1–2).
- **PR-R2-9 Stage 1** Closed-loop verification — NEW `.github/workflows/golden-path-extended.yml` that re-scaffolds +
  deploys + posts 100 valid + 5 invalid `/predict` requests + asserts the prediction-log counter increments. Triggers on
  `workflow_run` after the base Golden Path E2E succeeds, plus weekly schedule and on-demand. PR-R2-9b (alert firing via
  Prometheus + Pushgateway) is the explicit follow-on.
- **PR-R2-10** Reproducible drift + degraded-deploy drills — shipped.
- **PR-R2-11** D-01..D-31 anti-patterns as policy tests over scaffolded output — NEW `templates/service/tests/policy/`
  (13 tests) + dedicated weekly workflow. The suite scaffolds a fresh service per session and asserts AGENTS.md
  invariants hold on the rendered output. Surfaces a documentation drift in `aws/iam.tf` on first run; fixed in same PR.
- **PR-R2-12** Adoption-boundary doc + non-agentic on-ramp — NEW `docs/ADOPTION.md` (maturity matrix per cloud ×
  environment + non-claims list) + 12 new `make` targets so teams can adopt the template without inheriting the agentic
  surface + `test_adoption_boundary_contract.py` (7 tests) enforcing parity workflow ↔ make target ↔ doc.

### Cloud parity (AWS ↔ GCP)

- **GCP gets** secrets.tf, logging.tf, kms.tf at the live layer with bootstrap-tier KMS key separation. Mirrors the AWS
  surface introduced in 1.10.0.
- **NEW** `test_terraform_cloud_parity.py` (14 tests) enforces semantic parity: same secret-store usage, logging
  retention, budget alert wiring, CMEK across both clouds.
- **GCP IAM** parity rationale documented inline in `gcp/iam.tf`: GCP doesn't need an `iam-roles-split.tf` equivalent
  because per-service identities don't exist on GCP — Workload Identity bindings per-secret/per-bucket already partition
  responsibilities.
- **Cluster defaults** (PR-A3): private endpoint configurable and secure by default, system/workload node pool split
  with taint, deny-default `NetworkPolicy`. Enforced by `test_cluster_defaults_contract.py`.
- **Bootstrap split**: state bucket, KMS, registry tiers separated from live layer per ADR. Enforced by
  `test_terraform_bootstrap_contract.py` (~7 tests).

### NEW agent capabilities (Phase 0 only — runtime deferred)

- **ADR-018 Operational Memory Plane** ratifies a typed retrieval layer over existing evidence (audit.jsonl, drift
  reports, postmortems, security findings) that feeds new dynamic risk signals to `risk_context.py`. Hard boundaries
  codified: NOT in `/predict` path, NOT authoritative, NOT a policy mutator (memory hits can only ESCALATE prudence,
  never demote STOP). Phase plan with 7 phases; either phase auto-withdraws if the next phase doesn't ship in 30 days.
- **ADR-019 Agentic CI Self-Healing** ratifies the policy contract for bounded autofix on CI failures. Ships TWO
  governance YAMLs (`templates/config/ci_autofix_policy.yaml`, `templates/config/model_routing_policy.yaml`) + ONE
  contract test (10 invariants) + the canonical failure-class table (12 classes mapped AUTO/CONSULT/STOP). Runtime
  scripts deferred — policy first, scripts second.

### Model routing recommendation

- New README §"Recommended baseline (verified 2026-04)" subsection adds a provider × tier table mirroring
  `model_routing_policy.yaml` plus three pre-tuned profiles. Includes an explicit honesty caveat: vendor model names
  rotate every 6–12 months; the `verified_at` field declares when the catalog was last reconciled; the contract test
  enforces structure (preview never lands on protected branches), not specific identities.

### OSS packaging

- **NEW** `NOTICE` (Apache-2.0 attribution).
- **NEW** `DCO.md` (explicit DCO policy — already used in commits but undocumented).
- **NEW** `.github/CODEOWNERS` routes review for protected areas (AGENTS.md, agent runtime config, ADRs,
  CICD/k8s/infra/scripts, common_utils, service template, monitoring, governance files).
- Existing `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md` retained — already detailed and aligned.

### CI hardening

- **CI tfsec fix**: replaced unsupported Terraform `check` block with `terraform_data` + `lifecycle.precondition` for
  AWS subnet validation (tfsec v1.28.x parser limitation).
- **Policy-tests workflow numpy isolation**: added `--rootdir` + `--confcutdir` so the policy suite doesn't trigger the
  parent ML conftest's numerics imports. Workflow stays cheap (~30s install vs ~5min full ML stack).
- **black drift fix**: 5 test files reformatted; local + CI now agree.
- **F541 fix**: removed an f-prefix on a string with no placeholders.

### Test count

Contract tests: **76 PASS** (14 parity, 7 bootstrap, 11 cluster defaults, 7 adoption boundary, 10 CI policy, 18 day-2
artifacts, 9 IAM least-privilege).
Policy tests over scaffolded output: **13 PASS + 3 SKIP** (~4 min wall-clock per run).

### Known follow-ons (scoped, not regressions)

- **PR-R2-9b** alert-firing via Prometheus + Pushgateway (Stage 2 of PR-R2-9).
- **ADR-018 Phases 1–6** (canonical contracts, ingestion, storage/retrieval, integration,
  shadow→advisory→guarded-gate→enforced, hardening).
- **ADR-019 Phases 1–6** (context collection, classification, verifier helpers, workflow scaffold, AUTO enablement per
  failure class, CONSULT lane).

---

## [1.10.0] - 2026-04-26

Closes a 15-finding template audit. The audit caught a class of bugs
where the template LOOKED finished — green CI, scaffolder passes —
but the deploy chain, supply-chain trust chain, and governance gates
had silent gaps a real production user would discover only at the
worst time. This release is mostly fixes; there is little new
surface area but the existing surface now does what the docs say.

### Breaking for adopters (post-R4 audit re-classification)

Under the versioning policy in `docs/RELEASING.md`, this release SHOULD have been `v2.0.0` rather than `v1.10.0`. The R4
audit (finding C3) explicitly flagged it. Tag `v1.10.0` is immutable; this block makes the contract change visible. See
`MIGRATION.md` for the `v1.9 → v1.10` row, which is the highest-impact migration in the project's history.

- **Six environment overlays renamed**: `gcp-production` → `gcp-prod`; `aws-production` → `aws-prod`; new `gcp-dev`,
  `gcp-staging`, `aws-dev`, `aws-staging` introduced. Adopters MUST update every reference in custom CI, deploy scripts,
  and `kubectl` invocations. Pre-`v1.10` deploys for dev/staging never worked; adopters who thought they had dev deploys
  did not.
- **Cosign signing path now wired end-to-end**: prior versions advertised image signing but did NOT install `cosign` in
  any workflow. From `v1.10.0` onward, every prod build signs and attests; adopters with existing Kyverno admission
  policies in audit mode must move to enforce mode AFTER confirming all images carry signatures and SBOMs.
- **Image digest pinning is now mandatory**: `kustomize edit set image <name>=<repo>@<digest>` runs BEFORE every
  `kubectl apply`. Adopters who deployed by tag (e.g. `:latest`, `:1.0`) MUST switch to digest references; mutable tags
  are no longer supported by the deploy chain.
- **Init-container model loading**: `templates/k8s/base/deployment.yaml` introduces an init-container with an `emptyDir`
  volume to download model artifacts at runtime. Adopters who baked artifacts into images (D-11) MUST migrate to the
  init pattern.
- **Pod Security Standards labels mandatory**: each overlay now carries the correct PSS labels (`enforce=baseline` for
  dev/staging, `enforce=restricted` for prod). Adopters with custom namespaces MUST add the labels or admission control
  will reject the pods.

### Critical fixes

- **Six environment overlays** (`templates/k8s/overlays/`):
  `gcp-{dev,staging,prod}` and `aws-{dev,staging,prod}`. The
  deploy workflows referenced these names; the repo only shipped
  two misnamed dirs (`gcp-production`, `aws-production`). Every
  dev/staging deploy was broken. Each overlay carries its own
  `namespace.yaml` with PSS labels (D-29: `enforce=baseline` for
  dev/staging with `warn/audit=restricted`; `enforce=restricted`
  for prod) and env-tier resources (1×100m for dev, 1×250m for
  staging, 2×500m for prod).
- **Image digest pinning end-to-end**:
  `deploy-{gcp,aws}.yml` build job captures `sha256:...` via
  `docker buildx imagetools inspect` and emits a JSON map.
  `deploy-common.yml` consumes it and runs `kustomize edit set
  image <name>=<repo>@<digest>` BEFORE `kubectl apply`. Cosign
  signs and attests by digest. The Kyverno digest gate that was
  installed in v1.6.0 finally has compliant manifests to admit.
- **Root pytest stops crashing on Prometheus metric placeholders**:
  `templates/service/app/fastapi_app.py` previously declared
  `Counter("{service}_predictions_total", ...)`. `prometheus_client`
  rejects metric names containing `{` / `}`, so root-level coverage
  collection produced 11 errors before any test ran. Metrics now
  use `f"{os.getenv('SERVICE_METRIC_PREFIX', 'ml_service')}_..."`;
  `deployment.yaml` sets the env var to the scaffolded service name
  so production metrics are unchanged.

### High fixes

- **`common_utils/__init__.py` lazy re-exports** so
  `from common_utils.agent_context import AuditLog` works on a
  CI runner without joblib installed. The audit step in
  `deploy-common.yml` (introduced v1.8.0) was failing to write
  evidence on lightweight runners — the "obligatory" audit trail
  was actually fragile.
- **`AWS_ROLE_ARN` declared in `deploy-common.yml`'s
  `workflow_call.secrets`**. The reusable workflow read
  `secrets.AWS_ROLE_ARN` while declaring `secrets: {}` — invalid
  contract that blocked AWS callers at validation time.
- **Cosign installed in `deploy-{gcp,aws}.yml`**. The build job
  ran `cosign sign` and `cosign attest` without ever installing
  cosign on `ubuntu-latest`. Signing and attestation never worked
  before this release.
- **Pod Security Standards namespace labels** included in every
  overlay (was a separate file in `templates/k8s/policies/` that
  no overlay consumed). D-29 is now actually enforced.
- **`SecurityAuditResult` blocks HIGH findings**, not just
  CRITICAL. The dataclass derived `passed` from `trivy_critical
  == 0` only — a service with 5 HIGH and 0 CRITICAL passed the
  gate even though the `security-audit` skill said both block.
  The dataclass and the policy doc are now consistent.
- **Per-env Terraform state**. Backends are partial config:
  `backend "gcs" {}` / `backend "s3" {}` with `backend-configs/
  {env}.hcl` files passed at init time. Three envs × two clouds
  = six bucket/lock-table pairs, fully isolated. New runbook
  `docs/runbooks/terraform-state-bootstrap.md` documents the
  bootstrap.
- **Drift detection + retraining pipelines operationalized**.
  Workflows previously had `# TODO: Configure data download` +
  `echo "TODO: Upload model"` placeholders. Now use
  parametrized `{DATA,MODEL}_BUCKET_KIND` env vars to pick
  `gcs` vs `s3` and FAIL loudly on missing config instead of
  succeeding with a no-op. Champion/Challenger has a real
  champion to compare against; promotion uploads two copies
  (current + timestamped archive) per cloud.

### Medium fixes

- **`Makefile validate-tf` paths corrected** from
  `templates/infra/{gcp,aws}` to
  `templates/infra/terraform/{gcp,aws}`. `terraform init` now
  uses `-backend=false` so validate works without backend buckets.
- **`ci-examples.yml` installs `pyarrow` and `pytest-asyncio`**.
  Tests that exercised Parquet (prediction logger) or async
  client paths errored at import time and pytest counted them
  as "errors" not "failures" — green-yellow signal that hid
  missing coverage.
- **`docs/environment-promotion.md` purged of `GCP_SA_KEY`**
  references. The deploy chain has been WIF-only since v1.6.0;
  the doc was telling new contributors to provision a static
  service account key in defiance of D-18.
- **Demo fairness threshold documented as exception**, not a
  contradiction. `examples/minimal/train.py` keeps
  `FAIRNESS_THRESHOLD = 0.70` (synthetic data is too small to
  reliably hit 0.80 on a 1600-sample fold) but now has an 8-line
  comment explaining the policy baseline is 0.80 and only this
  demo runs laxer.
- **Smoke test runs in the right namespace**. `kubectl run smoke...`
  no longer lands in `default` and call services via FQDN
  (`<svc>-service.<ns>.svc.cluster.local`). With the new
  `{service}-{env}` overlay namespaces, the previous short-DNS
  form would always have failed.

### New runbooks

- `docs/runbooks/aws-irsa-setup.md` — AWS counterpart to
  the existing GCP WIF runbook. Covers GitHub OIDC trust + IRSA
  pod identity. Symmetric setup steps; cross-linked from
  AGENTS.md and environment-promotion.md.
- `docs/runbooks/terraform-state-bootstrap.md` — per-env state
  bucket bootstrap for GCP and AWS. Required reading before the
  first `terraform init` of any env.

### Verification

```bash
# All 6 overlays render
for o in gcp-dev gcp-staging gcp-prod aws-dev aws-staging aws-prod; do
  kustomize build templates/k8s/overlays/$o > /dev/null && echo "✓ $o"
done

# Cosign installed in BOTH deploy workflows
grep -c sigstore/cosign-installer templates/cicd/deploy-{gcp,aws}.yml

# Digest pinning step present
grep -c 'kustomize edit set image' templates/cicd/deploy-common.yml

# Test that previously crashed now passes
PYTHONPATH=templates/service python -c "from app import fastapi_app; print('ok')"

# Audit record runs without ML deps
PYTHONPATH=templates python -c "from common_utils.agent_context import AuditLog; print('ok')"

# Validator stays green strict
python scripts/validate_agentic.py --strict
```

### Total commits

9d8894e → b8708b6 (5 fix commits + this changelog entry)

---

## [1.9.0] - 2026-04-24

### Added

- **Batch inference skill** — `.windsurf/skills/batch-inference/SKILL.md`
  scaffolds CronJob-based scoring that reuses the exact same
  `predictor.predict_batch()` function and Pandera schema as the live
  API; includes PSS restricted container, `concurrencyPolicy: Forbid`,
  and `activeDeadlineSeconds` hard cap
- **ADR-013 — GitOps strategy** — codifies the current
  `kubectl apply` posture and the four revisit triggers for
  migrating to ArgoCD
- **DORA metrics exporter** — `templates/scripts/dora_metrics.py`
  aggregates deployment_frequency, lead_time_for_changes,
  change_failure_rate, and mttr from the GitHub REST API +
  `ops/audit.jsonl`. Writes `ops/dora/{YYYY-MM}-metrics.json`.
  Graceful degradation without `GITHUB_TOKEN`. 9 unit tests
- **Devcontainer** — `.devcontainer/devcontainer.json` +
  `post-create.sh` give contributors a reproducible environment
  matching the CI runner (Python 3.11 bookworm + docker-in-docker +
  kubectl/helm + terraform + cosign + conftest + syft + gitleaks)
- **Secret rotation runbook** — `docs/runbooks/secret-rotation.md`
  covers SCHEDULED rotation (complement to `secret-breach-response`
  which handles emergencies): per-credential cadence table, STOP per
  env, 7-day soak on OLD version, quarterly calendar

### Scope note

v1.9.0 was planned as a 10-item roadmap. Delivered the 5 highest-
impact items; deferred D2 (GPU), D5 (more reusable GHA), D6
(Terraform tests), D8 (template repo publish) to future releases.

### Total test count

- Unit tests: **127 passing** (was 118 in v1.8.1)

---

## [1.8.1] - 2026-04-24

### Added

- **Pod Security Standards** (D-29) — `templates/k8s/policies/pod-security-standards.yaml`
  with Namespace definitions per environment; base `deployment.yaml`
  now ships pod + container `securityContext` compatible with PSS
  `restricted` (`runAsNonRoot`, `capabilities.drop: [ALL]`,
  `seccompProfile: RuntimeDefault`). Rule 02 §Pod Security Standards
- **SBOM attestation** (D-30) — `deploy-gcp.yml` + `deploy-aws.yml`
  generate a CycloneDX SBOM via Syft and attach it as a Cosign
  attestation. Full SLSA L3 provenance via `slsa-github-generator`
  documented as ROADMAP template block
- **ThreadPoolExecutor sizing** — `docs/threadpool-sizing.md` operator
  guide + `templates/service/scripts/benchmark_executor.py` executable
  sweep script (writes `ops/benchmarks/{ts}-executor.json`)
- **Input quality** — `common_utils/input_quality.py` opt-in checker
  against training-time `[p01, p99]` quantiles. Emits
  `{service}_input_out_of_range_total` labels without blocking the
  request. 14 unit tests
- **Closed-loop Grafana dashboard** —
  `templates/monitoring/grafana/dashboard-closed-loop.json` (10 panels:
  SLO availability + burn, per-version AUC, sliced-AUC heatmap, C/C
  error rate, score-distribution p50, logger errors, input-quality
  flags, monitor heartbeat, PSI top 10)
- **Intersectional fairness** — `fairness.py::compute_intersectional_fairness()`
  evaluates all 2-way combinations of protected attributes. New
  parameters on `run_fairness_audit(intersectional=False,
  min_intersectional_samples=30)`. 4 unit tests
- **Anti-patterns D-29, D-30** in AGENTS.md

### Changed

- `templates/k8s/base/deployment.yaml` — pod/container securityContext;
  init container resource requests/limits
- `templates/cicd/deploy-gcp.yml` + `deploy-aws.yml` — SBOM generation
  and attestation steps
- `templates/service/src/{service}/fairness.py` —
  `run_fairness_audit(intersectional=..., min_intersectional_samples=...)`
  parameters; `_summary.intersectional_evaluated` flag; `_intersectional`
  report block
- `.windsurf/rules/02-kubernetes.md` — new Pod Security Standards section

### Total test count

- Unit tests: **118 passing** (was 100 in v1.8.0)

---

## [1.8.0] - 2026-04-24

### Added

- **AuditLog** — thread-safe append-only JSONL writer in
  `common_utils/agent_context.py` for `ops/audit.jsonl`. Integrates with
  `RiskContext` via `record_operation()` to automatically persist the
  five ADR-010 signals plus `base_mode` whenever dynamic escalation
  changed the operation's mode
- **AuditEntry hardening** — now validates `result ∈ {success, failure,
  halted}` and requires an `approver` for CONSULT/STOP success entries
  (human-accountability invariant)
- **Skill `rule-audit`** — READ-ONLY automated compliance scanner
  against AGENTS.md anti-patterns D-01..D-28. Per-invariant query,
  evidence-backed findings, `--subset` scoping, AuditLog integration
- **Skill `performance-degradation-rca`** — multi-stream RCA
  correlating sliced metrics, drift, deploys, upstream data, and
  logger health. Produces R1..R5 root-cause classification and
  `docs/incidents/{date}-{service}.md` blameless RCA template
- **Rule 14 `14-api-contracts.md`** — API contract versioning policy:
  committed `openapi.snapshot.json`, semver table for schema evolution,
  CI guard enforcing version bump alongside snapshot changes
- **`templates/service/tests/contract/`** — scaffolded contract-test
  layout: `test_openapi_snapshot.py` (3 tests) + `openapi.snapshot.json`
  (regenerated via `scripts/refresh_contract.py`)
- **`templates/service/scripts/refresh_contract.py`** — operator
  regeneration script (executable)
- **Anti-pattern D-28** in AGENTS.md — breaking API change without
  version bump + snapshot update
- **25 unit tests** in `test_agent_context.py` covering every typed
  handoff + AuditLog semantics

### Changed

- `common_utils/agent_context.py`: `AuditEntry` gains `risk_signals:
  list[str]` and `base_mode: AgentMode | None` fields (omitted from
  JSONL when None)

### Total test count

- Unit tests: **100 passing** (was 75 in v1.7.1)

---

## [1.7.1] - 2026-04-24

### Added

- **Model warm-up** (`warm_up_model` in `fastapi_app.py`) forces a dummy
  predict and builds the SHAP `KernelExplainer` once during lifespan,
  before `_warmed_up=True`. Cached on app state (D-24)
- **Probe split** — `livenessProbe: /health` (always 200 while alive),
  `readinessProbe: /ready` (503 until warmed), `startupProbe: /health`
  with `failureThreshold: 24` to absorb cold start (D-23)
- **Graceful shutdown** — `terminationGracePeriodSeconds: 30` coordinated
  with uvicorn `--timeout-graceful-shutdown=20` (D-25)
- **PodDisruptionBudget** (`k8s/base/pdb.yaml`) with `minAvailable: 1`
  and HPA `minReplicas: 2` (D-27)
- **Champion/Challenger Argo Rollouts** — two AnalysisTemplates:
  `{service}-cc-online` (4 proxy metrics during canary, auto-rollback)
  and `{service}-cc-post-deploy` (3 business metrics from performance_monitor,
  human-gated rollback). Closes G-02b
- **Rollback skill + /rollback workflow** — STOP-class emergency revert
  procedure: Argo Rollouts abort+undo, MLflow registry revert, alert
  silencing, audit issue. Closes G-05
- **Environment promotion chain** — dev→staging→prod with GitHub
  Environment Protection Rules. Reusable `deploy-common.yml` workflow;
  `deploy-gcp.yml` and `deploy-aws.yml` rewritten as 4-job chains.
  `docs/environment-promotion.md` operator setup guide (ADR-011, D-26)
- **Dynamic Behavior Protocol** — `common_utils/risk_context.py` with
  19 unit tests. Reads `mcp-prometheus` for 5 live signals
  (incident_active, drift_severe, error_budget_exhausted, off_hours,
  recent_rollback); escalates AUTO→CONSULT or CONSULT→STOP per
  ADR-010. Fallback to `ops/*.json` files keeps template usable
  without the MCP
- **mcp-prometheus promoted to CORE MCP** in AGENTS.md §MCP Integrations
- **ADR-010** — Dynamic Behavior Protocol via mcp-prometheus
- **ADR-011** — Environment Promotion Gates (dev→staging→prod)
- **Anti-patterns D-23..D-27** added to AGENTS.md table with corrective
  actions

### Changed

- `templates/tests/infra/policies/kubernetes.rego` converted from Rego
  v0 (legacy `deny[msg] { }`) to Rego v1 (`deny contains msg if { }`).
  Pre-existing technical debt — current versions of conftest reject the
  old syntax. Content preserved; only syntax updated.
- `.windsurf/rules/01-mlops-conventions.md` — invariants list grows to
  6 (adds warm-up/probe-split); new Dynamic Behavior Protocol section
- `.windsurf/rules/02-kubernetes.md` — new sections for probe split,
  graceful shutdown, PodDisruptionBudget
- `.windsurf/rules/05-github-actions.md` — Environment Promotion Gates
  (D-26) and Reusable Workflows sections
- HPA `minReplicas: 1 → 2` (required for PDB `minAvailable: 1` to be
  non-trivially effective)
- Rollout's canary `analysis.templates` references the new
  `{service}-cc-online` template in dedicated file (previously inline)

### Fixed

- `templates/tests/infra/policies/kubernetes.rego` now parses with
  current conftest/OPA (Rego v1 strict mode)
- Argo Rollout canary no longer serves traffic to pods that have passed
  `/health` but have not finished warming their SHAP explainer

### Total test count

- Unit tests: **75 passing** (was 56 in v1.7.0)

---

## [1.7.0] - 2026-04-23

### Added

#### Closed-Loop Monitoring — Ground Truth + Sliced Performance + Champion/Challenger

Closes the largest remaining gap in the template: concept drift went silent
because the system only tracked feature distributions (PSI). This release
wires predictions to their eventual ground-truth labels, computes sliced
performance metrics, and gates promotion on statistical superiority.

See **ADR-006** (closed-loop monitoring), **ADR-007** (sliced analysis),
**ADR-008** (champion/challenger), and **ADR-009** (retraining orchestration
triggers) for the full rationale.

**Prediction logger (ADR-006):**

- `templates/common_utils/prediction_logger.py` — async buffered logger with
  4 pluggable backends (parquet, BigQuery, SQLite, stdout) via
  `PREDICTION_LOG_BACKEND` env var
- `PredictionEvent` frozen dataclass validates `prediction_id`,
  `entity_id`, and `model_version` at construction (invariant D-20)
- Fire-and-forget semantics: handler never blocks on log I/O (D-21)
- Failure-tolerant: flush errors swallowed + counted via
  `prediction_log_errors_total` (D-22)
- Integrated into `fastapi_app.py` and `main.py` lifespan; gracefully
  degrades if backend fails to start

**Ground truth ingestion (ADR-006):**

- `templates/service/src/{service}/monitoring/ground_truth.py` — daily
  CronJob with user-implemented `fetch_labels_from_source()` contract
- Ships CSV stub for local dev + documented examples for BigQuery, Postgres
- Writes idempotent daily parquet partitions (`year=/month=/day=`)
- `configs/ground_truth_source.yaml` — declarative source config

**Sliced performance monitor (ADR-007):**

- `templates/service/src/{service}/monitoring/performance_monitor.py` —
  JOINs predictions with labels on `entity_id` with causality constraint
  (`label_ts >= prediction_ts`), computes AUC/F1/precision/recall/Brier
  globally AND per slice
- Baseline comparison for concept drift (`auc_drop_warning/alert`)
- Tri-state status: `ok` / `warning` / `alert` / `insufficient_data`
- Prometheus Pushgateway metrics with labels
  `{slice_name, slice_value, metric}` for Grafana filtering
- `configs/slices.yaml` — bounded-cardinality slice declarations
  (country, channel, model_version, score_bucket examples)

**K8s manifests:**

- `k8s/base/cronjob-performance.yaml` — two CronJobs:
  - `{service}-ground-truth-ingester` at 03:00 UTC
  - `{service}-performance-monitor` at 04:00 UTC
- `k8s/base/performance-prometheusrule.yaml` — 5 alerts:
  - `GlobalAUCBelowAlert` (concept drift)
  - `SlicedAUCBelowAlert` (subpopulation degradation)
  - `F1BelowAlert` (threshold calibration)
  - `PerformanceMonitorStale` (heartbeat)
  - `PredictionLogErrorsHigh` (D-22 degradation visibility)

**Champion/Challenger statistical gate (ADR-008):**

- `templates/service/src/{service}/evaluation/champion_challenger.py` —
  McNemar exact binomial test + bootstrap ΔAUC 95% CI combined into
  tri-state decision (promote / keep / block)
- `configs/champion_challenger.yaml` — alpha, n_bootstrap,
  non_inferiority_margin, superiority_margin
- `cicd/retrain-service.yml` — new C/C gate between quality gates and
  promotion; posts decision to Actions step summary; opens issue on
  keep/block
- Exit codes: 0 (promote) / 1 (keep) / 2 (block)

**Anti-patterns (D-20, D-21, D-22):**

- D-20 — prediction log events without `prediction_id` / `entity_id`
- D-21 — prediction logging blocking the async inference event loop
- D-22 — logging backend failure propagating to the HTTP response
- Added to `AGENTS.md` anti-pattern table (now D-01 → D-22)

**Agentic system:**

- `.windsurf/rules/13-closed-loop-monitoring.md` — invariants + slicing /
  ground-truth / C/C contracts + agent behavior by file (AUTO/CONSULT/STOP)
- `.windsurf/skills/concept-drift-analysis/` — new skill with RCA decision
  tree (global vs sliced, drift vs labels vs noise)
- `.windsurf/skills/drift-detection/` — extended to cover BOTH data drift
  (PSI) AND concept drift (sliced performance)
- `.windsurf/skills/model-retrain/` — new Step 5.5 for C/C statistical gate
- `.windsurf/workflows/performance-review.md` — monthly review workflow
  with multi-window metric collection + degrading-slice detection

**IDE parity:**

- `.cursor/rules/08-closed-loop.mdc` — parity with Windsurf rule 13
- `.claude/rules/08-closed-loop.md` — parity with Windsurf rule 13

**ADRs:**

- `docs/decisions/ADR-006-closed-loop-monitoring.md`
- `docs/decisions/ADR-007-sliced-performance-analysis.md`
- `docs/decisions/ADR-008-champion-challenger-statistical-gate.md`
- `docs/decisions/ADR-009-retraining-orchestration-triggers.md` —
  documents measurable triggers for migrating retraining beyond GHA;
  explicitly rejects premature Argo Workflows adoption

**Tests (50 total passing, 25 new):**

- `templates/tests/unit/test_prediction_logger.py` — 20 tests covering
  `PredictionEvent` invariants, 3 backends, D-21/D-22 contract
- `templates/tests/unit/test_ground_truth.py` — 6 tests for LabelRecord
  invariants and CSV stub
- `templates/tests/unit/test_performance_monitor.py` — 14 tests for
  metrics, slicing, thresholds, full pipeline with sliced subpopulations
- `templates/tests/unit/test_champion_challenger.py` — 10 tests for
  McNemar, bootstrap CI, decide() logic, end-to-end sklearn comparison

**Schema changes (BREAKING for services on v1.6.x):**

- `PredictionRequest.entity_id` is now REQUIRED (`min_length=1`)
- `PredictionResponse.prediction_id` is now REQUIRED (UUID hex)
- Optional: `PredictionRequest.slice_values: dict[str, str]` for sliced
  monitoring

**Dependencies:**

- `pyarrow ~=18.0` — parquet backend for prediction_logger
- `pyyaml ~=6.0` — config loaders

**Scope respected:**

- No Argo Workflows (ADR-009 documents triggers; GHA remains default)
- No Bytewax / streaming (parquet batch covers target audience)
- No ClickHouse default (parquet is default; BigQuery optional; ClickHouse
  mentioned as future trigger at >100M predictions/day)
- No Istio shadow mode (ADR-008 future-work section)
- ADR-001 scope honored throughout

---

## [1.6.0] - 2026-04-23

### Added

#### Agent Behavior Protocol + Supply Chain Security

Closes two latent gaps: agents now know **when to pause and ask**, and the supply
chain has first-class controls (Cosign signing + SBOM + admission policy). See
**ADR-005** for full rationale.

**Agent Behavior Protocol (3 modes):**

- `AGENTS.md` — new **Agent Behavior Protocol** section with AUTO / CONSULT / STOP modes
- **Operation → Mode mapping table** (21 operations, canonical)
- **Escalation triggers** — automatic STOP even from AUTO/CONSULT (marginal fairness,
  drift PSI > 2× threshold, cost > 1.2× budget, credential detected, etc.)
- Structured mode transition signal format for handoffs

**Authorization checkpoints in skills:**

- `.windsurf/skills/deploy-gke/SKILL.md` — `authorization_mode` frontmatter + protocol section (dev=AUTO,
  staging=CONSULT, prod=STOP)
- `.windsurf/skills/deploy-aws/SKILL.md` — same pattern
- `.windsurf/skills/model-retrain/SKILL.md` — train=AUTO, to_staging=CONSULT, to_production=STOP + automatic STOP on
  D-06 / marginal fairness / regression > 5%

**New Layer 2 agent: Agent-SecurityAuditor**

- Runs **before** Agent-DockerBuilder and Agent-K8sBuilder
- Blocks pipeline on findings (never silent)
- Chains to `/secret-breach` on secret leaks

**Agent Permissions Matrix** — capability boundaries per agent × environment.
"Blocked" entries cannot be bypassed by human insistence.

**Agent Handoff Schema** — typed dataclass contracts replacing ad-hoc dicts:

- `templates/common_utils/agent_context.py` — `AgentMode`, `Environment`,
  `EDAHandoff`, `TrainingArtifact`, `BuildArtifact`, `SecurityAuditResult`,
  `DeploymentRequest`, `AuditEntry`
- All `frozen=True`, validate invariants at construction (fail-fast)
- `DeploymentRequest` refuses to construct if `env=production` + `audit.passed=False`

**Audit Trail Protocol:**

- Every agentic operation → `ops/audit.jsonl` (append-only)
- Mirrored to GitHub Actions step summary
- CONSULT/STOP operations additionally open a GitHub issue tagged `audit`
- Failures open an issue tagged `audit` + `incident`

#### Supply Chain Security (SLSA L2 components)

**New anti-patterns D-17 / D-18 / D-19:**

- D-17: Hardcoded credentials / direct `os.environ` for secrets in prod
- D-18: Static AWS keys or GCP JSON keys in production
- D-19: Unsigned images or missing SBOM in production

**`.windsurf/rules/12-security-secrets.md` (NEW, `always_on`):**

- Non-negotiable invariants D-17/D-18/D-19
- Pre-commit gitleaks + credential-pattern grep
- Python module guidance: `common_utils.secrets.get_secret`, never log values
- K8s: `envFrom.secretRef`, IRSA/WI annotations, image digests in staging/prod
- Terraform: secret manager data sources, no literals
- Environment separation table (local / ci / staging / prod)
- Explicitly documents what it does NOT cover (Vault, SLSA L3+, compliance — per ADR-001)

**`templates/common_utils/secrets.py` (NEW):**

- Cloud-native secret loader with environment-aware resolution
- Backends: dotenv (local), `os.environ` (CI), AWS Secrets Manager, GCP Secret Manager
- **Refuses to fall through to `os.environ` in staging/production** (D-18)
- Never logs secret values (D-17)

**`templates/cicd/ci.yml` updates:**

- New `security-audit` job: gitleaks + credential-pattern grep + IRSA/WI enforcement
- `build` job renamed to "Build, Sign & Attest":
  - Syft SBOM generation (CycloneDX + SPDX) with 90-day retention
  - Cosign keyless signing via GitHub OIDC (commented until registry wired)
  - `cosign attest` for SBOM as CycloneDX attestation
  - `permissions.id-token: write` for keyless signing
  - Build provenance summary in GHA step summary

**`templates/k8s/policies/kyverno-image-verification.yaml` (NEW):**

- ClusterPolicy `verify-image-signatures` — reject unsigned images in
  `environment=production` namespaces
- Keyless Cosign: GitHub OIDC identity + Rekor transparency log
- Requires CycloneDX SBOM attestation (max 90 days old)
- Companion ClusterPolicy `require-image-digest` — forbids tag-only refs in staging/prod

**Incident response:**

- `.windsurf/skills/security-audit/SKILL.md` (NEW) — pre-build/pre-deploy scans
- `.windsurf/skills/secret-breach-response/SKILL.md` (NEW) — 7-phase playbook
  (halt → classify → revoke → audit → rotate → clean history → notify → post-mortem)
- `.windsurf/workflows/secret-breach.md` (NEW, `/secret-breach` slash command)

**Documentation:**

- `docs/decisions/ADR-005-agent-behavior-and-security.md` (NEW)
  - Why 3 modes (not binary)
  - Why keyless Cosign (not keypair)
  - Why Kyverno (not OPA Gatekeeper)
  - Why refuse `os.environ` in prod
  - Why not Vault (ADR-001 deferred)
  - Why JSONL audit log (not GitHub issues per op)
  - Why dataclasses (not JSON Schema)
  - 4 alternatives considered + rejected
  - Revisit triggers

### Changed

- `AGENTS.md`: new sections (Behavior Protocol, Handoff Schema, Audit Trail, Permissions Matrix)
- Agent list in Layer 2 now includes Agent-SecurityAuditor
- Skills inventory adds `security-audit`, `secret-breach-response`
- Workflow inventory adds `/secret-breach`
- Cross-references table adds: pre-build/pre-deploy, secret-leak-detected

### Smoke tests

- Handoff dataclasses enforce invariants at construction:
  - `TrainingArtifact.requires_consult()` returns True for marginal fairness (0.80–0.85) or metric > 0.99
  - `SecurityAuditResult` raises `ValueError` if `passed` flag disagrees with component fields
  - `DeploymentRequest` raises `ValueError` on production + failed audit

### The consultative gap is now closed

```text
Before:  Agent executes all the way to kubectl apply — human sees only results.
After:   Agent emits [AGENT MODE: CONSULT] before staging apply, [AGENT MODE: STOP]
         before production apply, presents plan, waits for explicit approval.
```

### The supply chain gap is now closed

```text
Before:  Trivy scan → push → deploy (no signature, no SBOM, no admission gate)
After:   Trivy + Gitleaks → SBOM (CycloneDX + SPDX) → Cosign sign (keyless OIDC)
         → Cosign attest SBOM → push → Kyverno admission verifies at cluster entry
```

---

## [1.5.0] - 2026-04-23

### Added

#### EDA Phase Integration (closes data-to-training gap)

The template now has a first-class Exploratory Data Analysis phase that connects
raw data → trained model through 6 structured phases with 4 agentic invariants.

**Agentic configuration:**

- **`AGENTS.md`**: new Agent-EDAProfiler (Layer 2); anti-patterns D-13 through D-16;
  updated skill/workflow inventories with `eda-analysis` and `/eda`
- **`.windsurf/rules/11-data-eda.md`**: enforces snake_case, sandbox isolation,
  baseline persistence, structural layout. Glob: `**/eda/**`, `**/notebooks/**/*.ipynb`
- **`.windsurf/skills/eda-analysis/SKILL.md`**: 6-phase procedure with hard gate on
  phase 4 (leakage detection) — chains to `/incident` on block
- **`.windsurf/workflows/eda.md`**: `/eda` slash command; chains to `/new-service`
  on pass or `/incident` on leakage block

**Template module `templates/eda/`:**

- **`eda_pipeline.py`** (500 lines): scriptable pipeline
  - Phase 0: ingest + snake_case normalization (D-13 sandbox check)
  - Phase 1: structural profile → `01_dtypes_map.json`
  - Phase 2: univariate + **`02_baseline_distributions.pkl`** (D-15) with
    quantile bins for PSI compatibility (D-08)
  - Phase 3: correlations + feature ranking
  - Phase 4: leakage detection HARD GATE (exit 1 if `BLOCKED_FEATURES` non-empty)
  - Phase 5: feature proposals with rationale (D-16)
  - Phase 6: `schema_proposal.py` with observed ranges (D-14) + summary markdown
- **`notebook_template.ipynb`**: interactive companion (13 cells, one per phase)
- **`requirements.txt`**: lightweight mode (~50MB, pandas + scipy + pandera)
- **`requirements-heavy.txt`**: opt-in ydata-profiling + plotly (~500MB)
- **`README.md`**: conventions, phase artifacts reference, drift loop diagram

**Anti-patterns D-13 to D-16:**

- D-13: EDA on production data without sandbox
- D-14: Pandera schema without observed ranges from EDA
- D-15: Baseline distributions not persisted (silently breaks drift detection)
- D-16: Feature engineering without documented rationale

**Integration:**

- `new-service.sh` now copies `eda/` to scaffolded services + creates
  `reports/`, `artifacts/`, `notebooks/` subdirs
- Updated scaffolder next-steps walk users through EDA before `schemas.py`/`features.py`
- `test_scaffold.sh` validates 5 new `eda/` paths exist in scaffolded output
- `make eda-validate` target (syntax + `py_compile`); chained into `make validate-templates`

**Documentation:**

- **`docs/decisions/ADR-004-eda-phase-integration.md`**: documents the design,
  rationale for 6 phases (not fewer), hard gate on leakage, lightweight vs heavy
  modes, and why `schemas.py` is never auto-overwritten

**Validation:**
Tested end-to-end against `examples/minimal` fraud data (400 rows × 6 cols): all
6 phases pass in <1s, `baseline_distributions.pkl` produced with quantile bins,
leakage gate correctly PASSED, 3 transforms proposed each with rationale.

**The drift detection loop now closes:**

```text
EDA phase 2 → 02_baseline_distributions.pkl (DVC-tracked)
           → Drift CronJob (production, consumes the pkl)
           → PSI per feature using quantile bins (D-08)
           → Alert if PSI > threshold → /drift-check → /retrain
```

---

## [1.4.0] - 2026-04-19

### Added

#### One-Command Bootstrap

- **`scripts/bootstrap.sh`** — Detects OS (Linux/macOS/WSL), verifies required tools (Python 3.11+, Docker, kubectl,
  terraform, git, make), installs Python dependencies, configures MCPs interactively, installs pre-commit hooks, and
  validates by running the minimal example end-to-end. Idempotent; supports `--skip-mcp`, `--skip-demo`, `--check-only`.
- **`scripts/_lib/detect_os.sh`** — OS detection helper
- **`scripts/_lib/install_deps.sh`** — Python + system dependency installer
- **`scripts/_lib/configure_mcp.sh`** — Interactive MCP configuration (github, git, kubectl-mcp-server, terraform-mcp-server)
- **Makefile targets**: `make bootstrap`, `make bootstrap-check`

#### Agentic System Validator

- **`scripts/validate_agentic.py`** — Validates `.windsurf/` structure:
  - Rule frontmatter (`trigger`, `description`, `globs`)
  - Glob patterns match real files (catches dead rules)
  - Skill `SKILL.md` contracts (`name`, `description`, `allowed-tools`)
  - Workflow frontmatter
  - AGENTS.md cross-references (no orphan skills/workflows)
- **CI job**: `agentic-system` in `validate-templates.yml` runs on every PR
- **Makefile target**: `make validate-agentic` (chained into `make validate-templates`)

#### Governance Module (opt-in)

- **`templates/governance/README.md`** — When/how to enable approval gates
- **`templates/governance/ROLES.md`** — ML Engineer / Tech Lead / Platform Engineer responsibilities
- **`templates/governance/github-environments.yml`** — GitHub Environments configuration reference (staging + production
  with `required_reviewers` and 24h soak)
- **`templates/governance/promote-with-approval.yml`** — GitHub Actions workflow for Staging → Production promotion with
  MLflow stage transitions and audit tags
- **`templates/governance/promote_to_stage.sh`** — CLI for MLflow Model Registry stage transitions with audit trail
- **`docs/decisions/ADR-002-model-promotion-governance.md`** — Documents why governance is opt-in, why GitHub
  Environments + MLflow stages over custom infrastructure, and how it respects ADR-001

#### Scaffolder End-to-End Test

- **`scripts/test_scaffold.sh`** — Runs `new-service.sh` in an isolated temp dir and validates:
  - Zero remaining `{ServiceName}`/`{service}`/`{SERVICE}` placeholders
  - All critical files and directories present
  - `src/{service}/` renamed correctly to `src/<slug>/`
  - All generated Python files parse (syntax check)
  - Both Kustomize overlays render (GCP + AWS)
  - `pytest` can collect scaffolded tests
- **CI job**: `scaffold-e2e` in `validate-templates.yml` runs on every PR
- **Makefile target**: `make test-scaffold` (also chained into `make validate-templates`)

#### Feast Integration Pattern

- **`docs/decisions/ADR-003-feast-integration-pattern.md`** — Documents the pattern for
  integrating Feast without modifying the core template. Uses external feature repo
  approach; service becomes a Feast client. Preserves Pandera validation (solves a
  different problem). Migration checklist (4 phases) and invariants maintained.

### Changed

#### Makefile (root)

- `validate-templates` now includes `validate-agentic` and `test-scaffold` steps
- Added `bootstrap`, `bootstrap-check`, `validate-agentic`, `test-scaffold` as first-class targets

---

## [1.3.0] - 2026-04-16

### Added

#### Standalone Documentation (root)

- **`QUICK_START.md`** — 10-minute setup guide: Option A (example demo), Option B (scaffold service), Option C (full
  MLflow stack)
- **`RUNBOOK.md`** — Template operations reference: scaffolding, validation, MLflow, contributing, release process
- **`LICENSE`** — MIT License (was referenced in README but file was missing)
- **`docker-compose.yml`** — Local dev stack: example fraud detection API + MLflow (one command: `docker compose up`)
- **`releases/`** — GitHub Release notes directory: `v1.0.0.md`, `v1.1.0.md`, `v1.2.0.md` ready to publish

#### DVC Templates (new)

- **`templates/service/dvc.yaml`** — DVC pipeline with 4 stages: validate → featurize → train → evaluate
- **`templates/service/.dvc/config`** — DVC remote configuration template for GCS/S3 storage

#### Infrastructure (from portfolio)

- **`templates/infra/docker-compose.mlflow.yml`** — Production-like MLflow stack: PostgreSQL + MinIO (S3-compatible) +
  MLflow server with health checks

#### Documentation Templates (new)

- **`templates/docs/CHECKLIST_RELEASE.md`** — Pre-deployment release checklist: quality gates, Docker, K8s, infra,
  monitoring, multi-cloud
- **`templates/docs/mkdocs.yml`** — MkDocs Material configuration template with navigation, plugins, theme, and
  docstring support

#### Integration Test Templates (new)

- **`templates/tests/integration/conftest.py`** — Service health wait fixture, auto-skip if unavailable
- **`templates/tests/integration/test_service_integration.py`** — Full service validation: health, predictions, SHAP,
  latency SLA, metrics, model info

#### Enterprise K8s & Security (new)

- **`templates/tests/infra/policies/kubernetes.rego`** — OPA/Conftest policies (ported from portfolio): non-root,
  resource limits, health probes, no :latest, namespace, HPA scaleDown + ML-specific D-01/D-02 enforcement
- **`templates/k8s/base/slo-prometheusrule.yaml`** — SLO/SLA definitions as PrometheusRule:
  - Availability SLI (99.5% non-5xx), Latency SLI (95% < 500ms)
  - Error budget recording rules (30-day window)
  - Multi-window burn rate alerts: P1 (14.4x/1h), P2 (6x/6h), P3 (budget < 25%)

#### Service Template Additions

- **`templates/service/codecov.yml`** — Codecov configuration template with per-service coverage flags

#### Example Improvements

- **`examples/minimal/Dockerfile`** — Docker image for the fraud detection example (used by root docker-compose.yml)

#### Architecture Decision Records

- **`docs/decisions/ADR-001-template-scope-boundaries.md`** — Documents why LLM/GenAI, multi-tenancy, Vault, feature
  store, data contracts, SOC2/GDPR, and audit logs are deferred. Includes revisit triggers and Engineering Calibration
  rationale.

#### CI: End-to-End Example Proof

- **`validate-templates.yml`** — New `example-e2e` job: install → train → verify artifacts → start server → run tests →
  drift check → verify quality gates. Proves the template works in CI, not just locally.

### Changed

#### README — Major Restructure

- **Concise hook at top** — Problem statement + differentiator in 3 lines, replacing verbose intro
- **Quick Navigation** — Replaced bullet list with 3-column table (Getting Started | Architecture | Development)
- **Quick Start** — Removed manual `sed -i` commands, now uses `new-service.sh` exclusively (fixes inconsistency with
  CHANGELOG v1.1.0)
- **"Try It in 5 Minutes"** — Added `make demo-minimal` one-liner and Docker Compose alternative
- **Repository Structure** — Updated tree with all new files: QUICK_START.md, RUNBOOK.md, LICENSE, docker-compose.yml,
  releases/, DVC, integration tests, SLO, mkdocs, checklist, MLflow compose
- **Templates Detail** — Added sections for DVC, integration tests, SLO, MLflow, release checklist, MkDocs
- **MkDocs section** — Now references `templates/docs/mkdocs.yml` template instead of just the portfolio
- Added links to QUICK_START.md and RUNBOOK.md at top of README

#### AGENTS.md

- Updated Template System tree with DVC, pyproject.toml, integration tests, SLO, MLflow compose, mkdocs, checklist

#### CLAUDE.md

- Updated File Structure with all new files and directories

#### `new-service.sh`

- Added DVC template copying step
- Added integration test template copying
- Added `data/validated/`, `data/processed/`, `reports/` to standard directories

#### Fairness Module

- **`templates/service/src/{service}/fairness.py`** — Added domain guidance: protected attribute selection by industry
  (Finance, Healthcare, Employment, GDPR), threshold customization, DIR limitations, proxy detection references

#### common_utils Distribution Strategy

- **`templates/common_utils/__init__.py`** — Documented the copy-in pattern, trade-offs, and PyPI graduation path (>5 services)

#### README: Claude Code & Cursor Rules

- **Agentic System section** — Added dedicated subsections for `.claude/rules/` (5 rules, `paths:` triggers) and
  `.cursor/rules/` (5 MDC rules, `globs:` triggers) with per-file tables

#### RUNBOOK: Secret Management

- **`RUNBOOK.md`** — Added Secret Management section: GCP/AWS Secrets Manager commands, anti-pattern D-10 guidance

### Notes

#### Claude-code-main Assessment

- Evaluated `/home/duque_om/projects/Claude-code-main` — TypeScript CLI rebuild of Claude Code, **no reusable content**
  for this MLOps template

#### Enterprise Gap Assessment

- **Already present**: RBAC (`rbac.yaml`), NetworkPolicy, Workload Identity/IRSA, SHAP `/predict?explain=true`,
  JSONFormatter, Prometheus/Grafana (9 panels), Pandera (3 validation points), Makefile x2, MLflow+DVC, Codecov badge
  (dynamic), `make demo-minimal`
- **Added in v1.3.0**: SLO/SLA PrometheusRule, ADR-001, e2e CI job, fairness domain guidance, Claude/Cursor docs
- **Deferred by design** (ADR-001): LLM/GenAI, multi-tenancy, HashiCorp Vault, feature store, SOC2/GDPR — documented
  with revisit triggers

---

## [1.2.0] - 2026-04-15

### Added

#### Developer Experience (root DX files)

- **`Makefile`** (root) — Contributor entry point with template-specific targets:
  - `make validate-templates` — lint + K8s validation in one command
  - `make lint-all` / `make format-all` — operate on all Python across `templates/` and `examples/`
  - `make demo-minimal` — run fraud detection example end-to-end (install → train → test → drift)
  - `make test-examples` — regression tests for examples/
  - `make new-service NAME=X SLUG=y` — scaffold wrapper around `new-service.sh`
- **`.pre-commit-config.yaml`** (root) — Contributor hooks: black, isort, flake8, `pre-commit-hooks` (yaml, merge
  conflicts, large files), gitleaks
- **`.gitleaks.toml`** (root) — Secret detection config shared between root and `templates/`, with allowlists for
  template placeholder tokens (`{ServiceName}`, `{service}`)

#### Multi-IDE Cursor Parity

- **`.cursor/rules/02-kubernetes.mdc`** — K8s rules: 1 worker, CPU HPA, init container pattern with code example
- **`.cursor/rules/03-python-serving.mdc`** — Serving rules: async inference, SHAP KernelExplainer, Prometheus metrics
- **`.cursor/rules/04-python-training.mdc`** — Training rules: pipeline sequence, quality gate table, required tests
- **`.cursor/rules/05-docker.mdc`** — Docker rules: multi-stage, non-root USER, HEALTHCHECK, no model artifacts

#### GitHub Releases

- **v1.0.0** — tag pushed to remote (was created locally, not published)
- **v1.1.0** — annotated tag created and pushed with full release notes

### Changed

#### CI Template (`templates/cicd/ci.yml`)

- Added **Python 3.12 matrix** — test job now runs `["3.11", "3.12"]` in parallel
- Added **Codecov integration** — uploads `coverage.xml` on `3.11` run via `codecov/codecov-action@v4`
- Coverage report format changed from `term-missing` only → `xml` + `term-missing`

#### README

- Added **Release badge** with dynamic version from GitHub Releases
- Updated **Python badge** to `3.11 | 3.12`
- Added **Codecov badge**
- Updated `.cursor/rules/` entry to reflect 5 MDC rules (was 1)
- Updated repo tree with root DX files (`Makefile`, `.pre-commit-config.yaml`, `.gitleaks.toml`)

#### AGENTS.md / CLAUDE.md / .cursor/rules/

- Updated Multi-IDE Support section in AGENTS.md to show all 5 cursor rules

---

## [1.1.0] - 2026-04-15

### Added

#### Working Example (`examples/minimal/`)

- **Fraud detection service** — fully functional end-to-end demo (train → serve → predict → test → drift)
- `train.py` — synthetic data generation, Pandera validation, sklearn pipeline, quality gates
- `serve.py` — FastAPI with async inference (ThreadPoolExecutor), SHAP KernelExplainer, Prometheus metrics
- `test_service.py` — regression tests: data leakage, SHAP consistency, latency SLA, fairness DIR
- `drift_check.py` — PSI drift detection with quantile bins and exit codes (0/1/2)

#### Scaffolding

- **`new-service.sh`** — automated scaffolding script: copies templates, replaces placeholders ({ServiceName},
  {service}, {SERVICE}), creates directory structure

#### Monitoring

- **`alertmanager-rules.yaml`** — production AlertManager rules with P1–P4 severity:
  - Service down + error rate spike (P1)
  - Inference latency degradation (P2)
  - **Drift heartbeat missing** (P2) — fires if CronJob hasn't run in 48h
  - PSI drift alert/warning (P3)
  - CPU approaching limit + pod restarts (P4)

### Changed

#### drift_detection.py — Production CronJob Integration

- Added **exit codes** (0=ok, 1=warning, 2=alert) for K8s CronJob integration
- Added **GitHub Issue creation** on alert-level drift via GitHub API
- Added **reference data update** with timestamped backups
- Added proper `main()` function with `sys.exit()` for clean process control

#### test_explainer.py — Self-Contained SHAP Tests

- Replaced stub tests with **runnable, self-contained regression tests**
- Tests use synthetic data + simple pipeline (no service dependency)
- Covers: all-zero SHAP detection, consistency property, original feature space, background representativeness, latency
  SLA

#### Kustomize Structure

- Moved manifests to `k8s/base/` (standard Kustomize pattern)
- Fixed `commonLabels` (deprecated) → `labels` with pairs syntax
- Fixed `patchesStrategicMerge` (deprecated) → `patches` in overlays
- Replaced `kubeval` (abandoned) with `kubeconform` in CI

#### README

- Added **"Try It in 5 Minutes"** section with copy-paste commands
- Added **"What's Different From Other Templates"** comparison table
- Updated Quick Start to use `new-service.sh` scaffolding script
- Updated repo structure tree with all new files

#### Agentic System Improvements

- **Split `04-python-ml.md`** into `04a-python-serving.md` (app/) and `04b-python-training.md` (training/) — reduces
  unnecessary context loading
- **Added `10-examples.md`** — prevents production rules from firing in `examples/` directory
- **Added `.claude/rules/`** — 5 context-aware rules with `paths:` frontmatter for Claude Code IDE
- **AGENTS.md** — added Session Initialization Protocol, How to Invoke Skills, Multi-IDE Support sections
- **01-mlops-conventions.md** — slimmed from 75 to 43 lines, references `AGENTS.md` for detail
- **CLAUDE.md** — comprehensive rewrite: session protocol, full anti-pattern table, key commands
- **`.cursor/rules/`** — enhanced with session protocol, full D-01→D-12 table, key commands
- **Skill `new-service`** — now invokes `new-service.sh`, verifies zero remaining placeholders
- **Skill `debug-ml-inference`** — added D-01→D-12 anti-pattern checklist as Step 1
- **Skill `drift-detection`** — added PSI interpretation table with exit codes, special cases for time series/NLP/categorical
- **Workflow `/new-service`** — uses `new-service.sh` with manual fallback
- **Workflow `/incident`** — added Step 0: severity classification decision tree (P1–P4)
- **Workflow `/retrain`** — added explicit quality gate table with typical thresholds and verification script
- **Workflow `/cost-review`** — added PromQL queries for CPU/memory/throughput/HPA utilization

### Fixed

- black formatting: reformatted `test_explainer.py` and `drift_detection.py`
- flake8 F401: removed unused imports across 7 files
- flake8 E501/F841: fixed long lines and unused variable in cli.py
- Kustomize cycle error: restructured to standard `base/` + `overlays/` layout

---

## [1.0.0] - 2026-04-15

### Added

#### Agentic System

- **AGENTS.md** - Root-level agent architecture with 3-layer design (Orchestrator, 11 Specialist Agents, 4 Maintenance
  Agents), 12 anti-pattern detectors (D-01 to D-12), and Engineering Calibration Principle
- **10 context-aware rules** (`.windsurf/rules/`) - Behavioral constraints for K8s, Terraform, Python serving/training
  (split), CI/CD, Docker, docs, data validation, monitoring, examples
- **8 operational skills** (`.windsurf/skills/`) - Structured frontmatter with `allowed-tools`, `when_to_use`,
  `argument-hint`, per-step `Success criteria`
- **8 slash-command workflows** (`.windsurf/workflows/`) - `/release`, `/retrain`, `/load-test`, `/new-adr`,
  `/incident`, `/drift-check`, `/new-service`, `/cost-review`

#### Service Template (`templates/service/`)

- FastAPI app with async inference via ThreadPoolExecutor
- SHAP KernelExplainer integration with consistency checks
- Prometheus metrics (counter, histogram, summary)
- Pandera DataFrameModel for training, API, and drift validation
- Optuna hyperparameter tuning with configurable trials
- Quality gates (primary metric, secondary metric, fairness DIR >= 0.80)
- MLflow experiment tracking and model registry integration
- Comprehensive pytest tests (leakage, quality gates, API, SHAP, latency SLA)
- Locust load test template (100 concurrent users, < 1% error rate)
- Multi-stage Dockerfile with non-root USER and HEALTHCHECK

#### Common Utils (`templates/common_utils/`)

- `seed.py` - Reproducibility across Python, NumPy, PyTorch, TensorFlow
- `logging.py` - JSON formatter (production K8s) + colored human-readable (dev)
- `model_persistence.py` - joblib save/load with SHA256 integrity validation
- `telemetry.py` - OpenTelemetry tracing with graceful no-op fallback

#### Kubernetes (`templates/k8s/`)

- Deployment with init container for model download from GCS/S3
- CPU-only HPA (never memory for ML pods)
- Kustomize base + GCP-production and AWS-production overlays
- Argo Rollouts canary deployment with Prometheus-based AnalysisTemplate
- ServiceAccount with Workload Identity (GCP) and IRSA (AWS) annotations

#### Infrastructure (`templates/infra/`)

- Terraform GCP: GKE cluster, Workload Identity, GCS buckets, Artifact Registry
- Terraform AWS: EKS cluster, OIDC for IRSA, managed node group, IAM roles

#### CI/CD (`templates/cicd/`)

- CI: flake8 + black + isort + mypy, pytest (90% coverage), Docker build + Trivy
- Infrastructure CI: terraform validate + tfsec + Checkov + kubeval
- Deploy GCP/AWS: tag-triggered with cluster verification and smoke tests
- Drift Detection: daily scheduled + manual trigger, auto-creates GitHub issue
- Retraining: manual trigger with data validation, quality gates, artifact upload

#### Scripts (`templates/scripts/`)

- `deploy.sh` - Build, push, deploy with kubectl context verification and tag immutability
- `promote_model.sh` - Quality gates (metric, fairness, leakage, integrity) before promotion
- `health_check.sh` - Pod status + /health and /model/info endpoint checks

#### Developer Experience

- `docker-compose.demo.yml` - Demo stack with MLflow + Pushgateway + optional monitoring
- `Makefile` - Standard targets: train, test, serve, build, deploy, health-check, demo
- `.pre-commit-config.yaml` - black, isort, flake8, mypy, bandit, gitleaks
- `.gitleaks.toml` - Secret detection configuration
- `.env.example` - Environment variable documentation

#### Documentation Templates

- ADR template with Context, Options, Decision, Rationale, Consequences, Revisit When
- Runbook template with P1-P4 severity procedures
- Service README template with measured data slots
- Model card template for ML transparency
- Dependency analysis template for conflict documentation

#### Monitoring Templates

- Prometheus alerts: error rate, service down, drift heartbeat, latency, resources
- Grafana dashboard: request rate, latency percentiles, PSI scores, HPA, CPU/memory

#### Open Source Maturity

- `SECURITY.md` - Vulnerability reporting policy and security measures
- `CONTRIBUTING.md` - Contribution guidelines with Engineering Calibration awareness
- `CODE_OF_CONDUCT.md` - Contributor Covenant v2.0
- `.github/ISSUE_TEMPLATE/` - Bug report and feature request templates
- `.github/pull_request_template.md` - PR checklist with anti-pattern verification
- `.github/dependabot.yml` - Automated dependency updates
- `.gitattributes` - Git LFS for model artifacts, line ending normalization
- CI workflow `validate-templates.yml` - Validates K8s, Terraform, and Python templates

---

*This template was extracted from [ML-MLOps-Portfolio](https://github.com/DuqueOM/ML-MLOps-Portfolio), a production portfolio with 3 live ML services.*
