#!/usr/bin/env bash
# test_scaffold.sh — Validate that Copier produces a working service
#
# Runs `copier copy` (via new-service.sh) in an isolated temp directory and
# verifies:
#   1. Exit code 0
#   2. Service directory created
#   3. Zero remaining {@ @} / {% %} / {# #} Jinja tokens
#   4. Critical files exist (Dockerfile, requirements.txt, src/<slug>/, app/, k8s/)
#   5. src/<slug>/ directory was correctly renamed from src/{@ service_slug @}/
#   6. Python modules are syntactically valid
#   7. Kustomize overlays render without errors
#   8. (optional) pytest dry-collect succeeds on the scaffolded tests/
#   9. Post-gen tasks ran (agentic adapters synced, manifest valid)
#
# The test creates a temp clone of the repo, runs new-service.sh (which calls
# `copier copy --vcs-ref HEAD`), validates, and cleans up.
#
# Exit codes:
#   0 = scaffold works
#   1 = validation failure
#   2 = setup error
#
# Usage:
#   ./scripts/test_scaffold.sh                   # default: TestSvc test_svc
#   ./scripts/test_scaffold.sh MyName my_name    # custom names
#   ./scripts/test_scaffold.sh --keep            # don't cleanup temp dir (debug)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SERVICE_NAME="TestSvc"
SERVICE_SLUG="test_svc"
KEEP_TEMP=false

for arg in "$@"; do
  case $arg in
    --keep) KEEP_TEMP=true ;;
    -h|--help)
      grep '^#' "$0" | head -25
      exit 0
      ;;
    *)
      if [[ -z "${CUSTOM_NAME_SET:-}" ]]; then
        SERVICE_NAME="$arg"
        CUSTOM_NAME_SET=1
      else
        SERVICE_SLUG="$arg"
      fi
      ;;
  esac
done

SERVICE_UPPER=$(echo "$SERVICE_SLUG" | tr '[:lower:]' '[:upper:]')

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1" >&2; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${BLUE}→${NC} $1"; }

# ════════════════════════════════════════════════
# Setup isolated test environment
# ════════════════════════════════════════════════
info "Creating isolated test environment..."
TEMP_ROOT="$(mktemp -d -t mlops-scaffold-test.XXXXXX)"

cleanup() {
  if [[ "$KEEP_TEMP" == "true" ]]; then
    echo -e "${YELLOW}Temp dir preserved: $TEMP_ROOT${NC}"
  else
    rm -rf "$TEMP_ROOT"
  fi
}
trap cleanup EXIT

# Copier needs the full repo (copier.yml at root + templates/service/).
# We clone the local repo into the temp dir so Copier can use --vcs-ref HEAD.
# Using git clone --no-hardlinks avoids issues with local file:// protocol.
info "Cloning repo into temp environment..."
if ! git clone --no-hardlinks "$REPO_ROOT" "$TEMP_ROOT/repo" > "$TEMP_ROOT/clone.log" 2>&1; then
  fail "git clone failed"
  cat "$TEMP_ROOT/clone.log" >&2
  exit 2
fi

# Copy uncommitted changes into the clone (Copier --vcs-ref HEAD includes
# dirty working tree, but only if the clone itself is dirty). We rsync the
# working tree over the clone to capture staged + unstaged changes.
rsync -a --delete --exclude='.git' \
  "$REPO_ROOT/" "$TEMP_ROOT/repo/" 2>/dev/null || true

pass "Temp environment: $TEMP_ROOT/repo"

# ════════════════════════════════════════════════
# Run the scaffolder (thin Copier wrapper)
# ════════════════════════════════════════════════
info "Running new-service.sh $SERVICE_NAME $SERVICE_SLUG..."
FAILURES=0
SERVICE_DIR="$TEMP_ROOT/repo/$SERVICE_NAME"

if bash "$TEMP_ROOT/repo/templates/scripts/new-service.sh" \
    "$SERVICE_NAME" "$SERVICE_SLUG" "test-org" "test-svc" \
    > "$TEMP_ROOT/scaffold.log" 2>&1; then
  pass "Scaffolder exited 0"
