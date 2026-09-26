"""Contract test — skills are rendered where each tool discovers them (ADR-027 §9).

Claude Code, Cursor and Codex load a skill only from `<root>/<id>/SKILL.md`
with `name` and `description` front-matter, and each reads specific roots.
Until 2026-09 only the Claude surface used that layout: Cursor received flat
`.cursor/skills/<id>.md` files and Codex flat `.codex/skills/<id>.md` files,
a directory Codex never reads. Neither tool listed a single skill, and
`validate_agentic_manifest.py --strict` stayed green, because it compared the
rendered files with the manifest, and the manifest named those places.

These tests pin what was missing: a check against the TOOLS, from a table kept
apart from the manifest, and a renderer that can share `.agents/skills/`
between Cursor and Codex without the two fighting over it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "templates/config/agentic_manifest.yaml"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate = _load("validate_agentic_manifest")
sync = _load("sync_agentic_adapters")

SKILL = {"id": "deploy", "source": "agentic/skills/deploy/SKILL.md", "mode": "CONSULT"}
CANONICAL = "---\nname: deploy\ndescription: Ship it\nmode: CONSULT\n---\n# deploy\n"


def _manifest(roots: dict[str, str], skills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    surfaces: dict[str, Any] = {
        "agentic": {"kind": "canonical", "roots": {"skills": "agentic/skills"}},
    }
    for surface, root in roots.items():
        surfaces[surface] = {"kind": "pointer", "roots": {"skills": root}}
    listed = skills if skills is not None else [{**SKILL, "surfaces": sorted(roots)}]
    return {"surfaces": surfaces, "skills": listed}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "agentic/skills/deploy").mkdir(parents=True)
    (tmp_path / "agentic/skills/deploy/SKILL.md").write_text(CANONICAL, encoding="utf-8")
    (tmp_path / "agentic/skills/audit").mkdir(parents=True)
    (tmp_path / "agentic/skills/audit/SKILL.md").write_text(CANONICAL.replace("deploy", "audit"), encoding="utf-8")
    monkeypatch.setattr(validate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
    return tmp_path


# --- the repository as it is ----------------------------------------------------


def test_every_pointer_surface_publishes_skills_where_its_tool_looks() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert validate._validate_skill_discovery(manifest) == []


def test_every_skill_pointer_is_loadable_on_every_tool() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for surface in validate.SKILL_DISCOVERY_ROOTS:
        root = manifest["surfaces"][surface]["roots"]["skills"]
        for skill in manifest["skills"]:
            if surface not in skill.get("surfaces", []):
                continue
            path = REPO_ROOT / root / skill["id"] / "SKILL.md"
            assert path.is_file(), f"{surface}: {path.relative_to(REPO_ROOT)} missing"
            body = path.read_text(encoding="utf-8")
            assert validate._skill_loadability(surface, path, skill["id"], body) == []


# --- the check is against the tools, not the manifest -------------------------


def test_the_old_layout_fails_the_discovery_contract(world: Path) -> None:
    """Codex pointed at `.codex/skills/` passed every manifest-relative check."""
    old = _manifest({"claude": ".claude/skills", "cursor": ".cursor/skills", "codex": ".codex/skills"})
    errors = validate._validate_skill_discovery(old)
    assert any("surfaces.codex.roots.skills='.codex/skills'" in e for e in errors), errors


def test_a_flat_pointer_is_not_where_any_tool_looks(world: Path) -> None:
    """Cursor's old root is one it reads, but a flat file there is never loaded."""
    manifest = _manifest({"cursor": ".cursor/skills"})
    flat = world / ".cursor/skills/deploy.md"
    flat.parent.mkdir(parents=True)
    flat.write_text("**Canonical source**: `agentic/skills/deploy/SKILL.md`\nAGENTS.md\n", encoding="utf-8")
    assert validate._adapter_path("cursor", {"skills": ".cursor/skills"}, "skills", "deploy") == (
        world / ".cursor/skills/deploy/SKILL.md"
    )
    assert any("flat skill pointer from the old layout" in e for e in validate._validate_skill_discovery(manifest))


