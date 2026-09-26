"""Contract test — Codex adapter (ADR-023 F5).

Authority: `docs/decisions/ADR-023-agentic-portability-and-context.md`.

Locks the structural invariants that distinguish "adapter" from "fork":

Skill pointers, never copies
  Each `.codex/{rules,workflows}/<id>.md` and each skill pointer
  `.agents/skills/<id>/SKILL.md` MUST reference its canonical `agentic/`
  source. Skills live in `.agents/skills/` because it is the only place
  Codex discovers them in a repository (ADR-027 §9); the directory is
  shared with Cursor. Without this rule the Codex directory
  drifts into a parallel agentic surface with no propagation contract.

Manifest declares the pointer
  Every skill listed in `agentic_manifest.yaml` with `codex` in
  `surfaces:` MUST have a matching pointer in `.agents/skills/`.
  Reverse: every pointer there MUST be listed in the manifest with
  `codex` in `surfaces:`.

MCP example only — live config never committed
  `.codex/mcp.example.json` exists and parses; `.codex/mcp.json`
  is NOT tracked by git (ADR-023 I-2 extended to MCP configs).

Automation files reference only AUTO/CONSULT-class operations
  Automations under `.codex/automations/` MUST NOT claim authority
  to perform STOP-class operations. Catches a future PR that adds
  a "production-deploy" automation in violation of AGENTS.md
  permissions matrix.

Surface entry coherent
  `agentic_manifest.yaml#surfaces.codex` declares status `adapter`
  with the documented roots and a `.codex_context.md` pointer.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CODEX_DIR = REPO_ROOT / ".codex"
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"  # ADR-027 §9: where Codex discovers skills
RULES_DIR = CODEX_DIR / "rules"
WORKFLOWS_DIR = CODEX_DIR / "workflows"
AUTOMATIONS_DIR = CODEX_DIR / "automations"
CANONICAL_SKILLS = REPO_ROOT / "agentic/skills"
CANONICAL_RULES = REPO_ROOT / "agentic/rules"
CANONICAL_WORKFLOWS = REPO_ROOT / "agentic/workflows"
MANIFEST = REPO_ROOT / "templates/config/agentic_manifest.yaml"
MCP_EXAMPLE = CODEX_DIR / "mcp.example.json"
MCP_LIVE = CODEX_DIR / "mcp.json"
CONTEXT_POINTER = REPO_ROOT / ".codex_context.md"


def _load_manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_codex_dir_layout() -> None:
    """Sanity — adapter directory has the expected shape."""
    assert CODEX_DIR.exists() and CODEX_DIR.is_dir()
    assert (CODEX_DIR / "README.md").exists()
    assert RULES_DIR.exists() and RULES_DIR.is_dir()
    assert SKILLS_DIR.exists() and SKILLS_DIR.is_dir()
    assert WORKFLOWS_DIR.exists() and WORKFLOWS_DIR.is_dir()
    assert AUTOMATIONS_DIR.exists() and AUTOMATIONS_DIR.is_dir()
    assert MCP_EXAMPLE.exists()
    assert CONTEXT_POINTER.exists()


def test_manifest_codex_surface_is_adapter() -> None:
    """Manifest declares Codex as an adapter (not planned, not fork)."""
    doc = _load_manifest()
    codex = (doc.get("surfaces") or {}).get("codex")
    assert codex is not None, "manifest missing surfaces.codex"
    assert codex.get("status") == "adapter", f"surfaces.codex.status must be 'adapter' (got {codex.get('status')!r})"
    roots = codex.get("roots") or {}
    for required_root in ("readme", "rules", "skills", "workflows", "automations", "mcp_example"):
        assert required_root in roots, f"surfaces.codex.roots missing key {required_root!r}"


def test_codex_skill_pointers_reference_canonical() -> None:
    """Every `.agents/skills/<id>/SKILL.md` references its
    canonical SKILL.md. This is the structural barrier against the
    Codex directory becoming a parallel fork.
    """
    pointers = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert pointers, "expected at least one Codex skill pointer"
    for ptr in pointers:
        body = ptr.read_text(encoding="utf-8")
        sid = ptr.parent.name
        canonical_ref = f"agentic/skills/{sid}/SKILL.md"
        assert canonical_ref in body, (
            f"{ptr.relative_to(REPO_ROOT)} must reference its canonical "
            f"source ({canonical_ref}). Pointer files cannot stand alone "
            "— see ADR-023 F5."
        )
        # Every pointer must declare its authority anchor.
        assert re.search(r"^\*\*Authority\*\*:", body, re.MULTILINE), f"{ptr.name} missing **Authority**: line"


def test_codex_skills_match_manifest_surfaces_codex() -> None:
    """Every skill listed in the manifest with `codex` in `surfaces:`
    has a matching pointer; every pointer file appears in the manifest.
    """
    doc = _load_manifest()
    declared_codex_skills = {s["id"] for s in (doc.get("skills") or []) if "codex" in (s.get("surfaces") or [])}
    pointer_skills = {p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")}
    missing_pointers = declared_codex_skills - pointer_skills
    extra_pointers = pointer_skills - declared_codex_skills
    assert not missing_pointers, (
        f"manifest claims codex consumes {sorted(missing_pointers)} but no pointer exists in .agents/skills/"
    )
    assert not extra_pointers, (
        f"pointer files exist for {sorted(extra_pointers)} but the manifest does not list them under surfaces.codex"
    )


def test_codex_rules_match_manifest_surfaces_codex() -> None:
    """Codex consumes every canonical rule through a generated pointer."""
    doc = _load_manifest()
    declared = {r["id"] for r in (doc.get("rules") or []) if "codex" in (r.get("surfaces") or [])}
    pointers = {p.stem for p in RULES_DIR.glob("*.md")}
    assert pointers == declared
    for rid in pointers:
        body = (RULES_DIR / f"{rid}.md").read_text(encoding="utf-8")
        assert f"agentic/rules/{rid}.md" in body
        assert "AGENTS.md" in body


def test_codex_workflows_match_manifest_surfaces_codex() -> None:
    """Codex consumes every canonical workflow through a generated pointer."""
    doc = _load_manifest()
    declared = {w["id"] for w in (doc.get("workflows") or []) if "codex" in (w.get("surfaces") or [])}
    pointers = {p.stem for p in WORKFLOWS_DIR.glob("*.md")}
    assert pointers == declared
    for wid in pointers:
        body = (WORKFLOWS_DIR / f"{wid}.md").read_text(encoding="utf-8")
        assert f"agentic/workflows/{wid}.md" in body
        assert "AGENTS.md" in body


def test_canonical_skill_files_exist() -> None:
    """Every Codex pointer's canonical SKILL.md exists.

    Caught a regression during F5 development where a typo in the
    pointer (debug_ml_inference vs debug-ml-inference) silently
    desynced from the canonical tree.
    """
    for ptr in SKILLS_DIR.glob("*/SKILL.md"):
        sid = ptr.parent.name
        canonical = CANONICAL_SKILLS / sid / "SKILL.md"
        assert canonical.exists(), (
            f"Codex pointer {sid}/SKILL.md references a non-existent "
            f"canonical SKILL.md at {canonical.relative_to(REPO_ROOT)}"
        )

    for ptr in RULES_DIR.glob("*.md"):
        rid = ptr.stem
        canonical = CANONICAL_RULES / f"{rid}.md"
        assert canonical.exists(), f"missing canonical rule for {ptr.name}"

    for ptr in WORKFLOWS_DIR.glob("*.md"):
        wid = ptr.stem
        canonical = CANONICAL_WORKFLOWS / f"{wid}.md"
        assert canonical.exists(), f"missing canonical workflow for {ptr.name}"


def test_mcp_example_parses_and_lists_required_servers() -> None:
    """The example MCP config is valid JSON and lists required servers."""
    raw = MCP_EXAMPLE.read_text(encoding="utf-8")
    doc = json.loads(raw)
    servers = doc.get("mcpServers") or {}
    required = {"github", "kubectl", "terraform", "prometheus"}
    missing = required - set(servers.keys())
    assert not missing, f"mcp.example.json missing required servers: {sorted(missing)}"


def test_live_mcp_json_is_not_tracked() -> None:
    """ADR-023 I-2 extends to MCP configs: `.codex/mcp.json` lives only
    on the adopter's workstation, never in git. Hard guard so a
    careless `git add -f` does not leak credentials.
    """
    proc = subprocess.run(
        ["git", "ls-files", ".codex/mcp.json"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
    )
    tracked = [line for line in proc.stdout.splitlines() if line.strip()]
    assert not tracked, (
        f".codex/mcp.json is tracked by git (violates ADR-023 I-2 / D-18): {tracked}. Remove and re-run."
    )


@pytest.mark.parametrize(
    "automation",
    sorted(p.name for p in AUTOMATIONS_DIR.glob("*.md")) if AUTOMATIONS_DIR.exists() else [],
)
def test_automation_does_not_claim_stop_authority(automation: str) -> None:
    """Codex automations may be AUTO or CONSULT but NEVER STOP.

    STOP-class operations (production deploys, secret rotation,
    quality-gate overrides) belong in GitHub Actions per the
    AGENTS.md permissions matrix. An automation file claiming STOP
    authority would silently route a STOP operation through Codex's
    cron — exactly the bypass we forbid.
    """
    body = (AUTOMATIONS_DIR / automation).read_text(encoding="utf-8")
    # Allow the word 'STOP' as a label in describing FORBIDDEN behaviour
    # (e.g. "writes are STOP per AGENTS.md"). Forbid only the assertion
    # pattern that would actually grant the automation that mode.
    forbidden_patterns = [
        r"^\s*-\s*STOP\b",  # Mode: \n - STOP
        r"\bMode\s*:\s*STOP\b",  # "Mode: STOP" claim
        r"automation\s+mode\s*:\s*STOP\b",  # "automation mode: STOP"
    ]
    for pat in forbidden_patterns:
        m = re.search(pat, body, re.MULTILINE | re.IGNORECASE)
        assert m is None, (
            f"{automation}: forbidden STOP-mode claim matched pattern "
            f"{pat!r} at: {m.group(0)!r}. Codex automations stay "
            "AUTO/CONSULT; STOP routes through GitHub Actions."
        )


def test_codex_context_pointer_is_compact() -> None:
    """`.codex_context.md` is a 60-line pointer back to AGENT_CONTEXT.md,
    same shape as the other surface pointers."""
    body = CONTEXT_POINTER.read_text(encoding="utf-8")
    lines = body.splitlines()
    assert 0 < len(lines) <= 60, (
        f".codex_context.md has {len(lines)} lines; surface pointers capped at 60 (matches the other surface pointers)"
    )
    assert "AGENT_CONTEXT.md" in body, (
        ".codex_context.md must reference AGENT_CONTEXT.md to keep the authority chain single-rooted"
    )