else
  fail "Scaffolder failed. Log:"
  cat "$TEMP_ROOT/scaffold.log" >&2
  exit 1
fi

# ════════════════════════════════════════════════
# Validation 1 — Service directory exists
# ════════════════════════════════════════════════
info "Validating service directory..."
[[ -d "$SERVICE_DIR" ]] && pass "Service directory created: $SERVICE_NAME/" \
  || fail "Service directory missing: $SERVICE_DIR"

# ════════════════════════════════════════════════
# Validation 2 — Critical files exist
# ════════════════════════════════════════════════
info "Validating critical files..."
CRITICAL_FILES=(
  "Dockerfile"
  "requirements.txt"
  "pyproject.toml"
  "Makefile"
  ".github/workflows/ci.yml"
  ".github/workflows/deploy-gcp.yml"
  ".github/workflows/deploy-aws.yml"
  ".github/workflows/deploy-common.yml"
  "src/$SERVICE_SLUG"
  "app"
  "k8s/base"
  "k8s/overlays"
  "tests"
  "monitoring"
  "infra/terraform"
  "docs/runbooks/day-2-operations.md"
  "docs/runbooks/drift-detection.md"
  "docs/runbooks/model-retrain.md"
  "eda/eda_pipeline.py"
  "eda/requirements.txt"
  "eda/reports"
  "eda/artifacts"
  "eda/notebooks"
)
for f in "${CRITICAL_FILES[@]}"; do
  if [[ -e "$SERVICE_DIR/$f" ]]; then
    pass "Found: $f"
  else
    fail "Missing: $f"
  fi
done

# ════════════════════════════════════════════════
# Validation 3 — Zero remaining Jinja tokens
# ════════════════════════════════════════════════
info "Checking for unreplaced Jinja tokens..."
# Copier uses {@ @} for variables, {% %} for blocks, {# #} for comments.
# After rendering, none of these should appear in any file.
JINJA_REGEX='\{@.*@\}|\{%.*%\}|\{#.*#\}'
# Exclude agentic/ and .devin/ docs — they legitimately mention {@ @} as
# literal text when documenting Copier delimiters (D-33/D-34 rules).
# The {% raw %} blocks ensure Copier doesn't try to render them, but the
# literal text remains in the output as intended.
# Also exclude AGENTS.md and CLAUDE.md (root) which document D-34 / grep
# examples with literal {@ @} tokens wrapped in {% raw %} blocks.
PLACEHOLDER_HITS=$({
  grep -rE "$JINJA_REGEX" \
    "$SERVICE_DIR" \
    --exclude-dir="agentic" --exclude-dir=".devin" \
    --include="*.py" --include="*.yaml" --include="*.yml" --include="*.md" \
    --include="*.toml" --include="*.sh" --include="*.tf" --include="*.json" \
    --include="*.txt" --include="Dockerfile" --include="Makefile" \
    2>/dev/null | grep -v '/AGENTS\.md:' | grep -v '/CLAUDE\.md:' || true
} | wc -l)

if [[ "$PLACEHOLDER_HITS" -eq 0 ]]; then
  pass "Zero unreplaced Jinja tokens ({@ @}, {% %}, {# #})"
else
  fail "$PLACEHOLDER_HITS lines still contain Jinja tokens:"
  grep -rEn "$JINJA_REGEX" \
    "$SERVICE_DIR" \
    --exclude-dir="agentic" --exclude-dir=".devin" \
    --include="*.py" --include="*.yaml" --include="*.yml" --include="*.md" \
    --include="*.toml" --include="*.sh" --include="*.tf" --include="Dockerfile" \
    --include="Makefile" 2>/dev/null | grep -v '/AGENTS\.md:' | head -10 >&2
fi

# ════════════════════════════════════════════════
# Validation 4 — src/{service}/ was renamed correctly
# ════════════════════════════════════════════════
info "Checking src/ directory rename..."
if [[ -d "$SERVICE_DIR/src/$SERVICE_SLUG" ]] && [[ ! -d "$SERVICE_DIR/src/{@ service_slug @}" ]]; then
  pass "src/{@ service_slug @} → src/$SERVICE_SLUG"
