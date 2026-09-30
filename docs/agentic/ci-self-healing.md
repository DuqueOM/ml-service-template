# Agentic CI self-healing

Moved here from the README, which keeps a short status summary and links to
this page. The status banner below is the same one the README carries, and
`templates/tests/governance/test_phase0_disclosure.py` holds both to the phase
that [ADR-019](../decisions/ADR-019-agentic-ci-self-healing.md) declares.

> **Status — Phase 1 (shadow, read-only).** The classifier and collector ship today: `scripts/ci_collect_context.py` and
> `scripts/ci_classify_failure.py`, governed by `templates/config/ci_autofix_policy.yaml` and
> `templates/config/model_routing_policy.yaml`, and contract-tested by `test_ci_autofix_policy_contract.py` and
> `test_ci_classify_failure_phase1.py`. The classifier is wired
> into CI in **shadow mode only** — it observes failures and emits classifications, but **does NOT write code, does NOT
> open PRs, does NOT mutate any branch**. The patch worker, verifier, and write-enabled lanes are **NOT implemented
> yet** and are gated on 14 days of shadow data per ADR-019 §Phase plan. No agent will autonomously open a PR against
> your CI today. See [`ADR-019`](../decisions/ADR-019-agentic-ci-self-healing.md) §Phase plan for the staged delivery.
>
> *Audit trail: Phase 0 disclosure added in response to R4 finding C2; transitioned to Phase 1 in the same audit-r4 sprint. See [`docs/audit/ACTION_PLAN_R4.md`](../audit/ACTION_PLAN_R4.md) §S0-2 + §S1-6.*

The template supports a bounded self-healing lane for CI. This is not "let the agent fix anything." It is a
policy-governed repair loop with verification, audit, and branch isolation.

## Safety model

- Repairs happen on a dedicated branch, never directly on `main`.
- Blast radius is capped by file count, line count, and retry count.
- Protected paths and sensitive workflows are excluded from `AUTO`.
- Every fix must re-run targeted verification before it can be proposed.
- Failure to verify escalates the action to `CONSULT` or `STOP`.

## Repair matrix

| Failure class | Mode | Examples |
| --------------- | ------ | ---------- |
| formatting drift | `AUTO` | lint formatting, imports, whitespace |
| documentation quality | `AUTO` | markdown issues, link fixes, generated docs drift |
| non-sensitive config syntax | `AUTO` | YAML, TOML, JSON syntax repairs in low-risk areas |
| fixture or snapshot alignment | `CONSULT` | test payload alignment, deterministic snapshot refresh |
| non-prod workflow repair | `CONSULT` | CI-only workflow fixes, path updates, harness repairs |
| security, auth, deploy, infra, or quality gate failures | `STOP` | secrets, identity, prod deploy, Terraform, fairness, drift, retrain gates |

This lane is designed to keep CI moving without allowing agent autonomy to leak into high-risk change surfaces.
