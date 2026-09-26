---
name: edge-audit
description: "Scan a service for edge-protection coverage (WAF, DDoS mitigation, rate limiting) against ADR-042/D-38 — produces a PASS/FAIL report with file:line evidence (Mode: AUTO — AGENTS.md Agent Behavior Protocol applies.)"
---

# edge-audit

**Adapter surface**: `codex`, `cursor`
**Authority**: `AGENTS.md#Agent Behavior Protocol`
**Mode**: `AUTO`
**Canonical source**: `agentic/skills/edge-audit/SKILL.md`

Read `agentic/skills/edge-audit/SKILL.md` in full before invoking this skill. The canonical
skill body, trigger conditions, escalation rules, and success criteria
live there.

This file exists only so `codex`, `cursor` can discover the skill without
forking `agentic/skills/`.