else
  fail "src/ directory not renamed correctly"
fi

# ════════════════════════════════════════════════
# Validation 5 — Python syntactic check
# ════════════════════════════════════════════════
info "Checking Python syntax..."
PY_ERRORS=0
while IFS= read -r -d '' pyfile; do
  if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
    fail "Syntax error: ${pyfile#$SERVICE_DIR/}"
    PY_ERRORS=$((PY_ERRORS + 1))
  fi
done < <(find "$SERVICE_DIR" -name "*.py" -print0)

if [[ "$PY_ERRORS" -eq 0 ]]; then
  pass "All Python files parse"
fi

# ════════════════════════════════════════════════
# Validation 5c — Post-gen tasks ran (agentic system)
# ════════════════════════════════════════════════
info "Checking post-gen agentic system..."
if [[ -d "$SERVICE_DIR/.devin/rules" ]]; then
  pass "Agentic adapters synced (.devin/rules/ exists)"
else
  fail "Agentic adapters NOT synced (.devin/rules/ missing)"
fi
if [[ -f "$SERVICE_DIR/config/agentic_manifest.yaml" ]]; then
  pass "Agentic manifest present (config/agentic_manifest.yaml)"
else
  fail "Agentic manifest missing"
fi
# ════════════════════════════════════════════════
# Validation 5a-bis — Copier answers file (the update path)
# ════════════════════════════════════════════════
# `copier copy` DOES create this file — provided the template ships an
# answers-file template in the render root. It is not an update-only
# artifact; the previous comment here asserted the opposite and thereby
# encoded a real defect as expected behaviour, which is why a template
# that could never be updated passed this suite for its whole life.
#
# Without this file `copier update` cannot run and the generated service
# is "a fork with extra steps" — precisely what ADR-003 §"Generation, not
# copying" exists to prevent, and what the scaffold-update workflow and
# _message_after_copy both promise. Assert it, never assume it.
info "Checking Copier answers file (ADR-003 update path)..."
ANSWERS_FILE="$SERVICE_DIR/.copier-answers.yml"
if [[ -f "$ANSWERS_FILE" ]]; then
  pass "Copier answers file present (.copier-answers.yml)"
  # `_commit` is what `copier update` diffs against. An answers file
  # without it records answers but still strands the service.
  if grep -qE '^_commit:' "$ANSWERS_FILE"; then
    pass "Answers file records _commit (update path is live)"
  else
    fail "Answers file lacks _commit — 'copier update' has no base revision to diff from"
  fi
  if grep -qE '^service_slug:' "$ANSWERS_FILE"; then
    pass "Answers file records the scaffold answers"
  else
    fail "Answers file lacks service_slug — answers were not persisted"
  fi
else
  fail "Copier answers file MISSING — generated service has no 'copier update' path (ADR-003)"
fi

# ════════════════════════════════════════════════
# Validation 5b — Generated CI/CD expects root-service layout
# ════════════════════════════════════════════════
info "Checking generated CI/CD layout contract..."
CI_FILE="$SERVICE_DIR/.github/workflows/ci.yml"
GCP_FILE="$SERVICE_DIR/.github/workflows/deploy-gcp.yml"
AWS_FILE="$SERVICE_DIR/.github/workflows/deploy-aws.yml"
COMMON_FILE="$SERVICE_DIR/.github/workflows/deploy-common.yml"

if grep -qE '\$\{\{ matrix\.service \}\}/(requirements\.txt|results|coverage\.xml)|cd \$\{\{ matrix\.service \}\}|docker build .* \$\{\{ matrix\.service \}\}/' "$CI_FILE"; then
  fail "ci.yml still assumes a nested service directory instead of the scaffolded repo root"
else
  pass "ci.yml uses scaffolded repo root for install, tests, coverage, and Docker build"
fi

SERVICE_KEBAB=$(echo "$SERVICE_SLUG" | tr '_' '-')
if grep -q "$SERVICE_NAME" "$GCP_FILE" "$AWS_FILE" "$COMMON_FILE"; then
  fail "deploy workflows still use PascalCase service names where kebab-case image/K8s slugs are required"
