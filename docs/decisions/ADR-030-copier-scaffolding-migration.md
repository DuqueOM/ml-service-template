# ADR-030 — Copier-based Scaffolding Migration

- **Status**: Accepted
- **Date**: 2026-06-29
- **Deciders**: Template maintainer (`@DuqueOM`)
- **Supersedes / amends**: none. Executes Wave 1 of
  `docs/audit/ACTION_PLAN_ADAPTABILITY.md` under the governance of
  **ADR-029** (Agentic Adoption Contract). Complements ADR-015 (placeholder /
  slug conventions) and ADR-027 (vendor-neutral canonical surface).
- **Superseded by**: none
- **Related artifacts**:
  - `templates/scripts/new-service.sh` — the current bespoke scaffolder.
  - `scripts/test_scaffold.sh`, `templates/service/tests/policy/test_smoke.py`
    — scaffold contract tests.
  - `copier.yml` (new) — the Copier template descriptor.
  - `scripts/sync_agentic_adapters.py` — post-gen agentic surface sync.

## 1. Context

`docs/audit/ACTION_PLAN_ADAPTABILITY.md` (gap **B1**) identifies the highest-ROI
adoption lever: the template scaffolds via a bespoke `new-service.sh`, while the
de-facto standard in the data/ML ecosystem is a Cookiecutter/Copier template.

Two concrete deficits of the bespoke scaffolder:

1. **No upgrade path.** Once a service is generated, there is no supported way to
   pull later template improvements back into it. Adopters fork-and-forget. The
   manual `cicd-template-drift` gate detects drift in-repo but cannot propagate
   fixes downstream.
2. **Non-standard ergonomics.** Adopters expect `copier copy gh:org/repo svc/` and
   `copier update`, not a positional-argument bash script they must read first.

Measured footprint (verified 2026-06-29 in this repo):

- `227` of `573` files under `templates/` contain a substitution token
  (`{ServiceName}`, `{service-name}`, `{service}`, `{SERVICE}`, `{ORG}`, `{REPO}`).
- The scaffolder also renames `src/{service}/` → `src/<slug>/` and must avoid
  rewriting shell variables like `${SERVICE}` (handled today by a perl
  negative-lookbehind).
- Verified in paths: only `src/{service}/` carries a token in a path; the other
  226 token occurrences are in file **content**.

### 1.1 Discovered structural constraint (decisive)

Copier copies exactly **one** template root (`_subdirectory`) and the generated
project's layout is the template root's layout (with path templating). The
bespoke `new-service.sh` does **not** copy 1:1 — it performs structural remapping
and pulls files from the **repository root**, neither of which a single
`_subdirectory` can express:

- `templates/cicd/*.yml` → generated `.github/workflows/` (remap)
- repo-root `scripts/audit_record.py`, `scripts/validate_quality_gates.py`,
  `scripts/_lib/`, and `docs/runbooks/*.md` are merged in (pulls from outside
  `templates/`)
- selective copy of `templates/scripts/` (not the whole directory)

Consequence: a faithful Copier template — one whose tracked render is
byte-identical to the generated project, which is the precondition for a working
`copier update` 3-way merge — **requires the template root to mirror the output
layout**. A "thin Copier + post-gen assembly task" alternative was considered and
rejected (§7): it would only postpone the layout work while degrading
`copier update` to a destructive re-scaffold. Per the action plan, upgradability
(`copier update`) is the *requirement* of gap B1, not an optional extra, so the
faithful layout is in scope now.

This ADR chooses the migration tool and the **render model**, which is the
load-bearing decision.

## 2. Decision

Adopt **[Copier](https://github.com/copier-org/copier)** as the scaffolding
engine, using **native Jinja templating with custom delimiters**, and reduce
`new-service.sh` to a thin backward-compatible wrapper.

### 2.1 Tool: Copier (not Cookiecutter)

Copier is chosen over Cookiecutter because it provides first-class
`copier update` (three-way merge of later template changes into an existing
generated project) via a committed `.copier-answers.yml`. That update capability
is the entire point of closing gap B1; Cookiecutter requires the third-party
`cruft` shim to approximate it.

### 2.2 Render model: native Jinja with custom delimiters

Copier renders files through Jinja. Jinja's default delimiters (`{{ }}`, `{% %}`,
`{# #}`) would collide catastrophically with the template's content: Python
f-strings and dict literals, YAML/JSON braces, GitHub Actions `${{ … }}`
expressions, and `${SERVICE}` shell variables all use `{`/`}`. We therefore
configure **custom delimiters** that do not appear in the corpus:

```yaml
_envops:
  variable_start_string: "{@"
  variable_end_string: "@}"
  block_start_string: "{%"
  block_end_string: "%}"
  comment_start_string: "{#"
  comment_end_string: "#}"
  keep_trailing_newline: true
```