def test_a_new_pointer_surface_needs_a_discovery_contract(world: Path) -> None:
    errors = validate._validate_skill_discovery(_manifest({"windsurf": ".windsurf/skills"}))
    assert any("surfaces.windsurf: no skill discovery contract" in e for e in errors), errors


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        ("", "missing YAML frontmatter (cursor will not load it)"),
        ("---\nname: other\ndescription: x\n---\n", "must equal the skill id"),
        ("---\nname: deploy\n---\n", "description is empty"),
    ],
)
def test_loadability_is_checked_for_cursor_and_codex_too(world: Path, head: str, expected: str) -> None:
    path = world / ".agents/skills/deploy/SKILL.md"
    errors = validate._skill_loadability("cursor", path, "deploy", head + "# deploy\n")
    assert any(expected in e for e in errors), errors


def test_a_flat_file_beside_the_skill_directories_is_reported(world: Path) -> None:
    """A stale `<id>/SKILL.md` must not hide a fresh render that wrote flat files."""
    manifest = _manifest({"cursor": ".agents/skills"})
    manifest["surfaces"]["cursor"]["roots"]["skills_index"] = ".agents/skills/INDEX.md"
    (world / ".agents/skills").mkdir(parents=True)
    (world / ".agents/skills/INDEX.md").write_text("# index\n", encoding="utf-8")
    (world / ".agents/skills/deploy.md").write_text("# deploy\n", encoding="utf-8")
    errors = validate._validate_skill_discovery(manifest)
    assert errors == [".agents/skills/deploy.md: a flat file in a skills root; cursor loads only <id>/SKILL.md"]


def test_the_validator_runs_the_discovery_check() -> None:
    """Called directly, the functions above prove nothing about the CLI."""
    results = validate.run(strict=False)
    assert results["skill_discovery"] == []


def test_the_pointer_walk_checks_loadability_on_cursor(world: Path) -> None:
    """Integration: the walk every pointer goes through, not the helper alone."""
    manifest = _manifest({"cursor": ".agents/skills"})
    pointer = world / ".agents/skills/deploy/SKILL.md"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("# deploy\n`agentic/skills/deploy/SKILL.md` AGENTS.md\n", encoding="utf-8")
    errors = validate._validate_adapter_pointers(manifest)
    assert any("missing YAML frontmatter (cursor will not load it)" in e for e in errors), errors


# --- the renderer ---------------------------------------------------------------


def test_a_shared_root_is_rendered_once_and_settles(world: Path) -> None:
    """Cursor and Codex share `.agents/skills/`; each run must leave it as the last one did."""
    manifest = _manifest(
        {"cursor": ".agents/skills", "codex": ".agents/skills"},
        skills=[
            {**SKILL, "surfaces": ["codex", "cursor"]},
            {"id": "audit", "source": "agentic/skills/audit/SKILL.md", "mode": "AUTO", "surfaces": ["cursor"]},
        ],
    )
    manifest["surfaces"]["cursor"]["roots"]["skills_index"] = ".agents/skills/INDEX.md"

    assert sync.render(manifest, check=False) == 0
    assert sync.render(manifest, check=True) == 0, "a second run changed files — the shared root does not settle"

    shared = (world / ".agents/skills/deploy/SKILL.md").read_text(encoding="utf-8")
    only_cursor = (world / ".agents/skills/audit/SKILL.md").read_text(encoding="utf-8")
    assert "**Adapter surface**: `codex`, `cursor`" in shared
    assert "**Adapter surface**: `cursor`" in only_cursor, "a skill Codex does not list was pruned or mislabelled"
    assert (world / ".agents/skills/INDEX.md").is_file(), "one surface's pass pruned the other's index"


def test_render_migrates_generated_flat_pointers_and_keeps_hand_written_files(world: Path) -> None:
    legacy = world / ".codex/skills"
    legacy.mkdir(parents=True)
    (legacy / "deploy.md").write_text("**Canonical source**: `agentic/skills/deploy/SKILL.md`\n", encoding="utf-8")
    (world / ".cursor/skills").mkdir(parents=True)
    (world / ".cursor/skills/notes.md").write_text("my own notes\n", encoding="utf-8")

    sync.render(_manifest({"codex": ".agents/skills"}), check=False)

    assert not (legacy / "deploy.md").exists()
    assert not legacy.exists(), "the emptied legacy directory was left behind"
    assert (world / ".cursor/skills/notes.md").read_text(encoding="utf-8") == "my own notes\n"
    assert (world / ".agents/skills/deploy/SKILL.md").is_file()
