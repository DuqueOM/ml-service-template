# ADR-027 — Vendor-Neutral Canonical Agentic Surface

- **Status**: Accepted; amended 2026-09-26 — skills render where each tool discovers them (§9)
- **Date**: 2026-06-08
- **Deciders**: Template maintainer (`@DuqueOM`)
- **Supersedes / amends**: Amends ADR-023 §3 invariant **I-4** (introduces
  *mirror-surfaces* alongside *pointer-surfaces*). Complements ADR-005
  (agent behavior), ADR-024 / ADR-025 (portability follow-ups).
- **Superseded by**: none
- **Related artifacts**:
  - `templates/config/agentic_manifest.yaml` — cross-surface index.
  - `scripts/sync_agentic_adapters.py` — renders surfaces from canonical.
  - `scripts/validate_agentic.py`, `scripts/validate_agentic_manifest.py`.

## 1. Context

On **2 June 2026** Cognition rebranded **Windsurf** to **Devin Desktop**
(over-the-air update; accounts, plans, extensions, keybindings preserved).
The in-editor agent **Cascade** is being replaced by **Devin Local** and is
**EOL on 1 July 2026** (source: official Devin Desktop FAQ,
`docs.devin.ai/desktop/devin-desktop-faq`).

The same FAQ defines, authoritatively, how the IDE now discovers
workspace agentic configuration:

- **Directory rules**: `.devin/rules/` is the **preferred** location and
  **takes precedence**; `.windsurf/rules/` is kept as a **backward-compat
  fallback**. Both are read.
- The IDE also reads `AGENTS.md` / `agents.md` and `.cursor/rules` (`.mdc`)
  as native rule sources.
- `.devin/{rules,workflows,skills,plans}/` and the `.windsurf/` equivalents
  are all read at the workspace level.
- There is **no** `.devinrules` single-file equivalent; `.windsurfrules`
  legacy still works.

When this repo was opened in Devin Desktop, the IDE proposed (and staged) a
pure rename `.windsurf/{rules,skills,workflows}/ → .devin/`. That move is
correct for the IDE, but it broke the template's **internal contract**:
ADR-023 made `.windsurf/` the *canonical authoritative surface* referenced
by the manifest, both validators, the adapter pointers, the context
pointers, and ~50 documentation/test files. After the rename, every
`source:` path in `agentic_manifest.yaml` pointed at a directory that no
longer existed, and `validate_agentic_manifest.py --strict` would fail.

The deeper problem is structural: **the canonical source of truth was named
after a vendor**. The rebrand is the second naming event in 14 months
(Codeium → Windsurf → Devin). Re-coupling the canonical store to `.devin/`
just resets the same time-bomb under a new name.

## 2. Decision

Introduce a **vendor-neutral canonical surface** and demote every
IDE-specific directory to a *generated surface*.

1. **Canonical body store → `agentic/{rules,skills,workflows}/`** (visible,
   top-level, human-authored, the only files humans edit). "Visible, not
   hidden" because this is first-class authored content — like `docs/`,
   `templates/`, and the already-visible `AGENTS.md` — not tool config. The
   name `agentic/` matches the established vocabulary (`agentic_manifest.yaml`,
   `docs/agentic/`, `validate_agentic.py`).

2. **`.devin/` becomes a generated *mirror-surface*** containing **full
   bodies** (not pointers), because Devin Desktop ingests directory content
   directly and does **not** read an arbitrary `agentic/` directory. `.devin/`
   is the preferred IDE location and has precedence per the FAQ.

3. **`.cursor/`, `.claude/`, `.codex/` remain *pointer-surfaces***
   (unchanged pattern): thin pointers back to `agentic/...` + `AGENTS.md`.

4. **`.windsurf/` is dropped.** Devin reads `.devin/` with precedence; the
   FAQ keeps `.windsurf/` only as a fallback for the deprecated client.
   Maintaining a third on-disk copy adds duplication without value. (Revisit
   trigger below if adopters still on legacy Windsurf report a gap.)

5. **`AGENTS.md` remains the sole authority** for invariants, modes,
   permissions, and handoffs — unchanged by this ADR.