**Delimiter family selection (empirically decided — supersedes the initial
`[[ ]]` choice).** Token conversion ran in two stages. Stage 1 used the
Copier square-bracket family `[[ ]]` / `[% %]` / `[# #]`. While converting we
measured that `[[ ]]` collides with idiomatic ML/shell content the template
must contain verbatim:

- pandas double-bracket selection — `df[["a","b"]]` (appears in feature and
  monitoring code), and
- bash test conditionals — `[[ -z "$X" ]]`, `while [[ $# -gt 0 ]]` (appears in
  every shipped shell script).

Under `_templates_suffix: ""` (see §2.3) Copier renders **every** file's
content through Jinja, so any literal `[[` would be parsed as a variable-start
and break the render. Stage 2c therefore **superseded** `[[ ]]` with the
`{@ … @}` family, chosen empirically:

- **Zero collisions**: `{@`, `{%`, `{#` each appear `0` times across the 249
  render-root files (verified), so rendering every file is safe.
- **Windows-filename-safe**: every sigil (`{ @ % #`) is legal in a filename,
  so the path token `src/{@ service_slug @}/` checks out cross-platform.
- **Cannot collide with lint-clean code**: the service's own flake8 (E225)
  forbids the operator spacing (`x {@ y`) that could otherwise reproduce the
  sequence.
- **Blocks/comments stay native Jinja** (`{% %}` / `{# #}`): both
  start-strings are also `0`-occurrence, so deviation from standard Jinja is
  minimized to just the variable delimiter.

Tokens are converted **once** across the render-root files. `service_slug` is
the single source-of-truth answer; the other casings are **derived** in
`copier.yml` (see §2.3), so the conversion maps each legacy token to the
corresponding derived variable:

| Legacy bespoke token | After (Copier/Jinja, `{@ @}` family) |
| --- | --- |
| `{ServiceName}` | `{@ service_name @}`  (default: PascalCase of slug; recorded, see §2.3 amendment) |
| `{service-name}` | `{@ service_kebab @}` (derived: slug with `_`→`-`) |
| `{service}` | `{@ service_slug @}` |
| `{SERVICE}` | `{@ service_upper @}` (derived: UPPER of slug) |
| `{ORG}` | `{@ gh_org @}` |
| `{REPO}` | `{@ gh_repo @}` |
| dir `src/{service}/` | dir `src/{@ service_slug @}/` |

**Why native Jinja and not a post-gen substitution task** (the rejected
"hybrid" design, §7): Copier's `update` algorithm regenerates the project from the
old and new template versions and applies the diff onto the working copy. If
substitution happened in a post-copy *task* (outside Copier's tracked render),
the tracked render would remain tokenized while the project files are
substituted — every update hunk near a substituted token would fail to apply.
Native Jinja makes the tracked render byte-identical to the project, so
`copier update` works as designed. This is the whole value of B1.

A welcome side effect: with `{@ @}` delimiters, `${SERVICE}` (and GitHub
Actions `${{ … }}`) are not Jinja constructs, so the perl negative-lookbehind
hack in `new-service.sh` is **deleted**, not ported. Verified by a real
`copier copy`: bash `[[ ]]`, GHA `${{ }}`, and pandas `df[[ ]]` all survive the
render unmangled.

### 2.3 Questionnaire (`copier.yml`)

`service_slug` is the **single source-of-truth** prompt; every other casing is
derived, which eliminates the casing-drift defect class of the old sed
scaffolder (where `ServiceName` and the slug could disagree):

```yaml
service_slug:   { type: str, help: "snake_case slug (e.g. fraud_detector)" }
                # validated against ^[a-z][a-z0-9_]*$
service_name:   # asked, default PascalCase of slug; RECORDED (amendment 2026-09-29)
service_kebab:  # derived: slug with _ -> -      (when: false)
service_upper:  # derived: UPPER of slug         (when: false)
gh_org:         { type: str, help: "GitHub org for cosign/Kyverno trust root" }
gh_repo:        { type: str, default: "{@ service_kebab @}" }
```

`_subdirectory: templates/service` makes the template root the
`templates/service/` tree, which **already mirrors the generated-service
layout** (it is the service-bound tree relocated there in Stage 2a), so the
tracked render equals the project. `_templates_suffix: ""` makes Copier render
every file's **content** through Jinja (its v9 default of `.jinja` would copy
content verbatim and leave `{@ … @}` tokens unrendered — only path names get
rendered by default). `_answers_file: .copier-answers.yml` is committed into
the generated service to enable `copier update`.

#### Amendment 2026-09-29 — `service_name` is recorded

