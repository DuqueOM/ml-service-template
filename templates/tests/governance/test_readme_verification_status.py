"""Contract test — README production-readiness wording discipline (R5-H1).

Authority: ACTION_PLAN_R5 §R5-H1 + audit-r4 evidence policy.

R5-H1 called out that the README used the unqualified phrase
``"Production-ready"`` against every row of the maturity matrix without
drawing the distinction between:

1. Components the template PROVES in its own CI (contracts, scaffold,
   golden-path E2E).
2. Components an adopter must VERIFY in their own environment (L4
   production rollout on their cluster with their traffic).

The softened wording — ``"Production-ready by design"`` — plus a new
§"Verification status" mini-matrix with layers L1..L4 is the honest
statement. These invariants exist so a future edit cannot silently
revert to the unqualified claim without this test failing.

Invariants:

- The phrase ``"Production-ready"`` is ALWAYS followed by
  ``"by design"`` when it appears as a status tag in the maturity
  matrix. Standalone ``"Production-ready"`` is only allowed in
  narrative / prose contexts (quoted-term paragraph, section titles).
- The README has a §"Verification status" section.
- That section mentions all four layers: L1, L2, L3, L4.
- L4 explicitly states that adopter verification is not assertable
  from this repo (honesty anchor).
- The anti-pattern badge shield uses ``anti--patterns-32%20encoded``
  (R5-L1 + R5-H1 are co-shipped; the badge is the most prominent
  count claim on the page).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_exists() -> None:
    assert README.exists(), f"README not found at {README}"


#: Every document that carries the README's capability claims. The detail
#: moved to docs/CAPABILITIES.md when the README adopted the shared standard
#: (#265), and this guard went on reading README.md alone — an unqualified
#: `Production-ready` cell planted there passed (QA-4 round seventeen, of
#: ml-platform, reading this repository).
CLAIM_SURFACES = (README, REPO_ROOT / "docs" / "CAPABILITIES.md")


@pytest.mark.parametrize("surface", CLAIM_SURFACES, ids=lambda path: path.name)
def test_every_claim_surface_qualifies_production_ready(surface: Path) -> None:
    """The R5-H1 wording rule, on every surface that now carries the claims."""
    text = surface.read_text(encoding="utf-8")
    bad = [
        f"| Production-ready{m.group('tail')} ..."
        for m in re.finditer(r"\|\s*Production-ready(?!-)(?P<tail>[^|]{0,60})", text)
        if not m.group("tail").lstrip().startswith("by design")
    ]
    assert not bad, f"{surface.name} has unqualified `Production-ready` status cells:\n  - " + "\n  - ".join(bad)


def test_status_column_uses_production_ready_by_design(readme_text: str) -> None:
    """Every status cell that says 'Production-ready' must be qualified
    with ``by design``. The qualifier is the whole point of R5-H1.

    Detection: match table-cell patterns of the form
    ``| <Area> | Production-ready ... |`` (NOT ``| ... | **Phase 1 — ...`` rows,
    and NOT prose).
    """
    # Capture: pipe · optional space · "Production-ready" · next 40 chars
    # to make the failure message show exactly what was emitted.
    bad_matches: list[str] = []
    for m in re.finditer(r"\|\s*Production-ready(?!-)(?P<tail>[^|]{0,60})", readme_text):
        tail = m.group("tail")
        if not tail.lstrip().startswith("by design"):
            bad_matches.append(f"| Production-ready{tail} ...")
    assert not bad_matches, (
        "README has unqualified `Production-ready` status cells — "
        "R5-H1 requires `Production-ready by design`:\n  - " + "\n  - ".join(bad_matches)
    )


def test_verification_status_section_present(readme_text: str) -> None:
    """The honesty-anchor section introduced in R5-H1 must exist."""
    assert re.search(r"^###\s+Verification status\b", readme_text, flags=re.MULTILINE), (
        "README must carry a §'Verification status' subsection introduced "
        "by R5-H1; without it the softened status column has no honesty anchor."
    )


@pytest.mark.parametrize("layer_token", ["L1", "L2", "L3", "L4"])
def test_verification_matrix_covers_all_four_layers(readme_text: str, layer_token: str) -> None:
    """All four verification layers MUST be named in README."""
    # Look for the bold-L pattern "**L1 — ..." inside the verification section.
    section = _extract_section(readme_text, "Verification status")
    assert re.search(rf"\*\*{layer_token}\s*[—-]", section), (
        f"Verification status section must describe layer {layer_token}; current section does not name it."
    )


def test_l4_is_explicitly_not_assertable(readme_text: str) -> None:
    """L4 must carry an explicit disclaimer that it is not assertable
    from this repo — this is the core honesty invariant of R5-H1.
    """
    section = _extract_section(readme_text, "Verification status")
    # Be forgiving on exact wording; require the semantic anchor.
    ok = re.search(r"\*\*L4\b[^|]*\|[^|]*\|[^|]*\|.*?Not assertable", section, flags=re.IGNORECASE)
    assert ok, (
        "L4 row must contain the phrase 'Not assertable' (or similar) to "
        "make clear that per-adopter production rollout is outside the "
        "repo's verification surface."
    )


def test_antipattern_badge_count_matches_canon(readme_text: str) -> None:
    """Badge shield must cite the canonical anti-pattern count (R5-L1 canon)."""
    # Accept either the standard shield URL-encoded form or spaced form.
    pat = re.compile(r"anti--patterns-(\d{2})(?:%20|\s)encoded", re.IGNORECASE)
    match = pat.search(readme_text)
    assert match, "README must expose an anti-pattern count badge"
    # Dynamically derive the canonical max from AGENTS.md to avoid
    # hardcoding the count (which drifts when new D-NN are added).
    agents_md = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    d_ids = re.findall(r"\bD-(\d{2})\b", agents_md)
    canonical_max = str(max(int(x) for x in d_ids)) if d_ids else "32"
    assert match.group(1) == canonical_max, (
        f"Badge says {match.group(1)} anti-patterns but canon is {canonical_max} "
        "(AGENTS.md §Anti-Patterns last row). Bump the shield."
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _extract_section(text: str, heading_plain: str) -> str:
    """Return content of a markdown section by heading (stops at next heading of
    same or higher level). Used to scope assertions to a specific section and
    avoid false positives elsewhere in the README.
    """
    # Match either `##` or `###` for the target heading
    start = re.search(rf"^#{{2,3}}\s+{re.escape(heading_plain)}\b", text, flags=re.MULTILINE)
    assert start, f"Section '{heading_plain}' not found"
    offset = start.end()
    # Next same-or-higher-level heading closes the section
    level = start.group(0).count("#", 0, 3)  # 2 or 3
    closer = re.search(rf"^#{{2,{level}}}\s", text[offset:], flags=re.MULTILINE)
    end = offset + closer.start() if closer else len(text)
    return text[offset:end]
