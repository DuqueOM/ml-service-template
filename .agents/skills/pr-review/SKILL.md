---
name: pr-review
description: "Dual-axis change review — Standards (repo conventions, anti-patterns) and Spec (does the diff implement what the linked ADR/issue asked for) — evaluated in isolation so neither pass contaminates the other's verdict (Mode: AUTO — AGENTS.md Agent Behavior Protocol applies.)"
---

# pr-review

**Adapter surface**: `codex`, `cursor`
**Authority**: `AGENTS.md#Agent Behavior Protocol`
**Mode**: `AUTO`
**Canonical source**: `agentic/skills/pr-review/SKILL.md`

Read `agentic/skills/pr-review/SKILL.md` in full before invoking this skill. The canonical
skill body, trigger conditions, escalation rules, and success criteria
live there.

This file exists only so `codex`, `cursor` can discover the skill without
forking `agentic/skills/`.
