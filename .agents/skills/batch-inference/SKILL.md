---
name: batch-inference
description: "Scaffold and run batch scoring jobs (CronJob + Parquet output) that reuse the service's model + feature-engineering code without opening the live API (Mode: AUTO — AGENTS.md Agent Behavior Protocol applies.)"
---

# batch-inference

**Adapter surface**: `codex`, `cursor`
**Authority**: `AGENTS.md#Agent Behavior Protocol`
**Mode**: `AUTO`
**Canonical source**: `agentic/skills/batch-inference/SKILL.md`

Read `agentic/skills/batch-inference/SKILL.md` in full before invoking this skill. The canonical
skill body, trigger conditions, escalation rules, and success criteria
live there.

This file exists only so `codex`, `cursor` can discover the skill without
forking `agentic/skills/`.