### 2.1 Amendment to ADR-023 I-4

ADR-023 I-4 stated *"adapters are generated pointers, never forks."* This
ADR refines it: surfaces are generated from the canonical store and fall
into two kinds, declared per surface in the manifest via `kind:`:

| Surface kind | Content | Surfaces | Why |
| -------------- | --------- | ---------- | ----- |
| `canonical` | authored bodies | `agentic/` | single source of truth |
| `mirror` | generated full bodies | `.devin/` | IDE ingests bodies, can't follow pointers |
| `pointer` | generated thin pointers | `.cursor/`, `.claude/`, `.codex/` | tool can discover via reference |

The single-source-of-truth guarantee is **preserved** even though
`mirror` surfaces duplicate bytes, because:

- The sync script is the **only** writer of `.devin/` (and pointer surfaces).
- CI runs `sync_agentic_adapters.py --check`; any drift between `agentic/`
  and a generated surface **fails the build** (same discipline as generated
  code or lockfiles).
- Humans editing a `mirror` surface directly is a contract violation caught
  by CI, not a silent fork.

## 3. Invariants (contract-enforced)

- **I-027-1** — `agentic/{rules,skills,workflows}/` is the only canonical
  body store. Every manifest `source:` resolves there.
- **I-027-2** — `.devin/` content is byte-identical to the rendered output
  of `agentic/`. Enforced by `sync_agentic_adapters.py --check` in CI.
- **I-027-3** — pointer-surfaces (`.cursor/`, `.claude/`, `.codex/`)
  reference both the canonical `source:` and `AGENTS.md`. Unchanged from
  ADR-023.
- **I-027-4** — no human edits a generated surface. The fix for any rule /
  skill / workflow change is to edit `agentic/...` then run sync.

## 4. Scope

**In scope** (this change):

- `agentic/` canonical store (moved from `.devin/` via `git mv`, preserving
  the rename chain `.windsurf → .devin → agentic`).
- Manifest: `source:` → `agentic/...`; `surfaces` gains `kind:` and a
  `canonical` + `devin` (mirror) entry.
- `validate_agentic.py`: scan `agentic/`.
- `validate_agentic_manifest.py`: validate mirror-surface body parity and
  the `.devin_context.md` pointer.
- `sync_agentic_adapters.py`: render mirror bodies into `.devin/`.
- Context pointers: add `.devin_context.md`; retire `.windsurf_context.md`.
- Contract tests updated to the new paths and the mirror/pointer split.
- Live references in code comments, docs, Makefiles, pre-commit, CI.

**Out of scope**:

- Rewriting historical ADRs (004, 005, 006, …). They are point-in-time
  records; their `.windsurf/...` references stand as written. Only ADR-023
  is amended (here) because it defines the live contract.
- Re-introducing `.windsurf/`. (Revisit trigger below.)
- Any change to `AGENTS.md` invariants, modes, or permissions.

## 5. Consequences

### Positive

- Canonical name survives the next rebrand: adding a future surface is a
  manifest entry + a sync backend, never a rename of the source of truth.
- "Tools adapt to our canon" is now structurally true and visible.
- CI-enforced parity means a vendor-renamed directory can never again
  silently diverge from the canon.

### Negative

- `.devin/` now duplicates bodies on disk (≈ 43 files). Mitigated: it is
  generated, CI-checked, and humans are told never to edit it.
- One more generated artifact in the tree. Mitigated by the `--check` gate.

### Neutral

- The IDE picks up `.devin/` exactly as before; interactive behavior is
  unchanged for users.

## 6. Revisit triggers

- **Adopters report Devin still reads `.windsurf/` in some flow** → add a
  second mirror-surface `windsurf` to the manifest; sync renders both.
- **A future IDE reads a neutral directory natively** → point it straight at
  `agentic/`, drop its mirror.
- **Cognition publishes a `.devinrules`/native pointer mechanism** → the
  `.devin/` mirror could shrink to a pointer; flip its `kind:` to `pointer`.
