# Operational Memory Plane

Moved here from the README, which keeps a short status summary and links to
this page. The status banner below is the same one the README carries, and
`templates/tests/governance/test_phase0_disclosure.py` holds both to the phase
that [ADR-018](../decisions/ADR-018-operational-memory-plane.md) declares.

> **Status — Phase 1 (contracts + redaction).** The canonical `MemoryUnit` dataclass and the gitleaks + PII redaction
> pipeline ship today: `templates/service/common_utils/memory_types.py` and
> `templates/service/common_utils/memory_redaction.py`, contract-tested by `test_memory_contracts.py` and
> `test_memory_redaction.py` for immutability, severity normalization, sensitivity ≥ bucket-ACL minimum, single-tenant
> Phase 1 scope, idempotent redaction, and structural isolation from the `/predict` path. The ingest worker, the vector
> store, the retrieval API, and any agent-facing recall surface are **NOT yet implemented** in this template and are
> explicitly deferred per ADR-018 §Phase plan. Adopters cannot call retrieval APIs today; the design page describes the
> **target shape** so the policy is reviewable before code lands. See
> [`ADR-018`](../decisions/ADR-018-operational-memory-plane.md) §Phase plan for the staged delivery.
>
> *Audit trail: Phase 0 disclosure added in response to R4 finding C2; transitioned to Phase 1 in the same audit-r4 sprint. See [`docs/audit/ACTION_PLAN_R4.md`](../audit/ACTION_PLAN_R4.md) §S0-2 + §S2-1.*

The Operational Memory Plane is an optional companion capability for repos that want agents to draw on prior work
without introducing hidden behavior.

## What it is

- A retrieval layer for prior incidents, deploy regressions, postmortems, drift events, training decisions, and
  successful fixes.
- A derived memory system, not the source of truth.
- Backed by structured metadata, embeddings, and evidence references to canonical artifacts.

## What it is not

- It is not in the synchronous `/predict` path.
- It does not change policy by itself.
- It does not replace audit logs, issues, runbooks, or ADRs.

## How it is used

- Before deploy: retrieve similar release failures and known bad remediation patterns.
- Before retrain: recall similar drift events, challenger outcomes, and previous thresholds.
- During incidents: retrieve similar symptoms, runbooks, and postmortem summaries.
- During CI repair: recall past failures and successful bounded fixes.

The operational rule is simple: memory can add context and escalate caution, but it cannot silently approve a risky action.
