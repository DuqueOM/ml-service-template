---
name: rollback
description: "Emergency rollback procedure for production ML service — Argo Rollouts abort + kubectl undo + MLflow revert + alert silencing (Mode: STOP — AGENTS.md Agent Behavior Protocol applies.)"
---

# rollback

**Adapter surface**: `codex`, `cursor`
**Authority**: `AGENTS.md#Agent Behavior Protocol`
**Mode**: `STOP`
**Canonical source**: `agentic/skills/rollback/SKILL.md`

Read `agentic/skills/rollback/SKILL.md` in full before invoking this skill. The canonical
skill body, trigger conditions, escalation rules, and success criteria
live there.

This file exists only so `codex`, `cursor` can discover the skill without
forking `agentic/skills/`.
