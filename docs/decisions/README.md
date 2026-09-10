<!-- GENERATED FILE — do not edit by hand.
     Regenerate with: python3 scripts/generate_adr_index.py
     Verified in CI by: python3 scripts/generate_adr_index.py --check -->

# Architecture Decision Records

Every non-trivial decision in this template is recorded here with its
measured trade-offs. This index is generated from the files themselves, so
it cannot drift from what the directory actually contains.

`docs/COMPLIANCE_MAPPING.md` cites this index as EU AI Act Art. 11
evidence (technical documentation sufficient to assess compliance). It is
therefore load-bearing: an auditor reading the mapping lands here.

Numbering is dense and gaps are deliberate — a withdrawn ADR keeps its
number and says so, rather than being deleted and leaving a hole
(`scripts/check_doc_coherence.py` C5 enforces this).

**50 decisions recorded.**

| ADR | Decision |
| --- | --- |
| 001 | [Template Scope Boundaries](ADR-001-template-scope-boundaries.md) |
| 002 | [Model Promotion Governance as Opt-in Module](ADR-002-model-promotion-governance.md) |
| 003 | [Feast Integration Pattern (External Feature Repo)](ADR-003-feast-integration-pattern.md) |
| 004 | [EDA Phase Integration into the Agentic Pipeline](ADR-004-eda-phase-integration.md) |
| 005 | [Agent Behavior Protocol + Supply Chain Security](ADR-005-agent-behavior-and-security.md) |
| 006 | [Closed-Loop Monitoring with Delayed Ground-Truth Labels](ADR-006-closed-loop-monitoring.md) |
| 007 | [Sliced Performance Analysis as First-Class Monitoring](ADR-007-sliced-performance-analysis.md) |
| 008 | [Champion/Challenger Statistical Gate Before Promotion](ADR-008-champion-challenger-statistical-gate.md) |
| 009 | [Retraining Orchestration — When (and When Not) to Migrate Beyond GitHub Actions](ADR-009-retraining-orchestration-triggers.md) |
| 010 | [Dynamic Behavior Protocol via MCP-Prometheus](ADR-010-dynamic-behavior-protocol.md) |
| 011 | [Environment Promotion Gates (dev → staging → prod)](ADR-011-environment-promotion-gates.md) |
| 012 | [API Evolution Policy (reserved number, withdrawn)](ADR-012-api-evolution-policy.md) |
| 013 | [GitOps Strategy — `kubectl apply` now, ArgoCD when it pays off](ADR-013-gitops-strategy.md) |
| 014 | [Gap Remediation Plan — v1.9.0 → v2.0.0 Public Release](ADR-014-gap-remediation-plan.md) |
| 015 | [Productization Roadmap (post-audit)](ADR-015-productization-roadmap.md) |
| 016 | [External Audit R2 — Remediation Plan](ADR-016-external-audit-r2-remediation.md) |
| 017 | [Network Mode + Per-Environment IAM Split (PR-A1)](ADR-017-network-iam-split.md) |
| 018 | [Operational Memory Plane](ADR-018-operational-memory-plane.md) |
| 019 | [Agentic CI Self-Healing](ADR-019-agentic-ci-self-healing.md) |
| 020 | [External Audit R4 — Remediation Plan (master)](ADR-020-r4-audit-remediation.md) |
| 021 | [Fairness Thresholds — Disparate Impact Ratio Floor](ADR-021-fairness-thresholds.md) |
| 022 | [PSI Drift Thresholds — Warn / Alert Cutoffs](ADR-022-psi-thresholds.md) |
| 023 | [Agentic Portability Layer and Contextualization](ADR-023-agentic-portability-and-context.md) |
| 024 | [May 2026 Audit Remediation & Posture Correction](ADR-024-audit-may-2026-remediation.md) |
| 025 | [`common_utils/` distribution model](ADR-025-common-utils-distribution.md) |
| 026 | [Branch Protection & Tag Immutability via GitHub Rulesets](ADR-026-branch-protection.md) |
| 027 | [Vendor-Neutral Canonical Agentic Surface](ADR-027-vendor-neutral-canonical-surface.md) |
| 028 | [LLM-Assist Integration for Template Maintenance and Day-2 Operations](ADR-028-llm-assist-integration.md) |
| 029 | [Agentic Adoption Contract & Interoperability Strategy](ADR-029-agentic-adoption-contract.md) |
| 030 | [Copier-based Scaffolding Migration](ADR-030-copier-scaffolding-migration.md) |
| 031 | [Documentation Coherence System](ADR-031-documentation-coherence-system.md) |
| 032 | [BentoML as an Optional Alternative Serving Backend](ADR-032-bentoml-alternative-serving-backend.md) |
| 033 | [Local-first Stack Profiles](ADR-033-local-first-stack-profiles.md) |
| 034 | [CCDS-aligned generated layout view](ADR-034-ccds-aligned-generated-layout.md) |
| 035 | [uv adoption + Copier index publication](ADR-035-uv-adoption-copier-index.md) |
| 036 | [Batch-Only Deployment Topology](ADR-036-batch-only-deployment-topology.md) |
| 037 | [Dual-Namespace Retrieval Separation (Operational Memory vs. Pedagogical RAG)](ADR-037-dual-namespace-retrieval-separation.md) |
| 038 | [Compliance Mapping (NIST AI RMF / ISO 42001 / EU AI Act)](ADR-038-compliance-mapping.md) |
| 039 | [CI-Green Verification as a Separated-Verb Agentic Gate](ADR-039-ci-green-verification-gate.md) |
| 040 | [Documentation Language and Private-Reference Guard](ADR-040-doc-language-and-privacy-guard.md) |
| 041 | [Agentic Skill Expansion and Domain Taxonomy (External Landscape Review)](ADR-041-agentic-skill-and-domain-expansion.md) |
| 042 | [Native-Cloud-First Edge Protection (Cloud Armor / AWS WAF+Shield), Cloudflare Optional](ADR-042-native-cloud-edge-protection.md) |
| 043 | [Audit-Grade Quality Guardian: a Maintenance Agent for Enterprise Audit Standards](ADR-043-audit-quality-guardian.md) |
| 044 | [Consolidate black + isort + flake8 into ruff](ADR-044-ruff-toolchain-consolidation.md) |
| 045 | [Separate the release-channel tag namespace from frozen audit snapshots](ADR-045-tag-namespace-separation.md) |
| 046 | [Replace archived tfsec with Trivy config for Terraform IaC scanning](ADR-046-tfsec-to-trivy-config-migration.md) |
| 047 | [Migrating the template from MLflow 2.18 to 3.x](ADR-047-mlflow-3-migration.md) |
| 048 | [Requirements files that share an environment must agree on their pins](ADR-048-requirements-co-installation-groups.md) |
| 049 | [The served image installs only what it runs](ADR-049-runtime-training-dependency-partition.md) |
| 050 | [Configuration a generated service declares must be read, or say it is not](ADR-050-configuration-must-be-read.md) |
