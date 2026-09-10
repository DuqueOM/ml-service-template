#!/usr/bin/env bash
# =============================================================================
# new-service.sh — Thin wrapper around Copier for scaffolding a new ML service
# =============================================================================
# Usage:
#   ./templates/scripts/new-service.sh FraudDetector fraud_detector
#   ./templates/scripts/new-service.sh FraudDetector fraud_detector my-org my-repo
#   ./templates/scripts/new-service.sh FraudDetector fraud_detector my-org my-repo staging
#
# This script delegates to `copier copy` (ADR-030). The Copier template lives
# at the repository root (`copier.yml` with `_subdirectory: templates/service`).
# Copier handles:
#   - Token substitution ({@ service_slug @} → fraud_detector, etc.)
#   - Path renaming (src/{@ service_slug @}/ → src/fraud_detector/)
#   - Post-gen tasks (sync_agentic_adapters.py + validate_agentic_manifest.py)
#
# The old manual cp+sed scaffolder was replaced because Copier provides:
#   - Idempotent updates via `copier update` (ADR-030 §2)
#   - Jinja-native templating with collision-free {@ @} delimiters
#   - Structured answers in .copier-answers.yml for upgrade tracking
#
# Prerequisite: `pip install copier` (>= 9.0.0).
# =============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; exit 1; }

# --- Argument validation ---
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <ServiceName> <service_slug> [gh_org] [gh_repo] [profile]"
    echo ""
    echo "  ServiceName  — PascalCase name (e.g., FraudDetector)"
    echo "  service_slug — snake_case slug  (e.g., fraud_detector)"
    echo "  gh_org       — GitHub org/owner (optional, defaults to git remote)"
    echo "  gh_repo      — GitHub repo name (optional, defaults to kebab slug)"
    echo "  profile      — Stack profile: local|staging|prod (optional, default: local; ADR-033)"
    echo ""
    echo "Example:"
    echo "  $0 FraudDetector fraud_detector"
    echo "  $0 FraudDetector fraud_detector my-org fraud-detector"
    echo "  $0 FraudDetector fraud_detector my-org fraud-detector staging"
    exit 1
fi

SERVICE_NAME="$1"
SERVICE_SLUG="$2"
PROFILE="${5:-local}"
case "$PROFILE" in
    local|staging|prod) ;;
    *) error "profile must be one of: local, staging, prod (got: $PROFILE)" ;;
esac