- **`.devin/` body drift appears in review** → tighten the CI `--check` to a
  required status, not advisory.

## 7. Alternatives considered

| Alternative | Why rejected |
| ------------- | -------------- |
| Accept the rename: `.devin/` is canonical (Option A) | Re-couples the source of truth to a vendor name; repeats the exact failure this ADR removes |
| Keep `.windsurf/` canonical, treat Devin as one more adapter (Option B) | `.windsurf` is now a dead brand and Devin needs real bodies under `.devin/`, so a pointer adapter would not be ingested |
| Hidden `.agents/` as canonical | Demotes authored content to tool-config status; inconsistent with the visible `AGENTS.md` authority; introduces `agents` vs `agentic` vocabulary split |
| Symlink `.devin/ → agentic/` | Not portable to Windows (the template explicitly supports Windows in `validate_agentic.py`) |

## 8. Related

- `docs/decisions/ADR-023-agentic-portability-and-context.md` — the contract
  this ADR amends.
- `AGENTS.md` — authority, unchanged.
- Official source: `docs.devin.ai/desktop/devin-desktop-faq`,
  `docs.devin.ai/desktop/cascade/workflows` (verified 2026-06-08).

## 9. Amendment, 2026-09-26 — skills render where each tool discovers them

**Decision.** Pointer surfaces publish a skill as `<root>/<id>/SKILL.md` with
`name` and `description` front-matter on every tool that discovers skills,
and at a root that tool reads. Cursor and Codex share `.agents/skills/`.

**Why.** §2 made the pointer layout a per-surface choice and the manifest the
record of it. Checked against each tool's documentation on 2026-09-26:

| Tool | Discovers skills at | Received until now |
| --- | --- | --- |
| Claude Code | `.claude/skills/<id>/SKILL.md` | that layout (fixed after audit R6) |
| Cursor | `.cursor/skills/` or `.agents/skills/<id>/SKILL.md`, `name` equal to the folder | flat `.cursor/skills/<id>.md` |
| Codex | `.agents/skills/<id>/SKILL.md`, from the working directory up to the repository root, and nowhere else in a repository | flat `.codex/skills/<id>.md` |

Neither Cursor nor Codex listed a single skill, in this repository or in any
scaffolded service. `validate_agentic_manifest.py --strict` stayed green
because it compared the rendered files with the manifest, and the manifest
named those places. The R6 fix made the Claude pointer loadable without asking
whether the other two were. The same gap was first found downstream, in
ml-platform's QA-4 round twelve.

**Why `.agents/skills/` and not `.cursor/skills/<id>/SKILL.md` for Cursor.**
Cursor reads both directories. Codex reads only `.agents/skills/`. Rendering
Cursor's copy under `.cursor/skills/` as well would list every skill twice in
Cursor. The shared root is rendered once, and each pointer names every surface
that reads it. This is not the hidden canonical `.agents/` that §7 rejected:
the canonical store is still `agentic/`, and `.agents/skills/` holds generated
pointers, like every other adapter directory. It is §6's second revisit
trigger, "a future IDE reads a neutral directory natively", arriving for
skills.

**Enforced by:**

- `validate_agentic_manifest.py`, `skill_discovery`. A table of each tool's
  documented roots, kept apart from the manifest, fails:
  - a surface whose skills root its tool does not read;
  - a pointer surface with no recorded contract;
  - a flat file in a skills root;
  - a generated pointer left at the old flat layout.

  Loadability (front-matter, name equal to the folder, description of at most
  1,024 characters) now applies to Cursor and Codex as well as Claude.
- `templates/tests/governance/test_skill_discovery_contract.py`, including the
  old layout failing, the shared root settling on a second render, and the
  migration of old pointers.

**Migration.** `sync_agentic_adapters.py` removes flat skill pointers it
generated under `.cursor/skills/` and `.codex/skills/`. It recognises them by
the canonical source they name, and leaves any other file alone. `copier
update` therefore migrates a scaffolded service by itself. See `MIGRATION.md`.

**Revisit when** a tool documents a different discovery root. Update the
table in `validate_agentic_manifest.py` first, and the manifest will follow.
