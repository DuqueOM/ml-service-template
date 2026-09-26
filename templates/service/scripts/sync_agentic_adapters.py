#!/usr/bin/env python3
"""Render agent-surface files from the canonical manifest (ADR-027).

The source of truth remains:

* `AGENTS.md` for operating protocol, permissions, and invariants.
* `agentic/` for canonical rule / skill / workflow bodies.
* `templates/config/agentic_manifest.yaml` for cross-surface indexing.

Surfaces come in two kinds:

* mirror-surfaces (`.devin/`): full bodies copied byte-for-byte from
  `agentic/`, because the IDE ingests directory bodies and cannot follow
  pointers. Humans never edit these; CI checks byte-parity via --check.
* pointer-surfaces (`.cursor/`, `.claude/`, `.codex/`): thin pointers back
  to the canonical source + `AGENTS.md`. No body text lives here.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - bootstrap guard
    raise SystemExit("PyYAML is required. Install with: pip install pyyaml")

# Layout-flexible path resolution (ADR-030).
#
# The scripts must work in two contexts:
#   1. Template repo:  <repo>/scripts/sync_agentic_adapters.py
#      Manifest at:    <repo>/templates/config/agentic_manifest.yaml
#   2. Generated service: <service>/scripts/sync_agentic_adapters.py
#      Manifest at:    <service>/config/agentic_manifest.yaml
#
# REPO_ROOT is always the directory containing AGENTS.md. The manifest is
# found by searching known config-dir locations relative to REPO_ROOT.

_MANIFEST_CANDIDATES = (
    "templates/config/agentic_manifest.yaml",  # template repo
    "config/agentic_manifest.yaml",  # generated service
)


def _find_repo_root(explicit: Path | None = None) -> Path:
    """Return the directory containing AGENTS.md."""
    if explicit is not None:
        return explicit.resolve()
    script_dir = Path(__file__).resolve().parent
    for ancestor in [script_dir] + list(script_dir.parents):
        if (ancestor / "AGENTS.md").is_file():
            return ancestor
    # Fall back to parents[1] (legacy behavior).
    return script_dir.parents[1]


def _find_manifest(root: Path, explicit: Path | None = None) -> Path:
    """Return the manifest path, searching known locations."""
    if explicit is not None:
        return explicit.resolve()
    for rel in _MANIFEST_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    # Fall back to the first candidate (legacy behavior).
    return root / _MANIFEST_CANDIDATES[0]


REPO_ROOT = _find_repo_root()
MANIFEST = _find_manifest(REPO_ROOT)


RULE_EXTENSIONS = {
    "cursor": ".mdc",
}

# Surfaces whose runtime only discovers skills laid out as
# `<root>/<skill-id>/SKILL.md` with YAML frontmatter (name + description).
# Claude Code, Cursor and Codex all follow the open Agent Skills layout and
# ignore flat `<root>/<skill-id>.md` files, so the pointer adopts the
# discoverable layout while still carrying zero policy text (ADR-027
# pointer-surface contract). Until 2026-09 only `claude` was listed, and
# Cursor and Codex discovered none of the skills (ADR-027 §9).
SKILL_DIR_SURFACES = {"claude", "cursor", "codex"}

# Where the flat pointers used to live. Generated pointers left there by an
# earlier render are removed, so a `copier update` migrates a service instead
# of leaving a second, invisible copy behind. Only files the generator wrote
# (they name their canonical source) are touched.
LEGACY_SKILL_ROOTS = (".cursor/skills", ".codex/skills")

WORKFLOW_ROOT_KEYS = ("workflows", "commands")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _load_manifest() -> dict[str, Any]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{MANIFEST} did not parse to a mapping")
    return data


def _write(path: Path, body: str, check: bool) -> bool:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == body:
        return False
    if check:
        print(f"would update {_rel(path)}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    print(f"updated {_rel(path)}")
    return True


def _copy_body(src: Path, dst: Path, check: bool) -> bool:
    """Byte-for-byte copy a canonical body into a mirror-surface path."""
    new = src.read_bytes()
    old = dst.read_bytes() if dst.exists() else None
    if old == new:
        return False
    if check:
        print(f"would update {_rel(dst)}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(new)
    print(f"updated {_rel(dst)}")
    return True


def _surface_kind(spec: dict[str, Any]) -> str | None:
    kind = spec.get("kind")
    if kind:
        return kind
    status = spec.get("status")
    if status in ("canonical", "authoritative"):
        return "canonical"
    if status == "mirror":
        return "mirror"
    if status == "adapter":
        return "pointer"
    return None


def _canonical_roots(manifest: dict[str, Any]) -> dict[str, str]:
    for spec in (manifest.get("surfaces") or {}).values():
        if _surface_kind(spec) == "canonical":
            return spec.get("roots") or {}
    return {}


def _render_mirror(surface: str, spec: dict[str, Any], manifest: dict[str, Any], check: bool) -> bool:
    """Copy canonical bodies into a mirror-surface, preserving layout."""
    changed = False
    canon_roots = _canonical_roots(manifest)
    roots = spec.get("roots") or {}
    for bucket in ("rules", "skills", "workflows"):
        canon_root = canon_roots.get(bucket)
        mirror_root = roots.get(bucket)
        if not canon_root or not mirror_root:
            continue
        expected: set[Path] = set()
        for entry in manifest.get(bucket) or []:
            if surface not in (entry.get("surfaces") or []):
                continue
            src = REPO_ROOT / entry["source"]
            try:
                rel = Path(entry["source"]).relative_to(canon_root)
            except ValueError:
                continue
            dst = REPO_ROOT / mirror_root / rel
            expected.add(dst)
            if src.exists():
                changed |= _copy_body(src, dst, check)
        # Prune stale mirror files (top-level *.md and nested SKILL.md).
        root_dir = REPO_ROOT / mirror_root
        if root_dir.exists():
            for item in sorted(root_dir.rglob("*.md")):
                if item not in expected:
                    changed = True
                    if check:
                        print(f"would remove {_rel(item)}")
                    else:
                        item.unlink()
                        print(f"removed {_rel(item)}")
    return changed


def _remove_stale(path: Path, expected: set[Path], suffixes: tuple[str, ...], check: bool) -> bool:
    changed = False
    if not path.exists():
        return changed
    for item in sorted(path.iterdir()):
        if item.is_file() and item.suffix in suffixes and item not in expected:
            changed = True
            if check:
                print(f"would remove {_rel(item)}")
            else:
                item.unlink()
                print(f"removed {_rel(item)}")
    return changed


def _remove_stale_skill_dirs(root: Path, expected: set[Path], check: bool) -> bool:
    """Prune `<root>/<id>/SKILL.md` pointers whose skill left the manifest."""
    changed = False
    if not root.exists():
        return changed
    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        skill_md = item / "SKILL.md"
        if skill_md.exists() and skill_md not in expected:
            changed = True
            if check:
                print(f"would remove {_rel(skill_md)}")
            else:
                skill_md.unlink()
                print(f"removed {_rel(skill_md)}")
        if not check and item.exists() and not any(item.iterdir()):
            item.rmdir()
            print(f"removed {_rel(item)}/")
    return changed


def _remove_legacy_skill_pointers(check: bool) -> bool:
    """Delete flat skill pointers an earlier layout generated (ADR-027 §9)."""
    changed = False
    for legacy in LEGACY_SKILL_ROOTS:
        root = REPO_ROOT / legacy
        if not root.is_dir():
            continue
        for item in sorted(root.glob("*.md")):
            body = item.read_text(encoding="utf-8")
            generated = "**Canonical source**: `agentic/skills/" in body or "Skills Index" in body
            if not generated:
                continue
            changed = True
            if check:
                print(f"would remove {_rel(item)}")
            else:
                item.unlink()
                print(f"removed {_rel(item)}")
        if not check and root.exists() and not any(root.iterdir()):
            root.rmdir()
            print(f"removed {_rel(root)}/")
    return changed


def _render_skill_roots(manifest: dict[str, Any], check: bool) -> bool:
    """Render every pointer surface's skills, once per ROOT rather than per surface.

    Cursor and Codex both read `.agents/skills/`. Rendered per surface, one
    surface's pass prunes what only the other lists and each run rewrites the
    other's files, so `--check` never settles. Grouped by root, each root is
    written and pruned once, and each pointer names every surface that reads
    it (ADR-027 §9).
    """
    surfaces = manifest.get("surfaces") or {}
    groups: dict[str, list[str]] = {}
    for surface, spec in sorted(surfaces.items()):
        root = (spec.get("roots") or {}).get("skills")
        if root and _surface_kind(spec) == "pointer":
            groups.setdefault(root, []).append(surface)

    changed = False
    for skill_root, sharing in groups.items():
        as_dirs = [s in SKILL_DIR_SURFACES for s in sharing]
        if len(set(as_dirs)) != 1:
            raise SystemExit(f"{skill_root} is shared by {sharing}, which disagree on the skill layout")
        root = REPO_ROOT / skill_root
        expected: set[Path] = set()
        for skill in manifest.get("skills") or []:
            readers = sorted(set(sharing) & set(skill.get("surfaces") or []))
            if not readers:
                continue
            label = "`, `".join(readers)
            args = (label, skill["id"], skill["source"], skill.get("mode", "AUTO"))
            if as_dirs[0]:
                path, body = root / skill["id"] / "SKILL.md", _skill_pointer_dir(*args)
            else:
                path, body = root / f"{skill['id']}.md", _skill_pointer(*args)
            expected.add(path)
            changed |= _write(path, body, check)
        for surface in sharing:
            roots = surfaces[surface].get("roots") or {}
            if "skills_index" not in roots:
                continue
            expected.add(REPO_ROOT / roots["skills_index"])
            listed = [s for s in (manifest.get("skills") or []) if surface in (s.get("surfaces") or [])]
            changed |= _copy_index("`, `".join(sharing), roots, listed, check)
        changed |= _remove_stale(root, expected, (".md",), check)
        if as_dirs[0]:
            changed |= _remove_stale_skill_dirs(root, expected, check)
    return changed


def _render_skill_index(surface: str, skills: list[dict[str, Any]]) -> str:
    rows = [
        "| Skill | Mode | Canonical |",
        "| ----- | ---- | --------- |",
    ]
    for skill in skills:
        sid = skill["id"]
        rows.append(f"| `{sid}` | `{skill.get('mode', 'AUTO')}` | `{skill['source']}` |")
    table = "\n".join(rows)
    title = "Shared" if "`" in surface else surface.title()
    return f"""# {title} Skills Index