`service_name` was `when: false`, like the other derived casings, and so was
never written to `.copier-answers.yml`. It reaches only display text — module
docstrings, the FastAPI title, log lines, workflow and artifact labels — and
adopters override it: ml-platform generated its service with
`--data service_name="Demand Forecast Serving"`. That value lived only in the
command line. A regeneration from the answers file alone differed from the
committed service in 60 files (ml-platform QA-4 round fourteen, P3-5), and any
`copier update` run without the flag would re-render each changed line with
`DemandForecastServing`, one release at a time.

It is now an ordinary question with the PascalCase default, so `--defaults`
behaves as before and the value — default or not — is recorded. The other
derived casings stay `when: false`: they reach identifiers, and there the
single-source rule above is the point. An existing service picks the answer up
on its next `copier update`, which asks once (or takes `--data`) and records it.

### 2.4a Self-contained template root (vendoring + drift gate)

Because Copier cannot pull from outside its root, the files `new-service.sh`
currently sourced from the repo root are **vendored into `templates/service/`**:
`scripts/audit_record.py`, `scripts/validate_quality_gates.py`, and the Day-2
runbooks `docs/runbooks/drift-detection.md` + `docs/runbooks/model-retrain.md`
(`day-2-operations.md` already lived in the render root). `scripts/_lib/` is
**intentionally not vendored**: no generated artifact references it (verified),
so shipping it would be dead weight.

To prevent these vendored copies from drifting from their repo-root originals, a
**drift gate** — `scripts/check_vendored_runtime_drift.py` — asserts each
vendored copy is byte-identical to its canonical source (with a `--fix`
resync mode), following the existing precedent of the `common_utils` drift gate
(ADR-025 Option A) and the `cicd-template-drift` gate. It runs as the
`vendored-runtime-drift` job in `validate-templates.yml`. All four vendored
files carry zero `{@`/`{%`/`{#` sequences, so Copier renders them as an
identity transform — verified by a real `copier copy`. This keeps a single
source of truth while satisfying Copier's single-root constraint.

### 2.4 Post-generation tasks (`_tasks`) — the agentic gate

After render, Copier runs, in order:

1. `scripts/sync_agentic_adapters.py` — regenerate `.cursor/.claude/.codex/.devin`
   surfaces from the canonical `agentic/` store into the new project.
2. `scripts/validate_agentic_manifest.py --strict` — fail loud on any drift.

This wires the **Agentic Adoption Contract** (ADR-029 condition 4) directly into
scaffolding and into every `copier update`: a generated project can never ship
with stale or hand-edited agent surfaces.

### 2.5 Retirement path for `new-service.sh`

`new-service.sh` is **not deleted**. It becomes a thin wrapper that maps its two
positional arguments to `copier copy --data` and shells out to Copier, emitting a
deprecation notice. This keeps `scripts/test_scaffold.sh` and every doc that
references the script working through the transition (one minor-release
deprecation window before removal is reconsidered).

### 2.6 Staged rollout (Option A, validated per stage)

The restructure is executed as four reviewable, individually-validated stages —
not a single blind big-bang. `new-service.sh` stays as a working fallback until
the Copier path is green in CI.

- **Stage 1 — token conversion (content)**: convert the render-root files
  `{token} → {@ jinja @}` (Stage 1 first used `[[ ]]`, superseded in Stage 2c —
  see §2.2) and rename `src/{service}/ → src/{@ service_slug @}/`.
  Validate: zero residual `{...}` tokens; `${SERVICE}` shell vars untouched.
- **Stage 2 — layout + plumbing**: reorganize `templates/` to mirror output
  (`cicd/ → .github/workflows/`), vendor the repo-root tools (§2.4a) with their
  drift gate, and add `copier.yml` (`_envops`, `_tasks`, `_exclude`,
  `_answers_file`).
- **Stage 3 — equivalence + tests**: reduce `new-service.sh` to a wrapper,
  update the scaffold contract tests, and validate a real `copier copy` render
  end-to-end (structure, no tokens, surfaces synced) plus `copier update` on a
  generated project.
- **Stage 4 — governance**: anti-patterns **D-33/D-34**,
  `agentic/rules/15-template-lifecycle.md`, the `scaffold-update` skill +
  `/scaffold-update` workflow, manifest entries, `rule-audit` catalogue bump.

## 3. Invariants (contract-enforced)

- **I-030-1** — A freshly generated project contains **zero** unrendered tokens
  (existing `test_smoke.py::test_scaffold_replaces_placeholders`, updated to the
  Jinja era) and **zero** Copier delimiters (`{@`, `{%`, `{#`).
- **I-030-2** — A generated project's `.cursor/.claude/.codex/.devin` surfaces are
  byte-identical to `sync_agentic_adapters.py` output (post-gen task + the
  existing `--check` discipline). No surface is hand-rendered.
