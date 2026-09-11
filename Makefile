# Makefile — ML-MLOps Production Template (root)
# For contributors working on the template itself.
# For the per-service Makefile (train, serve, build, deploy), see templates/service/Makefile.
#
# Usage:
#   make help              # Show all targets
#   make install-dev       # Set up contributor environment
#   make lint-all          # Lint all Python in templates/ and examples/
#   make format-all        # Auto-format all Python
#   make validate-templates# Validate K8s + Terraform + Python templates
#   make demo-minimal      # Run the fraud detection example end-to-end
#   make test-examples     # Run example regression tests

.PHONY: help install-dev lint-all format-all validate-templates \
        validate-agentic bootstrap smoke verify \
        mcp-check mcp-doctor mcp-render-docs \
        report-validate report-example \
        demo-minimal test-examples clean

# Colors
RED    := \033[0;31m
GREEN  := \033[0;32m
YELLOW := \033[1;33m
BLUE   := \033[0;34m
NC     := \033[0m

help: ## Show this help message
	@echo "$(GREEN)ML-MLOps Template — Contributor Commands:$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "$(YELLOW)%-22s$(NC) %s\n", $$1, $$2}'

# ═══════════════════════════════════════════════
# Setup
# ═══════════════════════════════════════════════

install-dev: ## Install contributor tools + pre-commit hooks (idempotent)
	@echo "$(GREEN)Installing contributor tools...$(NC)"
	pip install black isort flake8 mypy bandit pre-commit
	pip install -r examples/minimal/requirements.txt
	@bash scripts/dev-setup.sh
	@echo "$(GREEN)✓ Contributor environment ready$(NC)"

verify-hooks: ## Verify pre-commit hooks are actually installed in .git/hooks/
	@hooks_dir="$$(git rev-parse --git-path hooks)" ; \
	for h in pre-commit ; do \
	  if [ ! -f "$$hooks_dir/$$h" ] || ! grep -q pre-commit "$$hooks_dir/$$h" 2>/dev/null ; then \
	    echo "$(GREEN)MISSING:$(NC) $$hooks_dir/$$h" ; \
	    echo "  Run: make install-dev   (or: bash scripts/dev-setup.sh)" ; \
	    exit 1 ; \
	  fi ; \
	  echo "✓ $$hooks_dir/$$h" ; \
	done

bootstrap: ## One-command setup: detect OS, install deps, configure MCPs, run example
	@bash scripts/bootstrap.sh

bootstrap-check: ## Verify required tooling is installed (no install, no changes)
	@bash scripts/bootstrap.sh --check-only

setup-github-preview: ## Preview GitHub Rulesets payloads (main + tags) without applying — ADR-026
	@bash scripts/setup_branch_protection.sh --dry-run

setup-github: ## Apply ADR-026 branch protection + tag immutability rulesets (idempotent, CONSULT-class)
	@bash scripts/setup_branch_protection.sh

setup-github-check: ## Verify ADR-026 rulesets exist and are active — exits non-zero if drift
	@bash scripts/setup_branch_protection.sh --check

# ═══════════════════════════════════════════════
# Quality
# ═══════════════════════════════════════════════

lint-all: ## Lint all Python (templates/ + examples/)
	@echo "$(GREEN)Running flake8...$(NC)"
	flake8 --max-line-length=120 --extend-ignore=E203,W503 \
		templates/service/ examples/minimal/
	@echo "$(GREEN)Running black check...$(NC)"
	black --check --line-length=120 \
		templates/service/ examples/minimal/
	@echo "$(GREEN)✓ Lint passed$(NC)"

format-all: ## Auto-format all Python (templates/ + examples/)
	@echo "$(GREEN)Formatting...$(NC)"
	black --line-length=120 templates/service/ examples/minimal/
	isort --profile=black --line-length=120 \
		templates/service/ examples/minimal/
	@echo "$(GREEN)✓ Format done$(NC)"

# ═══════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════

validate-k8s: ## Validate K8s manifests with kustomize
	@echo "$(GREEN)Validating K8s manifests...$(NC)"
	kustomize build templates/service/k8s/base/ > /dev/null
	@echo "$(GREEN)✓ K8s valid$(NC)"

validate-tf: ## Validate Terraform syntax (no init — backends are partial config per env)
	@echo "$(GREEN)Validating Terraform (GCP + AWS)...$(NC)"
	@if command -v terraform >/dev/null 2>&1; then \
		terraform -chdir=templates/service/infra/terraform/gcp init -backend=false -input=false >/dev/null && \
		terraform -chdir=templates/service/infra/terraform/gcp validate && \
		terraform -chdir=templates/service/infra/terraform/aws init -backend=false -input=false >/dev/null && \
		terraform -chdir=templates/service/infra/terraform/aws validate && \
		echo "$(GREEN)✓ Terraform valid$(NC)"; \
	else \
		echo "$(YELLOW)⚠ terraform not installed, skipping$(NC)"; \
	fi

validate-agentic: ## Validate agentic system (rules, skills, workflows, AGENTS.md refs)
	@echo "$(GREEN)Validating agentic system...$(NC)"
	python3 scripts/validate_agentic.py
	@echo "$(GREEN)Checking generated agentic adapters...$(NC)"
	python3 scripts/sync_agentic_adapters.py --check
	@echo "$(GREEN)Validating agentic manifest + context layer (ADR-023)...$(NC)"
	python3 scripts/validate_agentic_manifest.py --strict

agentic-sync: ## Regenerate thin Cursor/Claude/Codex adapter pointers from manifest
	@echo "$(GREEN)Syncing agentic adapters from manifest...$(NC)"
	python3 scripts/sync_agentic_adapters.py

mcp-check: ## Read-only pass/fail check of MCP registry + surface capabilities (ADR-023 F4)
	@echo "$(GREEN)Validating MCP portability registry...$(NC)"
	python3 scripts/mcp_doctor.py --mode check

mcp-doctor: ## Long-form MCP registry report (install matrix, skill coverage, orphans)
	@echo "$(GREEN)MCP doctor — full report$(NC)"
	python3 scripts/mcp_doctor.py --mode doctor

mcp-render-docs: ## Regenerate docs/agentic/mcp-portability.md from the registry YAMLs
	@echo "$(GREEN)Rendering MCP portability docs...$(NC)"
	python3 scripts/mcp_doctor.py --mode render-docs

report-validate: ## Validate a report JSON against the schema. Usage: make report-validate FILE=ops/reports/release/<id>.json
	@if [ -z "$(FILE)" ]; then echo "$(RED)error: FILE=<path> required$(NC)"; exit 2; fi
	python3 scripts/generate_report.py validate $(FILE)

report-example: ## Print a syntactically valid example report. Usage: make report-example TYPE={release|drift|training|incident}
	@if [ -z "$(TYPE)" ]; then echo "$(RED)error: TYPE=<release|drift|training|incident> required$(NC)"; exit 2; fi
	python3 scripts/generate_report.py example $(TYPE)

test-scaffold: ## End-to-end test: runs copier copy via new-service.sh and validates output
	@echo "$(GREEN)Testing Copier scaffold end-to-end...$(NC)"
	@bash scripts/test_scaffold.sh

smoke: test-scaffold ## Alias of test-scaffold. Run before push when touching templates/service/ or copier.yml. CI runs the same script in pr-smoke-lane.yml; this is the local on-demand entry point (R5-L4).

eda-validate: ## Validate EDA pipeline: syntax + run against example dataset
	@echo "$(GREEN)Validating EDA pipeline...$(NC)"
	python3 -c "import ast; ast.parse(open('templates/service/eda/eda_pipeline.py').read())"
	python3 -m py_compile templates/service/eda/eda_pipeline.py
	@echo "$(GREEN)✓ EDA pipeline syntactically valid$(NC)"

validate-templates: lint-all validate-k8s validate-agentic test-scaffold eda-validate ## Validate all templates (lint + K8s + agentic + scaffold + EDA)
	@echo "$(GREEN)✓ All templates validated$(NC)"

# ═══════════════════════════════════════════════
# Gate suite — the CI contract, runnable locally
# ═══════════════════════════════════════════════
# Every check below is a job in validate-templates.yml. Before this target
# existed the only way to run the full set was to push and read CI, which
# is how three separate breakages in one afternoon (Link Check, vendored
# byte-identity, evidence-check) reached the remote instead of dying on the
# contributor's machine.
#
# Deliberately excludes the slow ones: `make smoke` (scaffolder E2E, ~2.5
# min) and the pytest suites. This target is the fast contract — every gate
# here is sub-second — so it is cheap enough to sit on pre-push.
#
# Runs ALL gates and reports every failure, rather than stopping at the
# first. A contributor who broke three things should learn that in one run.

GATES := \
	check_doc_coherence \
	check_doc_path_refs \
	check_cicd_template_drift \
	check_vendored_runtime_drift \
	check_common_utils_drift \
	check_dashboard_inventory \
	check_baselines_expiry \
	check_gitleaks_pin \
	check_adopter_scaffold_ref \
	check_service_adr_references \
	check_template_render_safety \
	check_payload_test_scope \
	check_markdownlint_parity \
	check_test_clock_isolation \
	check_dependency_pin_coherence \
	check_dependency_partition \
	check_config_is_read \
	validate_agentic

verify: ## Run every fast CI gate locally (the pre-push contract). Slow E2E lives in `make smoke`.
	@echo "$(GREEN)Running the CI gate suite...$(NC)"
	@failed=""; \
	for gate in $(GATES); do \
		if python3 scripts/$$gate.py >/tmp/$$gate.out 2>&1; then \
			printf '  %-34s PASS\n' "$$gate"; \
		else \
			printf '  %-34s FAIL\n' "$$gate"; \
			failed="$$failed $$gate"; \
		fi; \
	done; \
	if python3 scripts/sync_agentic_adapters.py --check >/tmp/adapter_sync.out 2>&1; then \
		printf '  %-34s PASS\n' "adapter-sync"; \
	else \
		printf '  %-34s FAIL\n' "adapter-sync"; \
		failed="$$failed sync_agentic_adapters"; \
	fi; \
	if python3 scripts/check_control_claims.py >/tmp/control_claims.out 2>&1; then \
		printf '  %-34s PASS\n' "control-claims"; \
	else \
		printf '  %-34s FAIL\n' "control-claims"; \
		failed="$$failed check_control_claims"; \
	fi; \
	if python3 scripts/generate_adr_index.py --check >/tmp/adr_index.out 2>&1; then \
		printf '  %-34s PASS\n' "adr-index"; \
	else \
		printf '  %-34s FAIL\n' "adr-index"; \
		failed="$$failed generate_adr_index"; \
	fi; \
	if [ -n "$$failed" ]; then \
		echo "$(RED)✗ failing gates:$$failed$(NC)"; \
		for g in $$failed; do echo "--- $$g ---"; tail -20 /tmp/$$g.out; done; \
		exit 1; \
	fi; \
	echo "$(GREEN)✓ every gate green — safe to push$(NC)"

# ═══════════════════════════════════════════════
# Example (Fraud Detection)
# ═══════════════════════════════════════════════

demo-install: ## Install example dependencies
	pip install -r examples/minimal/requirements.txt

demo-train: ## Train the fraud detection example model
	@echo "$(GREEN)Training fraud detection model...$(NC)"
	python examples/minimal/train.py
	@echo "$(GREEN)✓ Model trained$(NC)"

demo-serve: ## Serve the fraud detection example API
	@echo "$(GREEN)Starting example API on :8000...$(NC)"
	@echo "$(YELLOW)Test with: curl -X POST http://localhost:8000/predict -H 'Content-Type: application/json' -d '{\"amount\": 150.0, \"hour\": 2, \"is_foreign\": true, \"merchant_risk\": 0.8, \"distance_from_home\": 45.0}'$(NC)"
	cd examples/minimal && uvicorn serve:app --host 0.0.0.0 --port 8000

demo-minimal: demo-install demo-train ## Run minimal example end-to-end (train + test + drift)
	@echo "$(GREEN)Running full example pipeline...$(NC)"
	cd examples/minimal && pytest test_service.py -v --tb=short
	cd examples/minimal && python drift_check.py
	@echo "$(GREEN)✓ Example pipeline complete$(NC)"

test-examples: demo-install ## Run all example regression tests
	@echo "$(GREEN)Running example tests...$(NC)"
	cd examples/minimal && python train.py
	cd examples/minimal && pytest test_service.py -v --tb=short
	@echo "$(GREEN)✓ Example tests passed$(NC)"

# ═══════════════════════════════════════════════
# Scaffolding
# ═══════════════════════════════════════════════

new-service: ## Scaffold a new service via Copier: make new-service NAME=FraudDetection SLUG=fraud_detection
	@if [ -z "$(NAME)" ] || [ -z "$(SLUG)" ]; then \
		echo "$(RED)Usage: make new-service NAME=FraudDetection SLUG=fraud_detection$(NC)"; \
		exit 1; \
	fi
	bash templates/scripts/new-service.sh $(NAME) $(SLUG)

# ═══════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════

clean: ## Clean Python cache files
	@echo "$(YELLOW)Cleaning...$(NC)"
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✓ Clean$(NC)"

.DEFAULT_GOAL := help