**Adapter surface**: `{surface}`
**Authority**: `AGENTS.md` + `templates/config/agentic_manifest.yaml`

This directory is generated by `scripts/sync_agentic_adapters.py`.
Each skill file is a pointer to the canonical `agentic/.../SKILL.md`;
no skill body lives here.

{table}

To change behavior, edit the canonical `agentic/skills/<id>/SKILL.md`
and run:

```bash
python3 scripts/sync_agentic_adapters.py
python3 scripts/validate_agentic_manifest.py --strict
```
"""


def _copy_index(surface: str, roots: dict[str, str], skills: list[dict[str, Any]], check: bool) -> bool:
    index_key = "skills_index"
    if index_key not in roots:
        return False
    target = REPO_ROOT / roots[index_key]
    body = _render_skill_index(surface, skills)
    return _write(target, body, check)


def _rule_pointer(surface: str, rid: str, source: str) -> str:
    return f"""# {rid}

**Adapter surface**: `{surface}`
**Authority**: `AGENTS.md` + `templates/config/agentic_manifest.yaml`
**Canonical source**: `{source}`

Read the canonical source in full before acting. This file is a thin
adapter pointer and must not duplicate policy text.

To change this rule, edit `{source}`, update the manifest when needed,
then run:

```bash
python3 scripts/sync_agentic_adapters.py
python3 scripts/validate_agentic_manifest.py --strict
```
"""


def _canonical_skill_description(source: str) -> str:
    """Extract the one-line description from a canonical SKILL.md frontmatter."""
    path = REPO_ROOT / source
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ""
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return ""
    desc = str(meta.get("description", "")).strip()
    return " ".join(desc.split())


def _skill_pointer_dir(surface: str, sid: str, source: str, mode: str) -> str:
    """Discoverable SKILL.md pointer (frontmatter + thin body, no policy text)."""
    desc = _canonical_skill_description(source) or f"Pointer to canonical skill {source}"
    desc = desc.replace('"', "'")
    body = _skill_pointer(surface, sid, source, mode)
    return f"""---
