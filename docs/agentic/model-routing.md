# Model routing policy

This is the model-routing policy for the template's agentic lanes — which class
of model handles which task, and the rules that keep a cheap or experimental
model away from anything that can change a protected branch. It moved here from
the README, where it was 120 lines of a document whose job is to help someone
adopt the template; the policy itself is unchanged.

The machine-readable source of truth is
[`templates/config/model_routing_policy.yaml`](../../templates/config/model_routing_policy.yaml),
and the decision behind it is
[ADR-028](../decisions/ADR-028-llm-assist-integration.md). Nothing in this
document is load-bearing for a generated service: the serving path never calls
a language model.

The template treats model selection as a routing problem, not a brand decision.

## Routing roles

| Task type | Route | Expected behavior |
| ----------- | ------- | ------------------- |
| failure classification, extraction, low-cost triage | low-cost router | prioritize speed and cost |
| small patch generation | patch worker | optimize for bounded code edits |
| diff review and risk evaluation | reviewer / gatekeeper | prioritize consistency and policy awareness |
| multi-file root cause analysis | escalation | use stronger reasoning only when needed |

## Provider stance

- OpenAI, Anthropic, and Google models can all fit this template.
- Stable, mid-cost workhorse models should handle the default lanes.
- Frontier models should be reserved for escalation paths, hard RCA, or advisory benchmarking.
- Preview models should not be used on protected branches or governance-critical workflows.

The important part is not the provider. It is the routing policy, verification layer, and operation mode boundaries.

## Recommended baseline (cadence-anticipated, **NOT** vendor-verified)

> **Status — Anticipated names, pending verification.** The model names in the snapshot table below follow the cadence
> the project's adopter requested (`gpt-5.x`, `claude-opus-4.x`, `gemini-3.x`). They have **NOT** been reconciled
> against the live catalog of any provider. Several names (e.g. `gpt-5.4`, `gpt-5.5`, `gemini-3.1-pro-preview`,
> `gemini-3-flash-preview`) may not exist at adoption time.
>
> Before enabling any of these names for a production-adjacent route, **verify against the provider dashboard** (see
> §"Verifying model availability before adoption" below) and update `verified_at` in
> [`templates/config/model_routing_policy.yaml`](../../templates/config/model_routing_policy.yaml).
> The ADR-019 contract test enforces routing **structure** (preview never on protected branches; AUTO mode never
> escalation-tier), not specific model identities.
>
> *Audit trail: this disclaimer was added in response to R4 finding C1 (cadence-anticipated names presented as verified). See [`docs/audit/ACTION_PLAN_R4.md`](../audit/ACTION_PLAN_R4.md) §S0-1.*

### Cadence-anticipated names — pending vendor verification

| Role | OpenAI | Anthropic | Google | Use it for |
| ------ | -------- | ----------- | -------- | ------------ |
| **Router / cheap classify** | `gpt-5.4-nano` | `claude-haiku-4-5` | `gemini-2.5-flash-lite` | Failure triage, extraction, label classification |
| **Patch worker** | `gpt-5.4-mini` | `claude-haiku-4-5` | `gemini-2.5-flash` | Small patches, formatter fixes, doc edits |
| **Reviewer / gatekeeper** | `gpt-5.4` | `claude-sonnet-4-6` | `gemini-2.5-pro` | Diff review, risk evaluation, consistency check |
| **Hard escalation** | `gpt-5.5` | `claude-opus-4-6` | `gemini-2.5-pro` | Multi-file RCA, refactors with ripple, rare CI failures |
| **Frontier preview (non-prod only)** | — | — | `gemini-3.1-pro-preview`, `gemini-3-flash-preview` | Benchmarking lane, `workflow_dispatch` only — never on `main` |

The table above is a **structural recommendation** — four cost/quality tiers plus a non-prod preview lane. Substitute
each cell with whichever model in that tier exists in your provider's catalog at adoption time.

### Three pre-tuned structural profiles

The profiles below are described in cadence-anticipated names for continuity with the table above; treat the names as
placeholders for tier slots, not commitments to specific models.

