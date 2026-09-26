#!/usr/bin/env python3
"""Validate the agentic manifest + context layer (ADR-023).

This is a READ-ONLY validator. It never writes files, installs MCPs,
or mutates repo state. Exit codes:

    0  — all checks pass
    1  — validation failure (see stderr for anchor-accurate messages)
    2  — invoked with --help / bad args / bootstrap error

Checks performed
----------------

I-1 — Authority chain (manifest never contradicts AGENTS.md)
  * Every `authority:` field in `agentic_manifest.yaml` resolves to
    either a heading in `AGENTS.md` or an existing ADR file path.
  * Missing headings / missing ADRs → fail with the specific anchor.

Index coherence (manifest claims match reality)
  * Every `source:` path listed under rules / skills / workflows
    exists on disk.
  * Every surface declared as `status: authoritative | adapter` has
    its declared `roots:` present.
  * Skill mode values are valid (AUTO / CONSULT / STOP).
  * Skill domain values, when present, are valid (ml-data /
    platform-infra / security-compliance / sre-operations — ADR-041).

Context layer (F1)
  * `*.example.yaml` files parse against `context.schema.json`.
  * `--strict` additionally:
      - Resolves `*_context.local.yaml` via env overrides or the
        default location and validates them too (if present).
      - Refuses placeholders in a `.local.yaml` (e.g. `{CompanyName}`
        must have been replaced).
      - Refuses `escalation_overrides` that de-escalate a mode
        (AUTO → STOP is OK; STOP → AUTO is NOT OK).

Context format (F3)
  * `AGENT_CONTEXT.md` + `.*_context.md` pointers are bounded:
      <= 150 lines, no line starts with `## 20` (date-led), no
      table has more than 10 rows.

Usage
-----

    python3 scripts/validate_agentic_manifest.py
    python3 scripts/validate_agentic_manifest.py --strict
    python3 scripts/validate_agentic_manifest.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Layout-flexible path resolution (ADR-030).
#
# The scripts must work in two contexts:
#   1. Template repo:  <repo>/scripts/validate_agentic_manifest.py
#      Config at:      <repo>/templates/config/
#   2. Generated service: <service>/scripts/validate_agentic_manifest.py
#      Config at:      <service>/config/
#
# REPO_ROOT is always the directory containing AGENTS.md. Config files are
# found by searching known locations relative to REPO_ROOT.

_CONFIG_CANDIDATES = (
    "templates/config",  # template repo
    "config",  # generated service
)
_MANIFEST_NAME = "agentic_manifest.yaml"


def _find_repo_root(explicit: Path | None = None) -> Path:
    """Return the directory containing AGENTS.md."""
    if explicit is not None:
        return explicit.resolve()
    script_dir = Path(__file__).resolve().parent
    for ancestor in [script_dir] + list(script_dir.parents):
        if (ancestor / "AGENTS.md").is_file():
            return ancestor
    return script_dir.parents[1]


def _find_config_dir(root: Path) -> Path:
    """Return the config directory, searching known locations."""
    for rel in _CONFIG_CANDIDATES:
        candidate = root / rel
        if candidate.is_dir():
            return candidate
    return root / _CONFIG_CANDIDATES[0]


REPO_ROOT = _find_repo_root()
_CONFIG_DIR = _find_config_dir(REPO_ROOT)
MANIFEST = _CONFIG_DIR / "agentic_manifest.yaml"
SCHEMA = _CONFIG_DIR / "context.schema.json"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
ADR_DIR = REPO_ROOT / "docs" / "decisions"
CONTEXT_EXAMPLES = {
    "company": _CONFIG_DIR / "company_context.example.yaml",
    "project": _CONFIG_DIR / "project_context.example.yaml",
}

CONTEXT_POINTERS = [
    REPO_ROOT / "AGENT_CONTEXT.md",
    REPO_ROOT / ".devin_context.md",
    REPO_ROOT / ".cursor_context.md",
    REPO_ROOT / ".claude_context.md",
    REPO_ROOT / ".codex_context.md",
]
CONTEXT_POINTER_MAX_LINES = 150
CONTEXT_POINTER_MAX_TABLE_ROWS = 10
MODE_STRICTNESS = {"AUTO": 0, "CONSULT": 1, "STOP": 2}
# Skill domain taxonomy (ADR-041) — role-based filtering/discoverability
# only; does not affect mode/authority semantics. Optional: skills
# authored before ADR-041 may omit it, but if present it must be valid.
DOMAIN_VALUES = {"ml-data", "platform-infra", "security-compliance", "sre-operations"}
# Regex for a literal placeholder that must have been replaced in a
# .local.yaml — e.g. {CompanyName}. Intentionally narrow so legitimate
# curly-braced content (jinja, env interpolation) survives.
PLACEHOLDER_RE = re.compile(r"\{[A-Z][A-Za-z0-9_]*\}")


class ValidationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Bootstrap — PyYAML and jsonschema are template deps; fail helpfully if
# the adopter has not installed them.
# ---------------------------------------------------------------------------

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - bootstrap guard
    print(
        "error: PyYAML is required. Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)

try:
    import jsonschema  # type: ignore

    HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover - bootstrap guard
    # NOT a hard exit. This script runs as a copier post-copy task, BEFORE the
    # generated service's dependencies (which declare jsonschema) have been
    # installed — so requiring it here means scaffolding fails for every
    # adopter whose ambient python happens not to have it, while passing for
    # anyone whose does. Every check that does not need JSON Schema still runs;
    # the schema-validation checks report themselves as SKIPPED so their
    # absence is visible rather than silently assumed to have passed.
    jsonschema = None  # type: ignore[assignment]
    HAVE_JSONSCHEMA = False


# ---------------------------------------------------------------------------
# Authority chain (I-1)
# ---------------------------------------------------------------------------


def _load_agents_md_headings() -> set[str]:
    """Return the set of heading texts (without leading hashes) present
    in AGENTS.md. Used to resolve `AGENTS.md#<heading>` anchors.
    """
    if not AGENTS_MD.exists():
        raise ValidationError(f"missing authority file: {AGENTS_MD}")
    headings: set[str] = set()
    for line in AGENTS_MD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if m:
            headings.add(m.group(1).strip())
    return headings


def _resolve_authority(anchor: str, agents_headings: set[str]) -> str | None:
    """Return an error string if `anchor` does not resolve, else None."""
    if "#" in anchor:
        path, heading = anchor.split("#", 1)
        if path == "AGENTS.md":
            if heading.strip() not in agents_headings:
                return f"heading not in AGENTS.md: {heading!r}"
            return None
        # Fall-through: treat as file path below.
        anchor = path
    # File path anchor (e.g. `docs/decisions/ADR-XXX.md`).
    candidate = REPO_ROOT / anchor
    if not candidate.exists():
        return f"file does not exist: {anchor}"
    return None


def _collect_authority_anchors(manifest: dict) -> list[tuple[str, str]]:
    """Walk the manifest collecting every `authority:` anchor.

    Returns a list of (location, anchor) tuples for error reporting.
    """
    anchors: list[tuple[str, str]] = []

    def _walk(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            if "authority" in obj and isinstance(obj["authority"], str):
                anchors.append((path, obj["authority"]))
            for k, v in obj.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                _walk(item, f"{path}[{idx}]")

    _walk(manifest, "$")
    return anchors


def validate_authority_chain(manifest: dict) -> list[str]:
    errors: list[str] = []
    headings = _load_agents_md_headings()
    for location, anchor in _collect_authority_anchors(manifest):
        err = _resolve_authority(anchor, headings)
        if err:
            errors.append(f"{location}: authority {anchor!r} unresolved ({err})")
    return errors


# ---------------------------------------------------------------------------
# Index coherence
# ---------------------------------------------------------------------------


def _validate_source_paths(manifest: dict) -> list[str]:
    errors: list[str] = []
    for bucket in ("rules", "skills", "workflows"):
        for entry in manifest.get(bucket, []) or []:
            src = entry.get("source")
            if not src:
                errors.append(f"{bucket}:{entry.get('id')}: missing source path")
                continue
            if not (REPO_ROOT / src).exists():
                errors.append(f"{bucket}:{entry.get('id')}: source does not exist: {src}")
    return errors


def _validate_surface_roots(manifest: dict) -> list[str]:
    errors: list[str] = []
    for name, surface in (manifest.get("surfaces") or {}).items():
        status = surface.get("status")
        if status in {"authoritative", "adapter", "canonical", "mirror"}:
            for role, rel in (surface.get("roots") or {}).items():
                p = REPO_ROOT / rel
                if not p.exists():
                    errors.append(f"surfaces.{name}.roots.{role}: path does not exist: {rel}")
        # `planned` surfaces may have empty roots — that is the slot.
    return errors


def _validate_mode_enum(manifest: dict) -> list[str]:
    errors: list[str] = []
    for bucket in ("skills", "workflows"):
        for entry in manifest.get(bucket) or []:
            mode = entry.get("mode")
            if mode is not None and mode not in MODE_STRICTNESS:
                errors.append(
                    f"{bucket}:{entry.get('id')}: invalid mode {mode!r}; must be one of {list(MODE_STRICTNESS)}"
                )
    return errors


def _validate_domain_enum(manifest: dict) -> list[str]:
    """Skill `domain:` values must be from DOMAIN_VALUES when present (ADR-041)."""
    errors: list[str] = []
    for entry in manifest.get("skills") or []:
        domain = entry.get("domain")
        if domain is not None and domain not in DOMAIN_VALUES:
            errors.append(
                f"skills:{entry.get('id')}: invalid domain {domain!r}; must be one of {sorted(DOMAIN_VALUES)}"
            )
    return errors


# Where each tool discovers skills, from its own documentation (checked
# 2026-09-26). Deliberately NOT read from the manifest: the checks below
# compare the rendered surface with the manifest, so a manifest naming a
# directory the tool never reads passed them by construction. Cursor and
# Codex received flat pointers that neither loaded, with this validator green
# (ADR-027 §9).
#   Claude Code  https://code.claude.com/docs/en/skills           .claude/skills/<id>/SKILL.md
#   Cursor       https://cursor.com/docs/skills                    .cursor/skills/ or .agents/skills/<id>/SKILL.md
#   Codex        https://learn.chatgpt.com/docs/build-skills       .agents/skills/<id>/SKILL.md, nothing else
SKILL_DISCOVERY_ROOTS: dict[str, tuple[str, ...]] = {
    "claude": (".claude/skills",),
    "cursor": (".cursor/skills", ".agents/skills"),
    "codex": (".agents/skills",),
}
LEGACY_SKILL_ROOTS = (".cursor/skills", ".codex/skills")


def _validate_skill_discovery(manifest: dict) -> list[str]:
    """Each pointer surface publishes skills where its tool looks, and nowhere stale.

    A pointer surface with no discovery entry is an error too, so a new tool
    cannot arrive with its discovery unverified.
    """
    errors: list[str] = []
    surfaces = manifest.get("surfaces") or {}
    indexes = {
        REPO_ROOT / (spec.get("roots") or {})["skills_index"]
        for spec in surfaces.values()
        if "skills_index" in (spec.get("roots") or {})
    }
    flat_reported: set[Path] = set()
    for surface, spec in sorted(surfaces.items()):
        if _surface_kind(spec) != "pointer":
            continue
        root = (spec.get("roots") or {}).get("skills")
        if not root:
            continue
        readable = SKILL_DISCOVERY_ROOTS.get(surface)
        if readable is None:
            errors.append(f"surfaces.{surface}: no skill discovery contract recorded — add the tool's layout")
            continue
        if root.rstrip("/") not in readable:
            errors.append(
                f"surfaces.{surface}.roots.skills={root!r} is not a directory {surface} reads skills from "
                f"({', '.join(readable)})"
            )
        # A flat file beside the `<id>/` directories is never loaded. Checked
        # here because a stale `<id>/SKILL.md` from an earlier render would
        # otherwise satisfy every pointer check while a fresh render — a new
        # service — gets only the flat files.
        for flat in sorted((REPO_ROOT / root).glob("*.md")):
            if flat in indexes or flat in flat_reported:
                continue
            flat_reported.add(flat)
            errors.append(
                f"{flat.relative_to(REPO_ROOT)}: a flat file in a skills root; {surface} loads only <id>/SKILL.md"
            )
    for legacy in LEGACY_SKILL_ROOTS:
        for flat in sorted((REPO_ROOT / legacy).glob("*.md")):
            body = flat.read_text(encoding="utf-8")
            if "**Canonical source**: `agentic/skills/" in body or "Skills Index" in body:
                errors.append(
                    f"{flat.relative_to(REPO_ROOT)}: a flat skill pointer from the old layout no tool loads "
                    "(run sync_agentic_adapters.py, which removes it)"
                )
    return errors


def _adapter_path(surface: str, roots: dict, bucket: str, item_id: str) -> Path | None:
    """Return the expected adapter pointer path for a surface item."""
    if bucket == "rules":
        root = roots.get("rules")
        if not root:
            return None
        suffix = ".mdc" if surface == "cursor" else ".md"
        return REPO_ROOT / root / f"{item_id}{suffix}"
    if bucket == "skills":
        root = roots.get("skills")
        if not root:
            return None
        # Claude Code, Cursor and Codex only discover skills laid out as
        # <id>/SKILL.md with frontmatter; the pointer adopts that layout
        # (still zero policy text).
        if surface in SKILL_DISCOVERY_ROOTS:
            return REPO_ROOT / root / item_id / "SKILL.md"
        return REPO_ROOT / root / f"{item_id}.md"
    if bucket == "workflows":
        root = roots.get("workflows") or roots.get("commands")
        if not root:
            return None
        return REPO_ROOT / root / f"{item_id}.md"
    return None


def _surface_kind(spec: dict) -> str | None:
    """Return the surface kind, mapping legacy statuses for back-compat."""
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


def _canonical_roots(manifest: dict) -> dict:
    for spec in (manifest.get("surfaces") or {}).values():
        if _surface_kind(spec) == "canonical":
            return spec.get("roots") or {}
    return {}


def _mirror_path(source: str, bucket: str, canon_roots: dict, roots: dict) -> Path | None:
    """Path of a mirror-surface body: source with canonical root swapped."""
    canon_root = canon_roots.get(bucket)
    mirror_root = roots.get(bucket)
    if not canon_root or not mirror_root:
        return None
    try:
        rel = Path(source).relative_to(canon_root)
    except ValueError:
        return None
    return REPO_ROOT / mirror_root / rel


def _validate_adapter_pointers(manifest: dict) -> list[str]:
    """Validate generated surfaces (ADR-027).

    - canonical: the source itself; nothing to check.
    - mirror (.devin/): full body, byte-identical to the canonical source.
    - pointer (.cursor/.claude/.codex): thin pointer to source + AGENTS.md.
    """
    errors: list[str] = []
    surfaces = manifest.get("surfaces") or {}
    canon_roots = _canonical_roots(manifest)
    for bucket in ("rules", "skills", "workflows"):
        for entry in manifest.get(bucket, []) or []:
            item_id = entry.get("id")
            source = entry.get("source")
            for surface in entry.get("surfaces") or []:
                spec = surfaces.get(surface) or {}
                kind = _surface_kind(spec)
                if kind == "canonical":
                    continue
                if kind == "mirror":
                    mpath = _mirror_path(source, bucket, canon_roots, spec.get("roots") or {})
                    if mpath is None:
                        errors.append(f"{bucket}:{item_id}: surfaces.{surface}.roots missing {bucket} mirror root")
                        continue
                    if not mpath.exists():
                        errors.append(
                            f"{bucket}:{item_id}: mirror body missing for {surface}: "
                            f"{mpath.relative_to(REPO_ROOT)} (run sync_agentic_adapters.py)"
                        )
                        continue
                    if source and (REPO_ROOT / source).exists():
                        if mpath.read_bytes() != (REPO_ROOT / source).read_bytes():
                            errors.append(
                                f"{bucket}:{item_id}: mirror {mpath.relative_to(REPO_ROOT)} "
                                f"is not byte-identical to canonical {source} "
                                "(run sync_agentic_adapters.py)"
                            )
                    continue
                if kind != "pointer":
                    errors.append(f"{bucket}:{item_id}: surface {surface!r} has unknown kind {kind!r}")
                    continue
                path = _adapter_path(surface, spec.get("roots") or {}, bucket, item_id)
                if path is None:
                    errors.append(f"{bucket}:{item_id}: surfaces.{surface}.roots missing {bucket} adapter root")
                    continue
                if not path.exists():
                    errors.append(
                        f"{bucket}:{item_id}: adapter pointer missing for {surface}: {path.relative_to(REPO_ROOT)}"
                    )
                    continue
                body = path.read_text(encoding="utf-8")
                if source and source not in body:
                    errors.append(
                        f"{bucket}:{item_id}: adapter {path.relative_to(REPO_ROOT)} "
                        f"does not reference canonical source {source}"
                    )
                if "AGENTS.md" not in body:
                    errors.append(
                        f"{bucket}:{item_id}: adapter {path.relative_to(REPO_ROOT)} "
                        "does not reference AGENTS.md authority"
                    )
                # Loadability (R6 audit S2-1): pointer EXISTENCE does not
                # prove the IDE can LOAD it — the claude surface shipped
                # invisible flat skills for months because only existence
                # was checked. Assert the discoverable-format contract, on every
                # surface whose tool discovers skills — Cursor and Codex then
                # shipped the same flat layout, unloaded, for the same reason.
                if surface in SKILL_DISCOVERY_ROOTS and bucket == "skills":
                    errors.extend(_skill_loadability(surface, path, item_id, body))
    return errors


def _skill_loadability(surface: str, path: Path, item_id: str, body: str) -> list[str]:
    """Claude Code, Cursor and Codex only load `<id>/SKILL.md` whose YAML
    frontmatter parses and carries a `name` matching the directory plus a
    non-empty `description` (<= 1024 chars, the Agent Skills limit)."""
    rel = path.relative_to(REPO_ROOT)
    if not body.startswith("---"):
        return [f"skills:{item_id}: {rel} missing YAML frontmatter ({surface} will not load it)"]
    parts = body.split("---", 2)
    if len(parts) < 3:
        return [f"skills:{item_id}: {rel} frontmatter is not closed with `---`"]
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        return [f"skills:{item_id}: {rel} frontmatter does not parse as YAML: {exc}"]
    errors: list[str] = []
    if meta.get("name") != item_id:
        errors.append(f"skills:{item_id}: {rel} frontmatter name={meta.get('name')!r} must equal the skill id")
    desc = str(meta.get("description") or "").strip()
    if not desc:
        errors.append(f"skills:{item_id}: {rel} frontmatter description is empty")
    elif len(desc) > 1024:
        errors.append(
            f"skills:{item_id}: {rel} frontmatter description is {len(desc)} chars (> 1024 truncation budget)"
        )
    return errors


# ---------------------------------------------------------------------------
# Context layer (F1)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "Google API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PEM private key"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub personal token"),
    (re.compile(r"xox[aboprs]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    # URLs with embedded credentials (rough but catches the common case).
    (re.compile(r"https?://[^\s:@/]+:[^\s@/]+@"), "URL with embedded credential"),
]


def _scan_for_secret_patterns(text: str) -> list[str]:
    hits: list[str] = []
    for regex, label in _SECRET_PATTERNS:
        if regex.search(text):
            hits.append(label)
    return hits


# Files whose JSON Schema validation could not run because jsonschema is
# absent. Reported explicitly: a skipped check must never read as a passed one.
skipped_schema_checks: list[str] = []


def _load_schema() -> dict:
    if not SCHEMA.exists():
        raise ValidationError(f"missing schema: {SCHEMA}")
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def validate_context_examples(strict: bool) -> list[str]:
    errors: list[str] = []
    schema = _load_schema()
    for kind, path in CONTEXT_EXAMPLES.items():
        if not path.exists():
            errors.append(f"missing context example: {path}")
            continue
        raw = path.read_text(encoding="utf-8")
        secrets = _scan_for_secret_patterns(raw)
        if secrets:
            errors.append(
                f"{path.name}: secret-pattern match ({', '.join(secrets)}) "
                "— example files must not contain real-looking credentials"
            )
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: YAML parse error: {exc}")
            continue
        try:
            if not HAVE_JSONSCHEMA:
                skipped_schema_checks.append(str(path))
                continue
            jsonschema.validate(doc, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"{path.name}: schema violation: {exc.message}")
        # Placeholders are REQUIRED in example files, so don't complain
        # about them here. Complaint happens in the .local.yaml check.
        _ = kind

    if strict:
        errors.extend(_validate_local_yamls_if_present(schema))
    return errors


def _validate_local_yamls_if_present(schema: dict) -> list[str]:
    errors: list[str] = []
    config_dir = _find_config_dir(REPO_ROOT)
    for candidate in config_dir.glob("*_context.local*.yaml"):
        raw = candidate.read_text(encoding="utf-8")
        placeholders = PLACEHOLDER_RE.findall(raw)
        # Strip legitimate curly content from YAML key names (none expected).
        if placeholders:
            errors.append(f"{candidate.name}: unreplaced placeholders: {sorted(set(placeholders))}")
        secrets = _scan_for_secret_patterns(raw)
        if secrets:
            errors.append(
                f"{candidate.name}: secret-pattern match ({', '.join(secrets)}) "
                "— secrets belong in Secret Manager + IRSA/WI, not in context files"
            )
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            errors.append(f"{candidate.name}: YAML parse error: {exc}")
            continue
        try:
            if not HAVE_JSONSCHEMA:
                skipped_schema_checks.append(str(candidate))
                continue
            jsonschema.validate(doc, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"{candidate.name}: schema violation: {exc.message}")
        # Check escalation overrides never de-escalate.
        overrides = (doc or {}).get("agentic_policy", {}).get("escalation_overrides", {})
        for name, override_mode in overrides.items():
            # Default base-mode for overrides is CONSULT unless the adopter
            # also declared a base in the manifest. Since overrides can only
            # STRENGTHEN, a value of AUTO is always a regression unless the
            # default was already AUTO — which we cannot prove here without
            # the manifest linkage. Conservative check: `AUTO` in an override
            # is always suspicious because overrides exist to escalate.
            if override_mode == "AUTO":
                errors.append(
                    f"{candidate.name}: escalation_overrides.{name}={override_mode!r} "
                    "de-escalates (overrides may only STRENGTHEN modes)"
                )
    return errors


# ---------------------------------------------------------------------------
# Context pointer format (F3)
# ---------------------------------------------------------------------------


def _table_row_counts(text: str) -> list[int]:
    """Return the row count for each Markdown table found in text."""
    rows: list[int] = []
    current = 0
    in_table = False
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            if not in_table:
                in_table = True
                current = 1
                continue
            # Separator row `|---|---|` does not count as data.
            if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
                continue
            current += 1
        else:
            if in_table:
                rows.append(current)
                in_table = False
                current = 0
    if in_table:
        rows.append(current)
    return rows


def validate_context_pointers() -> list[str]:
    errors: list[str] = []
    for path in CONTEXT_POINTERS:
        if not path.exists():
            # AGENT_CONTEXT.md is required; the .*_context.md pointers
            # are required only for surfaces declared authoritative/adapter.
            if path.name == "AGENT_CONTEXT.md":
                errors.append(f"missing canonical context: {path}")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > CONTEXT_POINTER_MAX_LINES:
            errors.append(
                f"{path.name}: {len(lines)} lines exceeds CONTEXT_POINTER_MAX_LINES={CONTEXT_POINTER_MAX_LINES}"
            )
        # No date-led lines (prevents diary drift).
        for n, line in enumerate(lines, start=1):
            if re.match(r"^\s*(?:##+\s+)?20\d{2}[\-/]", line):
                errors.append(
                    f"{path.name}:{n}: date-led line {line.strip()!r} (context files must not drift into diaries)"
                )
                break
        # No oversized tables.
        for idx, count in enumerate(_table_row_counts("\n".join(lines))):
            if count > CONTEXT_POINTER_MAX_TABLE_ROWS:
                errors.append(
                    f"{path.name}: table #{idx + 1} has {count} rows (limit is {CONTEXT_POINTER_MAX_TABLE_ROWS})"
                )
    return errors


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run(strict: bool) -> dict:
    results: dict[str, list[str]] = {}
    if not MANIFEST.exists():
        raise ValidationError(f"missing manifest: {MANIFEST}")
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValidationError("manifest did not parse to a mapping")
    results["authority_chain"] = validate_authority_chain(manifest)
    results["source_paths"] = _validate_source_paths(manifest)
    results["surface_roots"] = _validate_surface_roots(manifest)
    results["adapter_pointers"] = _validate_adapter_pointers(manifest)
    results["skill_discovery"] = _validate_skill_discovery(manifest)
    results["mode_enum"] = _validate_mode_enum(manifest)
    results["domain_enum"] = _validate_domain_enum(manifest)
    results["context_examples"] = validate_context_examples(strict=strict)
    results["context_pointers"] = validate_context_pointers()
    results["reports_block"] = _validate_reports_block(manifest)
    return results


def _validate_reports_block(manifest: dict) -> list[str]:
    """Validate the `reports:` section added in ADR-023 F6.

    Allowed states:
      - block absent (back-compat for older forks)
      - block present and structurally valid

    Validations:
      * `schema`, `library`, `cli` paths exist on disk
      * `storage_root` is a directory that exists
      * `types` lists exactly the four canonical types in any order
      * each `types[].producer` references a known workflow id
      * each `types[].mode` is AUTO|CONSULT|STOP
    """
    errors: list[str] = []
    block = manifest.get("reports")
    if block is None:
        return errors
    if not isinstance(block, dict):
        return ["reports: block must be a mapping"]
    for path_key in ("schema", "library", "cli"):
        rel = block.get(path_key)
        if not rel:
            errors.append(f"reports.{path_key}: missing")
            continue
        if not (REPO_ROOT / rel).exists():
            errors.append(f"reports.{path_key}: path does not exist ({rel})")
    storage = block.get("storage_root")
    if storage:
        if not (REPO_ROOT / storage).is_dir():
            errors.append(f"reports.storage_root: not a directory ({storage})")
    types = block.get("types") or []
    canonical = {"release", "drift", "training", "incident"}
    seen = {t.get("id") for t in types if isinstance(t, dict)}
    missing = canonical - seen
    extra = seen - canonical
    if missing:
        errors.append(f"reports.types missing canonical entries: {sorted(missing)}")
    if extra:
        errors.append(f"reports.types has non-canonical entries: {sorted(extra)}")
    workflow_ids = {w.get("id") for w in (manifest.get("workflows") or [])}
    skill_ids = {s.get("id") for s in (manifest.get("skills") or [])}
    known_actions = workflow_ids | skill_ids
    for entry in types:
        if not isinstance(entry, dict):
            errors.append(f"reports.types[*]: not a mapping ({entry!r})")
            continue
        prod = entry.get("producer")
        if prod and prod not in known_actions:
            errors.append(f"reports.types[id={entry.get('id')!r}].producer: references unknown skill/workflow {prod!r}")
        mode = entry.get("mode")
        if mode not in ("AUTO", "CONSULT", "STOP"):
            errors.append(f"reports.types[id={entry.get('id')!r}].mode: invalid value {mode!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also validate any *_context.local*.yaml present.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit results as JSON on stdout (stderr unchanged).",
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
    global REPO_ROOT, MANIFEST, SCHEMA, CONTEXT_EXAMPLES, _CONFIG_DIR
    if args.root is not None:
        REPO_ROOT = args.root.resolve()
        _CONFIG_DIR = _find_config_dir(REPO_ROOT)
        MANIFEST = _CONFIG_DIR / "agentic_manifest.yaml"
        SCHEMA = _CONFIG_DIR / "context.schema.json"
        CONTEXT_EXAMPLES = {
            "company": _CONFIG_DIR / "company_context.example.yaml",
            "project": _CONFIG_DIR / "project_context.example.yaml",
        }
    if args.manifest is not None:
        MANIFEST = args.manifest.resolve()
    try:
        results = run(strict=args.strict)
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    total = sum(len(v) for v in results.values())
    if args.json:
        print(json.dumps({"errors": results, "total": total}, indent=2))
    else:
        for check, errors in results.items():
            if errors:
                print(f"[FAIL] {check}", file=sys.stderr)
                for err in errors:
                    print(f"  - {err}", file=sys.stderr)
            else:
                print(f"[ OK ] {check}")
    if skipped_schema_checks:
        # A skipped check must never read as a passed one.
        print(
            f"note: JSON Schema validation SKIPPED for {len(skipped_schema_checks)} file(s) "
            "— jsonschema is not installed. Every other check ran. Install it "
            "(pip install jsonschema) for full strictness; CI always has it.",
            file=sys.stderr,
        )
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