name: {sid}
description: "{desc} (Mode: {mode} — AGENTS.md Agent Behavior Protocol applies.)"
---

{body}"""


def _skill_pointer(surface: str, sid: str, source: str, mode: str) -> str:
    return f"""# {sid}

**Adapter surface**: `{surface}`
**Authority**: `AGENTS.md#Agent Behavior Protocol`
**Mode**: `{mode}`
**Canonical source**: `{source}`

Read `{source}` in full before invoking this skill. The canonical
skill body, trigger conditions, escalation rules, and success criteria
live there.

This file exists only so `{surface}` can discover the skill without
forking `agentic/skills/`.
"""


def _workflow_pointer(surface: str, wid: str, source: str, mode: str) -> str:
    return f"""# {wid}

**Adapter surface**: `{surface}`
**Authority**: `AGENTS.md#Agent Behavior Protocol`
**Mode**: `{mode}`
**Canonical source**: `{source}`

Execute this workflow by reading the canonical `agentic/` workflow in
full, then applying the same AUTO/CONSULT/STOP protocol and audit
requirements from `AGENTS.md`.

This file is generated as an adapter pointer. Do not add workflow
logic here.
"""


def render(manifest: dict[str, Any], check: bool) -> int:
    surfaces = manifest.get("surfaces") or {}
    changed = _render_skill_roots(manifest, check)

    for surface, spec in sorted(surfaces.items()):
        kind = _surface_kind(spec)
        if kind == "canonical":
            continue
        if kind == "mirror":
            changed |= _render_mirror(surface, spec, manifest, check)
            continue
        if kind != "pointer":
            continue
        roots = spec.get("roots") or {}

        rule_root = roots.get("rules")
        if rule_root:
            root = REPO_ROOT / rule_root
            suffix = RULE_EXTENSIONS.get(surface, ".md")
            expected: set[Path] = set()
            for rule in manifest.get("rules") or []:
                if surface not in (rule.get("surfaces") or []):
                    continue
                path = root / f"{rule['id']}{suffix}"
                expected.add(path)
                changed |= _write(
                    path,
                    _rule_pointer(surface, rule["id"], rule["source"]),
                    check,
                )
            changed |= _remove_stale(root, expected, (".md", ".mdc"), check)

        workflow_key = next((k for k in WORKFLOW_ROOT_KEYS if k in roots), None)
        if workflow_key:
            root = REPO_ROOT / roots[workflow_key]
            expected = set()
            for workflow in manifest.get("workflows") or []:
                if surface not in (workflow.get("surfaces") or []):
                    continue
                path = root / f"{workflow['id']}.md"
                expected.add(path)
                changed |= _write(
                    path,
                    _workflow_pointer(
                        surface,
                        workflow["id"],
                        workflow["source"],
                        workflow.get("mode", "AUTO"),
                    ),
                    check,
                )
            changed |= _remove_stale(root, expected, (".md",), check)

    changed |= _remove_legacy_skill_pointers(check)
    return 1 if check and changed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without writing adapter files.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Explicit path to agentic_manifest.yaml (overrides auto-discovery).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Explicit repo root directory (overrides auto-discovery).",
    )
    args = parser.parse_args()
    global REPO_ROOT, MANIFEST
    if args.root is not None:
        REPO_ROOT = args.root.resolve()
    if args.manifest is not None or args.root is not None:
        MANIFEST = _find_manifest(REPO_ROOT, args.manifest)
    return render(_load_manifest(), check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
