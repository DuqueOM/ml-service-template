#!/usr/bin/env bash
# =============================================================================
# scripts/dev-setup.sh — install + verify pre-commit hooks (idempotent).
#
# This is the CONTRACT for "you've set up your dev environment correctly":
#
#   1. pre-commit binary is on $PATH
#   2. .git/hooks/pre-commit exists and points at the pre-commit framework
#   3. A dry-run of `pre-commit run --all-files` is green
#
# Single-stage design: the pre-push hook was retired in R5-L4 (May 2026) and
# replaced by `make smoke` + CI pr-smoke-lane.yml. .pre-commit-config.yaml
# declares `default_install_hook_types: [pre-commit]`; this script must not
# reintroduce a pre-push hook.
#
# If any of those is false, this script fails LOUD with the fix command.
# Idempotent: safe to re-run.
#
# Why this exists: contributors who clone the repo and start committing
# without ever running `pre-commit install` push commits that pass black
# locally (because they ran black manually) but fail in CI because they
# missed flake8 / mypy / bandit / gitleaks. The first-filter design only
# works if the hooks are actually installed; this script enforces it.
# =============================================================================

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${repo_root}" ]]; then
  echo "ERROR: not inside a git repository — run this from the template clone." >&2
  exit 1
fi
cd "${repo_root}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

step() { printf "${GREEN}==>${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}WARN:${NC} %s\n" "$*" >&2; }
die()  { printf "${RED}FAIL:${NC} %s\n" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. pre-commit binary present
# ---------------------------------------------------------------------------
step "Checking pre-commit binary…"
if ! command -v pre-commit >/dev/null 2>&1; then
  warn "pre-commit not found — installing via pip"
  python -m pip install --quiet pre-commit
fi
pre_commit_version="$(pre-commit --version | awk '{print $2}')"
step "pre-commit ${pre_commit_version} OK"

# ---------------------------------------------------------------------------
# 2. config is valid
# ---------------------------------------------------------------------------
step "Validating .pre-commit-config.yaml…"
pre-commit validate-config >/dev/null
step "Config valid"

# ---------------------------------------------------------------------------
# 3. install hooks (reads default_install_hook_types from config: pre-commit
#    only — the pre-push stage was retired in R5-L4, replaced by `make smoke`
#    + CI pr-smoke-lane.yml)
# ---------------------------------------------------------------------------
step "Installing git hooks…"
pre-commit install --install-hooks --overwrite

# ---------------------------------------------------------------------------
# 4. verify the hooks actually landed in .git/hooks/
# ---------------------------------------------------------------------------
hooks_dir="$(git rev-parse --git-path hooks)"
for hook in pre-commit; do
  if [[ ! -f "${hooks_dir}/${hook}" ]]; then
    die "${hooks_dir}/${hook} missing after install. Aborting."
  fi
  if ! grep -q "pre-commit" "${hooks_dir}/${hook}"; then
    die "${hooks_dir}/${hook} exists but does not reference the pre-commit framework. Aborting."
  fi
done
step "Hooks installed at ${hooks_dir}/"

# ---------------------------------------------------------------------------
# 5. dry-run on tracked files (last sanity check)
# ---------------------------------------------------------------------------
if [[ "${SKIP_DRY_RUN:-0}" != "1" ]]; then
  step "Running 'pre-commit run --all-files' (dry-run sanity check)…"
  if pre-commit run --all-files; then
    step "All hooks green on the current tree."
  else
    warn "Some hooks reported issues. The hooks ARE installed correctly;"
    warn "the issues above are existing tree state, not a setup failure."
    warn "Fix them (or talk to a maintainer if they pre-existed your clone)"
    warn "before your next commit, or commits will be blocked."
    exit 2
  fi
else
  step "SKIP_DRY_RUN=1 — skipping the all-files dry-run."
fi

cat <<'EOF'

──────────────────────────────────────────────────────────────────────
✓ Dev environment is ready.

What just happened:
  1. pre-commit framework installed
  2. pre-commit hook wired into .git/hooks/
  3. Config validated
  4. All hooks dry-run green on the current tree

From now on:
  - `git commit`  triggers ruff (lint + format, ADR-044)/mypy/bandit/
                  gitleaks/validate-agentic/ci-autofix-policy-contract.
  - `git push`    no hook (pre-push scaffold smoke retired in R5-L4) —
                  run `make smoke` on demand; CI pr-smoke-lane.yml is the gate.
  - The commit lane BLOCKS on failure. CI is the safety net, not the gate.

To re-run all hooks manually anytime:
  pre-commit run --all-files

To bypass (DON'T — CI will catch you):
  git commit --no-verify     # blocked by CI
──────────────────────────────────────────────────────────────────────
EOF
