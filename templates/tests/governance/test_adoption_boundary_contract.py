"""Contract test for ADR-016 PR-R2-12 — adoption-boundary doc + non-agentic on-ramp.

Three invariants:

1. **Maturity matrix is present** — `docs/ADOPTION.md` exists and contains a
   ratings table (ready/partial/roadmap) plus the canonical non-claims list.

2. **Every /slash workflow has a `make` equivalent** — for each file in
   `agentic/workflows/*.md`, the corresponding `make` target exists in
   `templates/Makefile`. The mapping is canonical (defined in this test);
   if a new workflow is added without a make target, this test fails.

3. **README points at ADOPTION.md** — the README has an "Adoption boundary"
   section that links to the doc; otherwise platform reviewers will miss it.

These three together prevent the agentic surface from quietly becoming
load-bearing: any new workflow must ship its non-agentic equivalent.

Authority: ADR-016 PR-R2-12.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo-root resolution: this test lives under templates/service/tests/, so the
# repo root is 3 parents up.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Canonical workflow → make-target map.
#
# Adding a new /slash workflow without updating this map will fail the
# parity test (test_every_workflow_has_make_target). That is the intended
# defense against silent agentic-surface accumulation.
# ---------------------------------------------------------------------------

WORKFLOW_TO_MAKE: dict[str, str] = {
    "new-service": "new-service",
    "eda": "eda",
    "drift-check": "drift-check",
    "retrain": "retrain",
    "load-test": "load-test",
    "release": "release-checklist",
    "rollback": "rollback",
    "incident": "incident-runbook",
    "performance-review": "performance-review",
    "cost-review": "cost-review",
    "new-adr": "new-adr",
    "secret-breach": "secret-breach-check",
    "scaffold-update": "scaffold-update",
    "doc-coherence": "doc-coherence",
    "stack-switch": "switch-profile",
    "onboard": "onboard",
    "ci-green": "ci-green",
    "edge-setup": "edge-setup",
    "audit-quality": "audit-quality",
    "document-changes": "document-changes",
}


# ---------------------------------------------------------------------------
# 1. ADOPTION.md exists and has the required shape
# ---------------------------------------------------------------------------


def test_adoption_doc_exists() -> None:
    """`docs/ADOPTION.md` must exist (PR-R2-12 deliverable)."""
    path = REPO_ROOT / "docs" / "ADOPTION.md"
    assert path.is_file(), (
        "PR-R2-12 violation: docs/ADOPTION.md is missing. Platform reviewers "
        "have no entry point for the maturity matrix + non-agentic on-ramp."
    )


def test_adoption_doc_has_maturity_matrix() -> None:
    """ADOPTION.md must contain at least one ratings row.

    Ratings are the values `ready` / `partial` / `roadmap` inside markdown
    table cells. Not finding any of them means the matrix has been
    accidentally truncated.
    """
    text = (REPO_ROOT / "docs" / "ADOPTION.md").read_text()
    for rating in ("ready", "partial", "roadmap"):
        assert f"| {rating} |" in text, (
            f"ADOPTION.md missing ratings rows of type {rating!r}. The maturity matrix has been damaged."
        )


def test_adoption_doc_has_non_claims_section() -> None:
    """ADOPTION.md must explicitly state non-claims to prevent over-promising."""
    text = (REPO_ROOT / "docs" / "ADOPTION.md").read_text()
    # Heading varies in wording across edits; check for a stable phrase
    # plus at least 3 of the canonical non-claims so reorders don't break.
    canonical_non_claims = [
        "Multi-region active-active",
        "Compliance certifications",
        "feature store",
    ]
    found = sum(1 for nc in canonical_non_claims if nc in text)
    assert found >= 3, (
        f"ADOPTION.md missing ≥3 canonical non-claims (found {found}). "
        f"Without an explicit non-claims list, README copy can drift "
        f"toward over-promising."
    )


# ---------------------------------------------------------------------------
# 2. Every workflow has a make target
# ---------------------------------------------------------------------------


def test_workflow_to_make_map_matches_filesystem() -> None:
    """The canonical map must list every `agentic/workflows/*.md` file.

    If a new workflow was added without updating WORKFLOW_TO_MAKE, the map
    is stale. Update the map AND add a make target.
    """
    workflow_dir = REPO_ROOT / "agentic" / "workflows"
    if not workflow_dir.is_dir():
        pytest.skip("agentic/workflows/ not present in this checkout")
    on_disk = {p.stem for p in workflow_dir.glob("*.md")}
    in_map = set(WORKFLOW_TO_MAKE.keys())
    missing_in_map = on_disk - in_map
    extra_in_map = in_map - on_disk

    assert not missing_in_map, (
        f"PR-R2-12 violation: workflow(s) without a Makefile mapping in "
        f"WORKFLOW_TO_MAKE: {sorted(missing_in_map)}. Add a make target "
        f"in templates/Makefile and update this test's map."
    )
    assert not extra_in_map, (
        f"WORKFLOW_TO_MAKE references nonexistent workflow(s): {sorted(extra_in_map)}. Stale entries must be removed."
    )


def test_every_workflow_has_make_target() -> None:
    """For each canonical workflow, the make target must exist."""
    makefile = (REPO_ROOT / "templates" / "service" / "Makefile").read_text()
    # Match `^target:` or `^target [target2]:` as the target declaration line.
    target_pattern = re.compile(r"^([a-zA-Z_-][a-zA-Z0-9_-]*)\s*:", re.MULTILINE)
    declared_targets = set(target_pattern.findall(makefile))

    missing: list[str] = []
    for workflow, make_target in WORKFLOW_TO_MAKE.items():
        if make_target not in declared_targets:
            missing.append(f"{workflow} → make {make_target}")
    assert not missing, "PR-R2-12 violation: workflow(s) lack the documented Makefile equivalent:\n  " + "\n  ".join(
        missing
    )


def test_make_targets_appear_in_adoption_doc() -> None:
    """Every make target in WORKFLOW_TO_MAKE must be referenced in ADOPTION.md
    so the user-facing doc can't silently drift from the actual Makefile.
    """
    adoption = (REPO_ROOT / "docs" / "ADOPTION.md").read_text()
    missing: list[str] = []
    for workflow, make_target in WORKFLOW_TO_MAKE.items():
        if f"make {make_target}" not in adoption:
            missing.append(f"`make {make_target}` (from /{workflow})")
    assert not missing, (
        "PR-R2-12 violation: ADOPTION.md does not document the following make targets:\n  " + "\n  ".join(missing)
    )


# ---------------------------------------------------------------------------
# 3. README points at the adoption doc
# ---------------------------------------------------------------------------


def test_readme_links_to_adoption_doc() -> None:
    """README.md must link to docs/ADOPTION.md so reviewers find the matrix."""
    readme = (REPO_ROOT / "README.md").read_text()
    assert "docs/ADOPTION.md" in readme, (
        "PR-R2-12 violation: README.md does not link to docs/ADOPTION.md. "
        "Platform reviewers won't find the maturity matrix."
    )
    # The link must live under an explicit Adoption section so it isn't
    # buried in passing references.
    # A level-3 section inside "What it is" since the README adopted the shared
    # standard's fixed level-2 sections; the pointer and its section stay.
    assert re.search(r"^#{2,3}\s+Adoption\s+boundary", readme, re.MULTILINE), (
        "PR-R2-12 violation: README.md is missing the `## Adoption boundary` "
        "section header. The link to ADOPTION.md must live under that section."
    )