- **Maximum simplicity** (single family): `gpt-5.4-nano` → `gpt-5.4-mini` → `gpt-5.4` → `gpt-5.5`. Cleanest cost/quality
  gradient.
- **Mix cost + quality**: `gemini-2.5-flash-lite` (router) → `gpt-5.4-mini` (patcher) → `claude-sonnet-4-6` (reviewer) →
  `gpt-5.5` or `claude-opus-4-6` (escalation). Strong gatekeeper without paying frontier cost on every call.
- **Aggressive cost minimization**: `gemini-2.5-flash-lite` → `gemini-2.5-flash` → `gemini-2.5-pro` → escalation only
  when needed. Best volume economics.

### Hard rules (codified in `ci_autofix_policy.yaml`)

- AUTO mode never uses escalation-tier models — bounded blast radius implies bounded reasoning need.
- Preview models are restricted to `workflow_dispatch` and benchmarking lanes; they cannot land on protected branches.
  The contract test refuses configurations that violate this.
- Memory-plane signals (ADR-018) can route a query to a more capable model on `repeat_failure_pattern`, but never the
  other way around — same escalation-only discipline as ADR-010.

### Verifying model availability before adoption

Before enabling any name from the table above for a production-adjacent route, verify it exists in the provider's stable
catalog **on the day of adoption** and update `verified_at` in
[`templates/config/model_routing_policy.yaml`](../../templates/config/model_routing_policy.yaml).

| Provider | Where to verify | Catalog scope to confirm |
| ---------- | ----------------- | --------------------------- |
| OpenAI | [`platform.openai.com/docs/models`](https://platform.openai.com/docs/models) | Model name appears under "Current models" (not "Deprecated" or "Legacy"); pricing and rate-limit tier acceptable for the route's expected volume |
| Anthropic | [`docs.anthropic.com/en/docs/about-claude/models`](https://docs.anthropic.com/en/docs/about-claude/models) | Model name appears in the active models table; check the `model_id` column matches what your client will send |
| Google | [`ai.google.dev/gemini-api/docs/models`](https://ai.google.dev/gemini-api/docs/models) and Vertex AI Model Garden | Confirm GA vs preview status; preview models are restricted to non-protected lanes by `model_routing_policy.yaml` |

**Process**:

1. Open the dashboard above for each provider you intend to use.
2. For every cell of the recommended-baseline table you plan to enable, confirm the model name still exists and is in
   the maturity tier the YAML expects (`stable` or `preview`).
3. If a name no longer exists, the routing layer falls back to the next candidate in the route — it never silently
   switches families. Replace the missing name with a verified equivalent and bump `verified_at`.
4. Reviewers MUST re-verify before any rollout to a protected branch.

### Honesty caveat

Vendor model names rotate every 6–12 months. The `verified_at` field in `model_routing_policy.yaml` declares when the
catalog was last reconciled. The names in the table above are anticipated based on the project's adopter cadence and
have not been reconciled against any vendor catalog at the time of writing.

## Local model plane

The routing table above describes the **cloud** lanes. ADR-028 also defines
**local** tiers — small models served on a single host, registered below the
cheapest cloud tier — for the day-2 maintenance lanes.

Where that local agent core lives has changed since ADR-028 was written, and
the change is recorded as a dated correction in the ADR rather than by editing
its original text:

| Repository | Role today |
| --- | --- |
| [`ml-platform`](https://github.com/DuqueOM/ml-platform) | **Authoritative** home of the agent core (`libs/llm-core`). The core was migrated there with its history — ml-platform ADR-002 — and ml-platform ADR-010 makes it the source of truth |
| [`agent-local`](https://github.com/DuqueOM/agent-local) | Public and not archived, but a **one-way export** of that core. Nothing flows from it back into ml-platform |

What has not changed: this template does not ship or serve a language model,
and an LLM-serving variant is out of scope (see "Scope boundaries" in the
README). The local tiers are an operator's choice for the maintenance lanes,
never a runtime dependency of a generated service.
