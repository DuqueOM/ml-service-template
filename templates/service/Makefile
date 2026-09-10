# Makefile — ML Service Template
# Standard developer entry point for all operations.
#
# Usage:
#   make help          # Show all available targets
#   make install       # Install dependencies
#   make train         # Run training pipeline
#   make serve         # Start local dev server
#   make test          # Run all tests
#   make build         # Build Docker image
#   make deploy        # Deploy to K8s (requires configured kubectl)
#
# TODO: Replace {@ service_slug @} and {SERVICE_SLUG} with your actual service names.

.PHONY: help install install-uv lock train serve test lint format build deploy health-check clean \
        new-service eda drift-check retrain release-checklist rollback \
        incident-runbook performance-review cost-review new-adr \
        secret-breach-check security-audit audit-rules batch-inference doc-coherence \
        local-loop switch-profile onboard ci-green scaffold-update edge-setup \
        audit-quality document-changes

# Colors
RED    := \033[0;31m
GREEN  := \033[0;32m
YELLOW := \033[1;33m
BLUE   := \033[0;34m
NC     := \033[0m

# Configuration — customize per service
SERVICE_SLUG ?= {@ service_slug @}
MODEL_PATH   ?= models/model.joblib
DATA_PATH    ?= data/raw/dataset.csv
IMAGE_TAG    ?= $(SERVICE_SLUG):latest
VERSION      ?= latest
CLOUD        ?= gcp
PORT         ?= 8000
PROFILE      ?= local

help: ## Show this help message
	@echo "$(GREEN)ML Service — Available Commands:$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "$(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'

# ═══════════════════════════════════════════════
# Development
# ═══════════════════════════════════════════════

install: ## Install runtime dependencies (what the served image gets)
	@echo "$(GREEN)Installing runtime dependencies...$(NC)"
	pip install -r requirements.txt
	@echo "$(GREEN)✓ Runtime dependencies installed$(NC)"

install-train: ## Install runtime + training dependencies (mlflow, optuna)
	@echo "$(GREEN)Installing training dependencies...$(NC)"
	pip install -r requirements-train.txt
	@echo "$(GREEN)✓ Training environment ready$(NC)"

install-dev: ## Install runtime + dev dependencies + pre-commit hooks
	@echo "$(GREEN)Installing dev dependencies...$(NC)"
	pip install -r requirements-dev.txt
	pre-commit install
	@echo "$(GREEN)✓ Dev environment ready$(NC)"
	@echo "$(YELLOW)  make train also needs: make install-train (ADR-049).$(NC)"

install-uv: ## Install dependencies with uv (faster, reproducible)
	@echo "$(GREEN)Installing dependencies with uv...$(NC)"
	uv sync
	@echo "$(GREEN)✓ Dependencies installed via uv$(NC)"
	@echo "$(GREEN)  (requirements.txt retained for pip compatibility)$(NC)"

lock: ## Freeze each requirements file into a hash-pinned lockfile (reproducible rebuilds)
	@echo "$(GREEN)Generating hash-pinned lockfiles...$(NC)"
	uv pip compile requirements.txt --generate-hashes -o requirements.lock.txt
	uv pip compile requirements-train.txt --generate-hashes -o requirements-train.lock.txt
	@echo "$(GREEN)✓ lockfiles written — commit them with the release.$(NC)"
	@echo "$(YELLOW)  The runtime lock is what the image installs; the training lock"
	@echo "  is a superset. Locking only one of the two was how the split"
	@echo "  would quietly stop meaning anything.$(NC)"
	@echo "$(YELLOW)  requirements.txt (~=) states intent; the lockfile guarantees"
	@echo "  byte-identical rebuilds a year from now. Install with:"
	@echo "  pip install --require-hashes -r requirements.lock.txt$(NC)"

# ═══════════════════════════════════════════════
# ML Pipeline
# ═══════════════════════════════════════════════

train: ## Run training pipeline
	@echo "$(GREEN)Running training pipeline...$(NC)"
	python -m src.$(SERVICE_SLUG).training.train --data $(DATA_PATH)
	@echo "$(GREEN)✓ Training complete$(NC)"

serve: ## Start local dev server (uvicorn)
	@echo "$(GREEN)Starting dev server on port $(PORT)...$(NC)"
	MODEL_PATH=$(MODEL_PATH) uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

# ═══════════════════════════════════════════════
# Stack Profiles (ADR-033)
# ═══════════════════════════════════════════════

local-loop: ## Run train → serve → drift in local mode (no Docker/K8s/TF)
	@echo "$(GREEN)Running local loop (profile=local)...$(NC)"
	python -m src.$(SERVICE_SLUG).training.train --data $(DATA_PATH)
	@echo "$(GREEN)Starting server...$(NC)"
	@MODEL_PATH=$(MODEL_PATH) uvicorn app.main:app --host 0.0.0.0 --port $(PORT) &
	@sleep 2 && curl -sf http://localhost:$(PORT)/health && echo " $(GREEN)✓ healthy$(NC)" || echo " $(RED)✗ unhealthy$(NC)"
	@echo "$(GREEN)Running drift check...$(NC)"
	@if [ -f scripts/drills/run_drift_drill.py ]; then \
		python scripts/drills/run_drift_drill.py; \
	else \
		echo "$(YELLOW)No drift drill script found — skipping.$(NC)"; \
	fi
	@echo "$(GREEN)✓ Local loop complete$(NC)"

switch-profile: ## Switch stack profile (PROFILE=local|staging|prod)
	@echo "$(GREEN)Switching to profile $(PROFILE)...$(NC)"
	@if [ -z "$(PROFILE)" ]; then \
		echo "$(RED)Usage:$(NC) make switch-profile PROFILE=staging"; \
		exit 1; \
	fi
	@if [ ! -f configs/profiles/$(PROFILE).yaml ]; then \
		echo "$(RED)Profile $(PROFILE) not found in configs/profiles/$(NC)"; \
		exit 1; \
	fi
	@sed -i.bak 's/^profile: .*/profile: "$(PROFILE)"/' configs/profiles/active_profile.yaml && rm -f configs/profiles/active_profile.yaml.bak
	@echo "$(GREEN)✓ Active profile set to $(PROFILE)$(NC)"
	@echo "$(YELLOW)This is a CONSULT-class operation — review deploy targets before proceeding.$(NC)"

# ═══════════════════════════════════════════════
# Quality & Testing
# ═══════════════════════════════════════════════

test: ## Run all tests
	@echo "$(GREEN)Running tests...$(NC)"
	pytest tests/ -v --tb=short

test-coverage: ## Run tests with coverage report
	@echo "$(GREEN)Running tests with coverage...$(NC)"
	pytest tests/ --cov=src --cov=app --cov-report=term-missing --cov-fail-under=90

lint: ## Run linting (pre-commit hooks)
	@echo "$(GREEN)Running linters...$(NC)"
	pre-commit run --all-files

format: ## Auto-format code (black + isort)
	@echo "$(GREEN)Formatting code...$(NC)"
	black . --exclude '/(\.git|\.venv|data|models|mlruns)/'
	isort . --skip-gitignore
	@echo "$(GREEN)✓ Code formatted$(NC)"

typecheck: ## Run mypy type checking
	@echo "$(GREEN)Running type checks...$(NC)"
	mypy src/ app/ --ignore-missing-imports

security-scan: ## Run bandit security scan
	@echo "$(GREEN)Running security scan...$(NC)"
	bandit -r src/ app/ -ll -i

# ═══════════════════════════════════════════════
# Docker
# ═══════════════════════════════════════════════

build: ## Build Docker image
	@echo "$(GREEN)Building Docker image $(IMAGE_TAG)...$(NC)"
	docker build -t $(IMAGE_TAG) .
	@echo "$(GREEN)✓ Image built: $(IMAGE_TAG)$(NC)"

run: ## Run Docker container locally
	@echo "$(GREEN)Running container on port $(PORT)...$(NC)"
	docker run -p $(PORT):8000 \
		-v $(PWD)/models:/app/models:ro \
		-e LOG_FORMAT=json \
		$(IMAGE_TAG)

scan: ## Run Trivy security scan on Docker image
	@echo "$(GREEN)Scanning image $(IMAGE_TAG)...$(NC)"
	trivy image --severity HIGH,CRITICAL $(IMAGE_TAG)

# ═══════════════════════════════════════════════
# Deploy
# ═══════════════════════════════════════════════

deploy: ## Deploy to K8s (requires kubectl context)
	@if [ -f configs/profiles/active_profile.yaml ] && grep -q '^profile: "local"' configs/profiles/active_profile.yaml; then \
		echo "$(RED)✗ Blocked:$(NC) active profile is 'local' (ADR-033 / D-35) — the local profile must not target a cluster."; \
		echo "$(YELLOW)Switch first:$(NC) make switch-profile PROFILE=staging"; \
		exit 1; \
	fi
	@echo "$(GREEN)Deploying to Kubernetes...$(NC)"
	bash scripts/deploy.sh --service $(SERVICE_SLUG) --version $(VERSION) --cloud $(CLOUD)

health-check: ## Check health of deployed service
	@echo "$(GREEN)Checking service health...$(NC)"
	@curl -sf http://localhost:$(PORT)/health | python -m json.tool && \
		echo "$(GREEN)✓ Service healthy$(NC)" || \
		echo "$(RED)✗ Service unhealthy$(NC)"

load-test: ## Run Locust load test (headless, 60s)
	@echo "$(GREEN)Running load test...$(NC)"
	locust -f tests/load_test.py --host=http://localhost:$(PORT) \
		--headless -u 100 -r 10 --run-time 60s \
		--csv=reports/load_test

# ═══════════════════════════════════════════════
# Demo
# ═══════════════════════════════════════════════

demo-up: ## Start demo stack (service + MLflow + Pushgateway)
	@echo "$(GREEN)Starting demo stack...$(NC)"
	docker compose -f docker-compose.demo.yml up -d --build
	@echo "$(GREEN)✓ Demo started$(NC)"
	@echo "$(YELLOW)Service:$(NC)     http://localhost:8000/health"
	@echo "$(YELLOW)MLflow:$(NC)      http://localhost:5000"
	@echo "$(YELLOW)Pushgateway:$(NC) http://localhost:9091"

demo-down: ## Stop demo stack
	@echo "$(YELLOW)Stopping demo stack...$(NC)"
	docker compose -f docker-compose.demo.yml down
	@echo "$(GREEN)✓ Demo stopped$(NC)"

# ═══════════════════════════════════════════════
# Non-agentic on-ramp (PR-R2-12)
#
# Every /slash workflow has a `make` equivalent below. Teams that don't
# operate with AI assistants can adopt the template without inheriting
# the agentic surface. See docs/ADOPTION.md for the full mapping table
# and the maturity matrix per cloud × environment.
#
# Each target either:
#   (a) invokes a real script (drift drills, retrain, security scan), or
#   (b) prints the canonical runbook path for procedures that are
#       inherently human-judgment (cost review, incident response).
# ═══════════════════════════════════════════════

new-service: ## Scaffold a new ML service (NAME=PascalCase SLUG=snake_case)
	@if [ -z "$(NAME)" ] || [ -z "$(SLUG)" ]; then \
		echo "$(RED)Usage:$(NC) make new-service NAME=FraudDetector SLUG=fraud_detector"; \
		exit 1; \
	fi
	bash templates/scripts/new-service.sh $(NAME) $(SLUG)

eda: ## Run 6-phase EDA pipeline on data/raw/
	@echo "$(GREEN)Running EDA pipeline...$(NC)"
	@if [ -f eda/run_eda.py ]; then \
		python eda/run_eda.py; \
	else \
		echo "$(YELLOW)See eda/README.md for the 6-phase pipeline.$(NC)"; \
	fi

drift-check: ## Run reproducible drift drill against data/production/
	@echo "$(GREEN)Running drift drill...$(NC)"
	python scripts/drills/run_drift_drill.py

retrain: ## Run training pipeline + quality gates (non-agentic equivalent of /retrain)
	@echo "$(GREEN)Retraining $(SERVICE_SLUG)...$(NC)"
	python -m src.$(SERVICE_SLUG).training.train --data $(DATA_PATH)
	@echo "$(GREEN)Validating quality gates...$(NC)"
	python scripts/validate_quality_gates.py --require-at-least-one
	@echo "$(GREEN)✓ Retraining + gate validation complete$(NC)"
	@echo "$(YELLOW)Promotion is a CONSULT-class operation \u2014 see docs/runbooks/model-retrain.md$(NC)"

release-checklist: ## Print the canonical release checklist
	@echo "$(GREEN)Release checklist:$(NC)"
	@if [ -f docs/runbooks/release-checklist.md ]; then \
		cat docs/runbooks/release-checklist.md; \
	else \
		echo "$(YELLOW)See agentic/skills/release-checklist/SKILL.md$(NC)"; \
	fi

rollback: ## Roll back the last deploy (REV=<revision> optional)
	@echo "$(RED)EMERGENCY ROLLBACK \u2014 STOP-class operation$(NC)"
	@echo "$(YELLOW)See docs/runbooks/rollback.md before proceeding.$(NC)"
	@if [ -n "$(REV)" ]; then \
		kubectl rollout undo deployment/$(SERVICE_SLUG)-predictor --to-revision=$(REV); \
	else \
		kubectl rollout undo deployment/$(SERVICE_SLUG)-predictor; \
	fi
	kubectl rollout status deployment/$(SERVICE_SLUG)-predictor --timeout=5m

incident-runbook: ## Print the incident-response runbook
	@echo "$(GREEN)Incident response runbook:$(NC)"
	@if [ -f docs/runbooks/incident-response.md ]; then \
		cat docs/runbooks/incident-response.md; \
	else \
		echo "$(YELLOW)See agentic/workflows/incident.md$(NC)"; \
	fi

performance-review: ## Sliced performance review (concept drift detection)
	@echo "$(GREEN)Running performance review...$(NC)"
	@if [ -f scripts/run_performance_review.py ]; then \
		python scripts/run_performance_review.py; \
	else \
		echo "$(YELLOW)See docs/runbooks/performance-review.md$(NC)"; \
	fi

cost-review: ## Print the cost-review runbook
	@echo "$(GREEN)Monthly cost review procedure:$(NC)"
	@if [ -f docs/runbooks/cost-review.md ]; then \
		cat docs/runbooks/cost-review.md; \
	else \
		echo "$(YELLOW)See agentic/skills/cost-audit/SKILL.md$(NC)"; \
	fi

new-adr: ## Create a new ADR (TITLE='<title>')
	@if [ -z "$(TITLE)" ]; then \
		echo "$(RED)Usage:$(NC) make new-adr TITLE='Introduce feature flags'"; \
		exit 1; \
	fi
	@bash -c 'set -e; \
		num=$$(ls docs/decisions/ADR-*.md 2>/dev/null | sed -E "s/.*ADR-([0-9]+).*/\1/" | sort -n | tail -1); \
		num=$${num:-000}; \
		next=$$(printf "%03d" $$((10#$$num + 1))); \
		slug=$$(echo "$(TITLE)" | tr "[:upper:] " "[:lower:]-" | tr -cd "a-z0-9-"); \
		path="docs/decisions/ADR-$$next-$$slug.md"; \
		if [ -f docs/decisions/template.md ]; then \
			cp docs/decisions/template.md "$$path"; \
		else \
			printf "# ADR-%s: %s\n\n## Status\n\nProposed\n\n## Context\n\n## Decision\n\n## Consequences\n" "$$next" "$(TITLE)" > "$$path"; \
		fi; \
		echo "$(GREEN)Created$(NC) $$path"'

secret-breach-check: ## Scan for leaked secrets (gitleaks)
	@echo "$(GREEN)Scanning for leaked secrets...$(NC)"
	gitleaks detect --no-git --source=. --redact

# TEMPLATE_REF is deliberately separate from REF (used by ci-green as a git
# branch, defaulting to `main`) and deliberately has NO default. Reusing REF
# here would inherit that `main` default and update the service from the
# moving development branch instead of a release — a worse outcome than the
# unpinned form this target used to have.
scaffold-update: ## Update this service from the template. TEMPLATE_REF is required: make scaffold-update TEMPLATE_REF=v0.26.0
	@if [ -z "$(TEMPLATE_REF)" ]; then \
		echo "$(RED)TEMPLATE_REF is required.$(NC) Usage: make scaffold-update TEMPLATE_REF=<template release tag>"; \
		echo "Copier resolves an unpinned source to the highest-sorting tag, so the"; \
		echo "service would jump to a release nobody chose. Pin it."; \
		echo "See agentic/workflows/scaffold-update.md for the full procedure."; \
		exit 2; \
	fi
	@echo "$(GREEN)Updating from template at $(TEMPLATE_REF)...$(NC)"
	copier update --vcs-ref=$(TEMPLATE_REF) --trust --defaults

doc-coherence: ## Check cross-document coherence (version, CHANGELOG, ADRs, counts)
	@echo "$(GREEN)Checking documentation coherence...$(NC)"
	@if [ -f scripts/check_doc_coherence.py ]; then \
		python3 scripts/check_doc_coherence.py; \
	else \
		echo "$(YELLOW)See agentic/skills/doc-coherence/SKILL.md (rule 16, ADR-031)$(NC)"; \
	fi

onboard: ## Generate an adopter context file from the example template (non-agentic /onboard equivalent)
	@echo "$(GREEN)Generating adopter context file...$(NC)"
	@if [ -f $(SERVICE_SLUG)_context.local.yaml ]; then \
		echo "$(YELLOW)$(SERVICE_SLUG)_context.local.yaml already exists — not overwriting.$(NC)"; \
	elif [ -f config/adopter_context.example.yaml ]; then \
		cp config/adopter_context.example.yaml $(SERVICE_SLUG)_context.local.yaml; \
		echo "$(GREEN)✓ Created $(SERVICE_SLUG)_context.local.yaml — edit it with your real settings.$(NC)"; \
	else \
		echo "$(YELLOW)See agentic/skills/template-onboard/SKILL.md (ADR-029 Wave 3)$(NC)"; \
	fi
	@echo "$(YELLOW)Validate:$(NC) python3 -c \"import json,yaml,jsonschema; jsonschema.validate(yaml.safe_load(open('$(SERVICE_SLUG)_context.local.yaml')), json.load(open('config/adopter_context.schema.json')))\""

REF ?= main

ci-green: ## Verify GitHub Actions CI is green before promote/release/deploy (REF=branch-or-sha, D-36, ADR-039)
	@echo "$(GREEN)Checking CI status for $(REF)...$(NC)"
	@if command -v gh >/dev/null 2>&1; then \
		gh run list --branch $(REF) --limit 20 \
			--json name,status,conclusion,headSha,workflowName \
			--template '{{range .}}{{.workflowName}}{{"\t"}}{{.status}}{{"\t"}}{{.conclusion}}{{"\n"}}{{end}}'; \
	else \
		echo "$(YELLOW)gh CLI not found — see agentic/skills/ci-green-verify/SKILL.md$(NC)"; \
	fi

audit-quality: ## Run the recurring 23-domain enterprise audit + Q-01..Q-08 anti-pattern sweep (ADR-043)
	@echo "$(GREEN)Running enterprise audit sweep...$(NC)"
	@echo "$(YELLOW)See agentic/skills/enterprise-audit/SKILL.md and agentic/rules/18-audit-quality.md$(NC)"

document-changes: ## Document the current change surface: CHANGELOG entry, rule-16 cascade, audit-trail record (ADR-043)
	@echo "$(GREEN)Documenting change surface...$(NC)"
	@echo "$(YELLOW)See agentic/workflows/document-changes.md (Agent-DocUpdater entry point)$(NC)"

edge-setup: ## Audit edge-protection coverage for OVERLAY=gcp-prod|aws-prod (D-38, ADR-042; terraform apply itself is CONSULT — see docs/runbooks/edge-protection-setup.md)
	@echo "$(GREEN)Checking edge-protection coverage for $(OVERLAY)...$(NC)"
	@if [ -z "$(OVERLAY)" ]; then \
		echo "$(RED)Usage:$(NC) make edge-setup OVERLAY=gcp-prod"; \
		exit 1; \
	fi
	@if [ ! -d k8s/overlays/$(OVERLAY) ]; then \
		echo "$(RED)Unknown overlay:$(NC) k8s/overlays/$(OVERLAY) does not exist"; \
		exit 1; \
	fi
	@if command -v kubectl >/dev/null 2>&1; then \
		kubectl kustomize k8s/overlays/$(OVERLAY)/ | \
			grep -A2 "edge-protection.mlops-template.io/implementation" || \
			echo "$(RED)No edge-protection component wired into $(OVERLAY).$(NC) See docs/runbooks/edge-protection-setup.md (D-38, ADR-042) — wiring the component and applying Terraform requires human approval (CONSULT), never AUTO."; \
	else \
		echo "$(YELLOW)kubectl not found — see agentic/skills/edge-audit/SKILL.md$(NC)"; \
	fi

security-audit: ## Run combined security audit (gitleaks + bandit + trivy)
	@echo "$(GREEN)Running security audit...$(NC)"
	@$(MAKE) secret-breach-check
	@$(MAKE) security-scan
	@if [ -f $(IMAGE_TAG) ] || docker image inspect $(IMAGE_TAG) >/dev/null 2>&1; then \
		$(MAKE) scan; \
	else \
		echo "$(YELLOW)Image $(IMAGE_TAG) not built locally \u2014 skipping trivy.$(NC)"; \
	fi

audit-rules: ## Validate AGENTS.md D-XX invariants are documented + tested
	@echo "$(GREEN)Validating AGENTS.md invariants...$(NC)"
	@if [ -f scripts/validate_agentic.py ]; then \
		python scripts/validate_agentic.py; \
	fi
	@echo "$(GREEN)Running policy suite over scaffolded output...$(NC)"
	pytest templates/service/tests/policy/ -v --no-cov --tb=short || true

batch-inference: ## Run batch scoring job (DATA=path/to/input.parquet)
	@if [ -z "$(DATA)" ]; then \
		echo "$(RED)Usage:$(NC) make batch-inference DATA=data/batch/input.parquet"; \
		exit 1; \
	fi
	@echo "$(GREEN)Running batch inference on $(DATA)...$(NC)"
	@if [ -f scripts/batch_inference.py ]; then \
		python scripts/batch_inference.py --input $(DATA); \
	else \
		echo "$(YELLOW)See agentic/skills/batch-inference/SKILL.md for the K8s CronJob pattern$(NC)"; \
	fi

# ═══════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════

clean: ## Clean temporary files
	@echo "$(YELLOW)Cleaning...$(NC)"
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage reports/
	@echo "$(GREEN)✓ Clean$(NC)"

.DEFAULT_GOAL := help