- **I-030-3** — `.copier-answers.yml` is committed in generated projects;
  `copier update` is the supported upgrade path (documented in `MIGRATION.md`).
- **I-030-4** — `new-service.sh` produces output equivalent to `copier copy` for
  the same inputs throughout the deprecation window (asserted by
  `scripts/test_scaffold.sh`).
- **I-030-5** — Vendored copies in `templates/service/` (`audit_record.py`,
  `validate_quality_gates.py`, the Day-2 runbooks) are byte-identical to
  their repo-root sources; CI's `vendored-runtime-drift` gate
  (`scripts/check_vendored_runtime_drift.py`) fails otherwise (ADR-025
  precedent).

## 4. Scope

**In scope**: `copier.yml`, custom-delimiter conversion of the `templates/` tree,
post-gen agentic sync, the `new-service.sh` wrapper, scaffold-test updates, and a
CI lane that renders via Copier.

**Out of scope** (own ADRs / waves):

- Local-first stack profiles → ADR-031 (Wave 2).
- CCDS-aligned layout → ADR-032 (Wave 3).
- `uv`/`pyproject` modernization → Wave 4.
- Removing `new-service.sh` entirely (deferred until adopter telemetry confirms
  the wrapper is unused).

## 5. Consequences

### Positive

- Industry-standard scaffolding (`copier copy`) and, critically, a real upgrade
  path (`copier update`) — the main adoption lever in the action plan.
- The agentic surfaces are regenerated on every scaffold and every update,
  enforcing ADR-029 condition 4 mechanically.
- Deletes the `${SERVICE}` negative-lookbehind special case.

### Negative

- A one-time 227-file mechanical diff (token → Jinja). Mitigated: scripted,
  reviewed in a single focused PR, and gated by a real Copier render in CI plus
  the unchanged scaffold contract tests.
- Adds `copier` as a scaffolding-time dependency. Mitigated: it is a dev/scaffold
  tool (like `cookiecutter`), pinned with `~=`, not a runtime dependency of
  generated services.

### Neutral

- `.copier-answers.yml` appears in generated projects. Expected and required for
  updates.

## 6. License & provenance (ADR-029 §1.1 / action plan §1.1)

Copier is **MIT-licensed** and consumed as a tool dependency; using it does not
make generated output a derivative work of Copier (same as `git`/`pytest`). **No
Copier source is vendored.** The repository remains **Apache-2.0**. Reference
SPDX identifiers verified for Wave 1: Copier — MIT; Cookiecutter Data Science —
MIT; ZenML — Apache-2.0; Made With ML — MIT (see action plan §1.1, item W1.5b).

## 7. Alternatives considered

| Alternative | Why rejected |
| ------------- | -------------- |
| Keep `new-service.sh`, add `cruft` for updates | Cruft is a Cookiecutter shim; Copier's native update is cleaner and the tool is simpler to pin |
| Cookiecutter (no update) | No first-class update path; B1's main value is upgradability |
| **Hybrid: Copier copies verbatim + post-gen task substitutes tokens** | Avoids the 227-file diff, but breaks `copier update`: the tracked render stays tokenized while project files are substituted, so update hunks near tokens fail to apply — defeats B1 |
| **"Option B": thin Copier front-end + post-gen assembly task reusing `new-service.sh`** | Lower effort, but only *postpones* the layout work rather than solving it, and degrades `copier update` to a destructive re-scaffold (no 3-way merge). Since upgradability IS the B1 requirement, this fails the requirement while adding a second code path to maintain |
| Default Jinja delimiters (`{{ }}`) | Collides with Python f-strings, dict/JSON/YAML braces, and `${SERVICE}` across 227 files — unworkable |
| Big-bang delete of `new-service.sh` | Breaks docs + `test_scaffold.sh` during transition; no rollback safety |

## 8. Revisit triggers

- **Copier publishes a breaking 10.x with different `_envops` semantics** →
  re-pin and re-validate the render in CI.
- **Adopter telemetry shows `new-service.sh` unused for two minor releases** →
  remove the wrapper (new ADR amendment).
- **A template file legitimately needs a literal `[[` or `[%`** → escape via
  `[[ '[[' ]]` or switch that file to a `.jinja`-suffixed explicit template.
- **`copier update` 3-way merges prove noisy in practice** → document a
  `--skip-answered` / conflict-resolution recipe in `MIGRATION.md`.

## 9. Related

- `docs/decisions/ADR-029-agentic-adoption-contract.md` — governs this wave.
- `docs/decisions/ADR-015-*.md` — placeholder/slug conventions this preserves.
- `docs/decisions/ADR-027-vendor-neutral-canonical-surface.md` — the agentic
  surfaces the post-gen task regenerates.
- `docs/audit/ACTION_PLAN_ADAPTABILITY.md` — Wave 1 tracker (B1).