else
  pass "deploy workflows use kebab-case service slugs"
fi

if grep -Fq '${SERVICE}-predictor' "$GCP_FILE" "$AWS_FILE" && \
   grep -q "${SERVICE_KEBAB}-predictor" "$SERVICE_DIR/k8s/overlays/gcp-dev/kustomization.yaml"; then
  pass "deploy workflows publish images compatible with Kustomize overlays"
else
  fail "deploy workflows do not reference ${SERVICE_KEBAB}-predictor image names"
fi

# ════════════════════════════════════════════════
# Validation 6 — Kustomize overlays render
# ════════════════════════════════════════════════
info "Validating Kustomize overlays..."
# Discovered, not listed. The hardcoded six omitted `batch-only` — the same
# gap PR #102 closed in the CI workflows, still open here. A list has to be
# remembered; a glob cannot forget.
mapfile -t OVERLAYS < <(cd "$SERVICE_DIR/k8s/overlays" 2>/dev/null && ls -d */ 2>/dev/null | sed 's|/$||')
if [[ ${#OVERLAYS[@]} -eq 0 ]]; then
  fail "No overlays found under k8s/overlays/ — a loop over nothing reports the same green as a full pass"
fi
if command -v kustomize >/dev/null 2>&1; then
  for overlay in "${OVERLAYS[@]}"; do
    overlay_dir="$SERVICE_DIR/k8s/overlays/$overlay"
    if [[ -d "$overlay_dir" ]]; then
      if kustomize build "$overlay_dir" > /dev/null 2>&1; then
        pass "Overlay renders: $overlay"
      else
        fail "Overlay fails to render: $overlay"
      fi
    else
      fail "Overlay missing after scaffold: $overlay"
    fi
  done
elif command -v kubectl >/dev/null 2>&1; then
  for overlay in "${OVERLAYS[@]}"; do
    overlay_dir="$SERVICE_DIR/k8s/overlays/$overlay"
    if [[ -d "$overlay_dir" ]]; then
      if kubectl kustomize "$overlay_dir" > /dev/null 2>&1; then
        pass "Overlay renders: $overlay (via kubectl)"
      else
        fail "Overlay fails to render: $overlay"
      fi
    else
      fail "Overlay missing after scaffold: $overlay"
    fi
  done
else
  echo -e "${YELLOW}⚠${NC} kustomize/kubectl not available — skipping overlay validation"
fi

# ════════════════════════════════════════════════
# Validation 7 — pytest can collect tests (dry run)
# ════════════════════════════════════════════════
info "Testing pytest collection..."
if command -v pytest >/dev/null 2>&1; then
  # pytest --collect-only validates test files parse without running them
  collect_out="$(cd "$SERVICE_DIR" && PYTHONPATH=.:src pytest --collect-only -q tests/ 2>&1)" \
    && collect_rc=0 || collect_rc=$?
  if [[ "$collect_rc" -eq 0 ]]; then
    pass "pytest can collect tests/"
  else
    # Deliberately a warning, not a failure: without an install step a missing
    # third-party dependency is expected here, and classifying WHICH errors are
    # environmental turned out to be unreliable — a conftest that raises does
    # not surface as an `E   <Exception>` line at all, so any such heuristic
    # waves real defects through.
    #
    # The honest check lives in the SCAFFOLD_SMOKE chain below, AFTER the
    # dependencies are installed, where a collection error can only mean the
    # scaffold is broken. CI sets SCAFFOLD_SMOKE=1, so CI gets the hard gate.
    echo -e "${YELLOW}⚠${NC} pytest collection failed (deps absent here; the hard check runs post-install under SCAFFOLD_SMOKE=1)"
    echo "$collect_out" | tail -8
  fi
else
  echo -e "${YELLOW}⚠${NC} pytest not installed — skipping collection check"
fi

# ════════════════════════════════════════════════
# Validation 7b — the RENDERED markdown is lint-clean
# ════════════════════════════════════════════════
# This has to run against the render, not the template. A `{% raw %}` alone on
# a line renders to nothing but keeps its newline, so the generated service
# gained blank lines that do not exist in the source: the template linted clean
# while every scaffolded service shipped MD012 violations. No template-side
# check can see that, and the service's own docs-quality lane only runs after
# an adopter already has the repository.
info "Linting the rendered service's markdown..."
if command -v npx >/dev/null 2>&1; then
  if [[ ! -f "$SERVICE_DIR/.markdownlint-cli2.jsonc" ]]; then
    fail "Scaffolded service has no .markdownlint-cli2.jsonc — its docs-quality job would lint nothing and pass"
  else
    # The service is scaffolded INSIDE the cloned template repo, whose
    # .gitignore lists the scaffold directory. The shipped config sets
    # `gitignore: true`, so markdownlint walked up to that enclosing repo,
    # found the whole service ignored and linted zero files. An adopter's
    # service is its own repository — Copier's closing message makes
    # "Initialize git" step 3 — so make it one before linting. This also
    # brings the smoke run closer to reality for the git-dependent contract
    # tests that follow.
    git -C "$SERVICE_DIR" init -q 2>/dev/null || true
    md_out="$(cd "$SERVICE_DIR" && npx --yes markdownlint-cli2@0.23.2 2>&1)" && md_rc=0 || md_rc=$?
    md_files="$(echo "$md_out" | sed -n 's/^Linting: \([0-9]*\) files$/\1/p' | head -1)"
    if [[ "$md_rc" -ne 0 ]]; then
      fail "Rendered service fails its own markdown lint"
      echo "$md_out" | grep -E 'MD[0-9]{3}/' | head -10
    elif [[ -z "$md_files" || "$md_files" -eq 0 ]]; then
      # A run that linted nothing exits 0 too. Zero files is the failure this
      # whole validation exists to distinguish from a clean pass.
      fail "markdownlint linted 0 files in the rendered service — the config's globs match nothing"
    else
      pass "Rendered markdown is lint-clean ($md_files files)"
    fi
  fi
elif [[ -n "${CI:-}" ]]; then
  # Locally npx may be absent; in CI it never is, and a silent skip there
  # would leave this validation permanently unexercised.
  fail "npx not available in CI — the rendered-markdown lint could not run"
else
  echo -e "${YELLOW}\u26a0${NC} npx not available — skipping rendered-markdown lint (CI enforces it)"
fi

# ════════════════════════════════════════════════
# Validation 8 — Smoke: install deps + snapshot + pytest (optional)
# ════════════════════════════════════════════════
# These validations exercise what a freshly-scaffolded service can DO,
# not just what files it has. They are guarded by SCAFFOLD_SMOKE=1 so
# the lightweight structural checks above stay fast for local runs.
# CI (validate-templates.yml) sets the flag to enable the full chain.
SMOKE_REQUESTED="${SCAFFOLD_SMOKE:-0}"

if [[ "$SMOKE_REQUESTED" == "1" ]]; then
  info "Running smoke chain (install + snapshot + pytest)..."

  # Use a venv to avoid PEP-668 friction on Ubuntu 22.04+ runners.
  if python3 -m venv "$TEMP_ROOT/venv" 2>/dev/null; then
    # shellcheck disable=SC1091
    source "$TEMP_ROOT/venv/bin/activate"
    pass "Created venv: $TEMP_ROOT/venv"
  else
    fail "SCAFFOLD_SMOKE=1 but python3 -m venv failed; smoke chain is mandatory"
  fi
fi

if [[ "$SMOKE_REQUESTED" == "1" && "$FAILURES" -eq 0 ]]; then
  # 8a. Install scaffolded service deps. Bound by 5 min to fail fast on
  # network outages. In SCAFFOLD_SMOKE mode this is a hard gate: a
  # scaffold that cannot install its own dependencies is not a validated
  # end-to-end scaffold.
  # requirements-dev.txt, not requirements.txt. It opens with
  # `-r requirements.txt` and adds the test tooling. The runtime set alone used
  # to be enough here only because pytest, httpx and locust were declared in
  # it; the runtime/training split (ADR-049) moved them out, and installing the
  # runtime set left `pytest` resolving to the SYSTEM interpreter outside this
  # venv, which then could not see numpy:
  #     ModuleNotFoundError: No module named 'numpy'
  # The collection check below is the whole point of the smoke chain, so it has
  # to run against the environment a developer of the generated service
  # actually has.
  info "Installing scaffolded service dependencies (timeout 300s)..."
  if (cd "$SERVICE_DIR" && timeout 300 pip install --quiet --upgrade pip \
        && timeout 300 pip install --quiet -r requirements-dev.txt) 2>"$TEMP_ROOT/pip.log"; then
    pass "Dependencies installed"
  else
    echo "pip install log tail:" >&2
    tail -5 "$TEMP_ROOT/pip.log" >&2 || true
    fail "SCAFFOLD_SMOKE=1 but dependency installation failed"
  fi
fi

if [[ "$SMOKE_REQUESTED" == "1" && "$FAILURES" -eq 0 ]]; then
  # 8a-bis. The whole shipped suite must COLLECT, now that dependencies exist.
  #
  # This is the check that was missing. Validation 7 above runs before the
  # install and therefore has to guess whether a collection error is
  # environmental; it guessed "deps not installed" for months while
  # tests/policy/conftest.py raised a RuntimeError looking for a template-repo
  # path, so `pytest` was broken in EVERY scaffolded service and nothing said
  # so. After the install there is nothing left to blame.
  info "Verifying the scaffolded suite collects with dependencies installed..."
  if (cd "$SERVICE_DIR" && PYTHONPATH=.:src pytest --collect-only -q tests/ > "$TEMP_ROOT/collect.log" 2>&1); then
    pass "Scaffolded tests/ collects cleanly"
  else
    fail "scaffolded tests/ does not collect even with dependencies installed:"
    tail -15 "$TEMP_ROOT/collect.log" >&2
  fi
fi

if [[ "$SMOKE_REQUESTED" == "1" && "$FAILURES" -eq 0 ]]; then
  # 8b. Bootstrap the OpenAPI contract snapshot (D-28). Required by
  # tests/contract/test_openapi_snapshot.py::test_snapshot_file_exists.
  info "Generating openapi.snapshot.json via refresh_contract.py..."
  if (cd "$SERVICE_DIR" && PYTHONPATH=.:src python scripts/refresh_contract.py) \
        > "$TEMP_ROOT/refresh.log" 2>&1; then
    if [[ -f "$SERVICE_DIR/tests/contract/openapi.snapshot.json" ]]; then
      pass "OpenAPI snapshot bootstrapped"
    else
      fail "refresh_contract.py exited 0 but snapshot file is missing"
    fi
  else
    fail "refresh_contract.py failed:"
    tail -10 "$TEMP_ROOT/refresh.log" >&2
  fi
fi

if [[ "$SMOKE_REQUESTED" == "1" && "$FAILURES" -eq 0 ]]; then
  # 8c. Run the real test suite. Validates that the scaffolded service
  # is testable AND its tests pass against a freshly-generated snapshot.
  # `test_quality_gates_config.py` was added by PR-R2-7 (audit R2 §4.2)
  # to gate every change to configs/quality_gates.yaml — it runs in
  # milliseconds (no sklearn imports) so it's cheap to include here.
  info "Running pytest (FastAPI contract + API + training + quality gates + contract/)..."
  # `test_fastapi_template_contract.py` (v0.15.2) gates the
  # scaffolded serving contract: required endpoints, executor-backed
  # async inference, train/inference feature parity, readiness gating,
  # auth/admin guards, observability hooks, and dev-only modelless
  # startup.
  # `test_prediction_logger_lifecycle.py` (Phase 1.1) gates the env-aware
  # fail-fast contract for closed-loop monitoring. It runs in milliseconds
  # because each test only exercises `_start_prediction_logger` directly,
  # never the full FastAPI lifespan.
  # `test_error_envelope.py` (Phase 1.2) gates the canonical error
  # contract; it runs against the real router so a regression on
  # `install_error_envelope` is caught here.
  # `test_metrics_contract.py` (Phase 1.3) gates the alignment between
  # Counter/Gauge/Histogram declarations in app/fastapi_app.py and
  # src/<svc>/monitoring/* AND the metrics referenced in alert exprs
  # in k8s/base/slo-prometheusrule.yaml + monitoring/alertmanager-rules.yaml.
  # Catches the silent-failure case where a metric is renamed in code
  # but the alert still references the old name and never fires.
  # `test_quality_gates_schema_sync.py` (Phase 2 / PR-B1) gates the
  # behavioural equivalence between the Pydantic QualityGatesConfig
  # model and the committed JSON Schema file used by
  # `scripts/validate_quality_gates.py` and editor tooling. Drift
  # there means a config that passed CI Pydantic validation could
  # still fail JSON-Schema validation in a downstream tool — the
  # exact silent-divergence ADR-015 PR-B1 closes.
  # `test_eda_gate.py` + `test_drift_eda_baseline.py` (PR-B2 stage 2)
  # gate the canonical EDA-artifact contract end-to-end:
  #   - training refuses to start when leakage_report.json is BLOCKED;
  #   - drift CronJob can compute PSI against the parquet baseline;
  #   - both modes (legacy CSV reference / new EDA baseline) agree
  #     within tolerance on no-drift data.
  # Without these, a service could ship the canonical artifacts but
  # silently fall back to legacy CSV mode in production — exactly the
  # silent-divergence ADR-015 PR-B2 closes.
  # `test_split_strategies.py` + `test_training_manifest.py` (PR-B3)
  # gate the leakage-hardening + reproducibility-evidence contract:
  #   - Trainer._split_data dispatches on quality_gates.split.strategy;
  #     temporal split forbids future-leak; grouped split keeps entities
  #     disjoint; random refuses without explicit acknowledge_iid.
  #   - Every Trainer.run() writes a versioned training_manifest.json
  #     with content hashes, dependency versions, EDA cross-reference,
  #     and quality-gate verdict — even on rejected runs.
  if (cd "$SERVICE_DIR" && PYTHONPATH=.:src timeout 240 pytest \
        tests/test_fastapi_template_contract.py \
        tests/test_api.py tests/test_training.py \
        tests/test_quality_gates_config.py \
        tests/test_quality_gates_schema_sync.py \
        tests/test_eda_gate.py \
        tests/test_drift_eda_baseline.py \
        tests/test_split_strategies.py \
        tests/test_training_manifest.py \
        tests/test_evidence_bundle.py \
        tests/test_promote_evidence_gate.py \
        tests/test_prediction_logger_lifecycle.py \
        tests/test_error_envelope.py \
        tests/test_input_validation.py \
        tests/test_metrics_contract.py \
        tests/test_alert_routing_contract.py \
        tests/test_drills_reproducible.py \
        tests/test_k8s_name_vocabulary.py \
        tests/test_day2_artifacts_contract.py \
        tests/test_data_paths.py \
        tests/contract/ \
        tests/integration/ \
        -q --tb=short --no-cov --capture=no) > "$TEMP_ROOT/pytest.log" 2>&1; then
    pass "pytest passed on freshly-scaffolded service"
  else
    fail "pytest failed:"
    tail -20 "$TEMP_ROOT/pytest.log" >&2
  fi
fi

# ════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════
echo ""
if [[ "$FAILURES" -eq 0 ]]; then
  echo -e "${GREEN}━━━ SCAFFOLD TEST PASSED ━━━${NC}"
  echo "  Copier render produces a valid service structure."
  [[ "$SMOKE_REQUESTED" == "1" ]] && echo "  Smoke chain: install + snapshot + pytest all green."
  exit 0
else
  echo -e "${RED}━━━ SCAFFOLD TEST FAILED ━━━${NC}"
  echo "  $FAILURES validation(s) failed."
  [[ "$KEEP_TEMP" == "false" ]] && echo "  Re-run with --keep to inspect the failing scaffold."
  echo "  Temp dir: $TEMP_ROOT"
  exit 1
fi