# Resolve GitHub org/repo (same priority chain as the old scaffolder):
#   1. CLI args $3 ($4)
#   2. ML_TEMPLATE_ORG / ML_TEMPLATE_REPO env vars
#   3. `git remote get-url origin` parse (HTTPS or SSH)
#   4. Final fallback: YOUR_ORG / YOUR_REPO with a loud warning
GH_ORG="${3:-${ML_TEMPLATE_ORG:-}}"
GH_REPO="${4:-${ML_TEMPLATE_REPO:-}}"
if [[ -z "$GH_ORG" || -z "$GH_REPO" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
    if origin_url=$(git -C "$REPO_ROOT" config --get remote.origin.url 2>/dev/null); then
        slug=$(echo "$origin_url" | sed -E 's#^(https?://[^/]+/|git@[^:]+:)([^/]+/[^/]+?)(\.git)?$#\2#')
        if [[ "$slug" == */* ]]; then
            GH_ORG="${GH_ORG:-${slug%%/*}}"
            GH_REPO="${GH_REPO:-${slug##*/}}"
        fi
    fi
fi
GH_ORG="${GH_ORG:-YOUR_ORG}"
GH_REPO="${GH_REPO:-YOUR_REPO}"
if [[ "$GH_ORG" == "YOUR_ORG" || "$GH_REPO" == "YOUR_REPO" ]]; then
    warn "Could not resolve GitHub org/repo; substituted YOUR_ORG/YOUR_REPO."
    warn "Edit Kyverno policies before deploying to production OR re-run with: $0 $SERVICE_NAME $SERVICE_SLUG <org> <repo>"
fi

# Locate the template root (repo root containing copier.yml)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_DIR="$REPO_ROOT/$SERVICE_NAME"

if [[ -d "$TARGET_DIR" ]]; then
    error "Directory $TARGET_DIR already exists. Remove it first or choose a different name."
fi

# --- Check prerequisites ---
if ! python3 -m copier --version >/dev/null 2>&1; then
    error "Copier is not installed. Run: pip install copier>=9.0.0"
fi

info "Scaffolding $SERVICE_NAME ($SERVICE_SLUG) via Copier..."
info "Template: $REPO_ROOT (copier.yml _subdirectory: templates/service)"
info "Target:   $TARGET_DIR"
info "GitHub:   $GH_ORG/$GH_REPO"
info "Profile:  $PROFILE"

# --- Run Copier ---
# --defaults   : use default values for derived questions (service_name, service_kebab, etc.)
# --overwrite  : overwrite if target exists (safe — we checked above)
# --trust      : allow post-gen tasks (sync + validate)
# --vcs-ref HEAD : use the current working tree (includes uncommitted changes)
# --quiet      : suppress Copier's file-by-file output (post-gen sync is verbose enough)
python3 -m copier copy \
    --data "service_slug=$SERVICE_SLUG" \
    --data "gh_org=$GH_ORG" \
    --data "gh_repo=$GH_REPO" \
    --data "profile=$PROFILE" \
    --vcs-ref HEAD \
    --defaults \
    --overwrite \
    --trust \
    --quiet \
    "$REPO_ROOT" \
    "$TARGET_DIR"

# --- Create standard data directories (not in the template — adopter-specific) ---
mkdir -p "$TARGET_DIR/data/raw" \
         "$TARGET_DIR/data/processed" \
         "$TARGET_DIR/data/reference" \
         "$TARGET_DIR/data/production" \
         "$TARGET_DIR/data/validated" \
         "$TARGET_DIR/models" \
         "$TARGET_DIR/reports" \
         "$TARGET_DIR/eda/reports" \
         "$TARGET_DIR/eda/artifacts" \
         "$TARGET_DIR/eda/notebooks"

touch "$TARGET_DIR/data/raw/.gitkeep" \
      "$TARGET_DIR/data/processed/.gitkeep" \
      "$TARGET_DIR/data/reference/.gitkeep" \
      "$TARGET_DIR/data/production/.gitkeep" \
      "$TARGET_DIR/data/validated/.gitkeep" \
      "$TARGET_DIR/models/.gitkeep" \
      "$TARGET_DIR/reports/.gitkeep"

# --- Summary ---
echo ""
info "=== Scaffolding complete ==="
echo ""
echo "  Service:   $SERVICE_NAME"
echo "  Slug:      $SERVICE_SLUG"
echo "  Directory: $TARGET_DIR"
echo ""
echo "  Next steps:"
echo "    1. cd $TARGET_DIR"
echo "    2. Place your dataset: cp <your-data>.csv data/raw/"
echo "    3. Run EDA (produces baseline distributions + schema proposal):"
echo "         pip install -r eda/requirements.txt"
echo "         python -m eda.eda_pipeline --input data/raw/<file>.csv --target <col> --service-slug $SERVICE_SLUG"
echo "    4. Review eda/reports/04_leakage_audit.md (must show BLOCKED_FEATURES: [])"
echo "    5. Copy src/$SERVICE_SLUG/schema_proposal.py → schemas.py (review first)"
echo "    6. Edit src/$SERVICE_SLUG/training/features.py (consume 05_feature_proposals.yaml)"
echo "    7. Edit src/$SERVICE_SLUG/training/model.py"
echo "    8. Edit app/schemas.py with your API request/response"
echo "    9. pip install -r requirements-train.txt   # runtime + mlflow/optuna (ADR-049)"
echo "         (requirements.txt alone is the SERVING set — what the image installs)"
echo "   10. make train DATA=data/raw/<file>.csv"
echo "   11. make serve"
echo ""
info "Done."
