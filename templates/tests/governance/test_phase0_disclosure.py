"""Contract test — README must disclose that ADR-018/019 are not live runtime.

Origin: R4 audit finding C2.

WHY THIS WAS REWRITTEN
----------------------
The original version guarded the *Phase 0* wording and every check began::

    if not _is_phase_0(ADR):
        pytest.skip("ADR is no longer Phase 0 — banner no longer mandatory.")

Both ADRs advanced to Phase 1, so all six invariants began skipping and the
suite reported ``6 skipped`` — a green run that enforced nothing. The
disclosure it was written to protect became deletable without any test
failing, which is the exact regression the test existed to prevent.

A guard that disarms itself the moment the thing it guards changes state is
worse than no guard: it reports success while doing nothing, so nobody goes
looking. This version is **phase-aware instead of phase-pinned**:

* it re-derives what to enforce from the ADR's own declared phase, and
* it FAILS rather than skips when it cannot determine what to enforce.

The disclosure requirement lifts only when an ADR explicitly declares its
runtime complete (see ``RUNTIME_LIVE_MARKERS``). Advancing 1 → 2 → 3 keeps
the guard armed automatically; no test edit is required, and forgetting to
edit it can no longer silently disarm it.

Authority: R4 audit C2, ADR-020, ACTION_PLAN_R4 §S0-2.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
ADR_018 = REPO_ROOT / "docs" / "decisions" / "ADR-018-operational-memory-plane.md"
ADR_019 = REPO_ROOT / "docs" / "decisions" / "ADR-019-agentic-ci-self-healing.md"

# The full target design of each capability lives in its own document; the
# README keeps the status banner and a link. A reader who lands on the detail
# page from a search or a link never passes through the README, so the page
# must carry the same disclosure. Both surfaces are held to the ADR's phase.
MEMORY_DOC = REPO_ROOT / "docs" / "agentic" / "memory-plane.md"
SELF_HEALING_DOC = REPO_ROOT / "docs" / "agentic" / "ci-self-healing.md"

MEMORY_HEADING = "## Operational Memory Plane"
SELF_HEALING_HEADING = "## Agentic CI self-healing"

HERO_HEADING = "## What this template is"
MATRIX_HEADING = "## Production-ready scope"

# Phrases in an ADR Status line that mean "the runtime actually ships now".
# Only these lift the disclosure requirement.
RUNTIME_LIVE_MARKERS = (
    "runtime complete",
    "runtime live",
    "fully implemented",
    "generally available",
)

# Substance markers: at least one must appear in the disclosure surface.
# A list rather than one canonical phrase so honest rewording does not break
# the build, while the *claim* stays enforced.
NOT_LIVE_MARKERS = (
    "not implemented",
    "not runtime",
    "not yet implemented",
    "no runtime",
    "shadow-only",
    "shadow mode",
    "read-only",
    "deferred",
    "contracts only",
    "contracts-only",
    "writes nothing",
    "roadmap",
)


def _read(path: Path) -> str:
    assert path.exists(), f"required file not found: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme_text() -> str:
    return _read(README)


def _status_line(adr_path: Path) -> str:
    """Return the ADR's Status line, lowercased.

    Fails loudly when absent: an unparseable ADR must never be read as
    "nothing to enforce".
    """
    for line in _read(adr_path).splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("- **status**"):
            return stripped
    pytest.fail(
        f"{adr_path.name} has no '- **Status**:' line, so the required "
        "disclosure level cannot be derived. Restore the Status line."
    )
    raise AssertionError("unreachable")


def _declared_phase(adr_path: Path) -> int:
    status = _status_line(adr_path)
    match = re.search(r"phase\s+(\d+)", status)
    if not match:
        pytest.fail(
            f"{adr_path.name} Status line does not declare a 'Phase N': {status!r}. "
            "The disclosure guard derives its requirement from that number; "
            "without it the guard cannot stay armed."
        )
    return int(match.group(1))


def _runtime_is_live(adr_path: Path) -> bool:
    status = _status_line(adr_path)
    return any(marker in status for marker in RUNTIME_LIVE_MARKERS)


def _section(text: str, heading: str, max_chars: int = 2500) -> str:
    idx = text.find(heading)
    assert idx != -1, f"README is missing the {heading!r} heading"
    return text[idx : idx + max_chars]


def _assert_discloses(surface: str, where: str, adr_name: str) -> None:
    lowered = surface.lower()
    hits = [marker for marker in NOT_LIVE_MARKERS if marker in lowered]
    assert hits, (
        f"{where} must state that the {adr_name} capability is NOT live runtime. "
        f"None of the accepted markers appear: {list(NOT_LIVE_MARKERS)}. "
        "An adopter reading this surface would conclude the feature ships. "
        "See docs/audit/ACTION_PLAN_R4.md §S0-2."
    )


# ---------------------------------------------------------------------------
# Invariant 0 — the guard itself must be armed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adr", [ADR_018, ADR_019], ids=["ADR-018", "ADR-019"])
def test_adr_declares_a_parseable_phase(adr: Path) -> None:
    """The whole guard hangs off this. Assert it rather than assume it."""
    phase = _declared_phase(adr)
    assert phase >= 0


# ---------------------------------------------------------------------------
# Invariant 1 — section banner discloses non-runtime status.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adr", "heading", "label"),
    [
        (ADR_018, MEMORY_HEADING, "ADR-018"),
        (ADR_019, SELF_HEALING_HEADING, "ADR-019"),
    ],
    ids=["ADR-018", "ADR-019"],
)
def test_section_banner_discloses_non_runtime(readme_text: str, adr: Path, heading: str, label: str) -> None:
    if _runtime_is_live(adr):
        pytest.skip(f"{label} declares its runtime live; disclosure no longer required.")
    _assert_discloses(_section(readme_text, heading), f"README §'{heading}'", label)


# ---------------------------------------------------------------------------
# Invariant 2 — the README banner's phase matches the ADR's phase.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adr", "heading", "label"),
    [
        (ADR_018, MEMORY_HEADING, "ADR-018"),
        (ADR_019, SELF_HEALING_HEADING, "ADR-019"),
    ],
    ids=["ADR-018", "ADR-019"],
)
def test_section_banner_phase_matches_adr(readme_text: str, adr: Path, heading: str, label: str) -> None:
    """Catch the drift where an ADR advances and the README keeps the old number."""
    if _runtime_is_live(adr):
        pytest.skip(f"{label} declares its runtime live; phase banner no longer required.")
    phase = _declared_phase(adr)
    section = _section(readme_text, heading)
    assert re.search(rf"phase\s+{phase}\b", section, re.IGNORECASE), (
        f"README §'{heading}' does not state 'Phase {phase}', but {adr.name} "
        f"declares Phase {phase}. The README and the ADR disagree about how "
        "much of this capability is real."
    )


# ---------------------------------------------------------------------------
# Invariant 3 — hero list flags both capabilities.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adr", "capability", "label"),
    [
        (ADR_018, "Operational Memory Plane", "ADR-018"),
        (ADR_019, "CI self-healing", "ADR-019"),
    ],
    ids=["ADR-018", "ADR-019"],
)
def test_hero_list_flags_capability(readme_text: str, adr: Path, capability: str, label: str) -> None:
    if _runtime_is_live(adr):
        pytest.skip(f"{label} declares its runtime live; hero-list flag no longer required.")
    hero = _section(readme_text, HERO_HEADING)
    assert capability in hero, f"§'{HERO_HEADING}' no longer mentions {capability!r}."
    _assert_discloses(hero, f"README §'{HERO_HEADING}'", label)


# ---------------------------------------------------------------------------
# Invariant 4 — maturity matrix row must not read as production-ready.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adr", "row_key", "label"),
    [
        (ADR_018, "Operational Memory Plane", "ADR-018"),
        (ADR_019, "Agentic CI self-healing", "ADR-019"),
    ],
    ids=["ADR-018", "ADR-019"],
)
def test_maturity_matrix_row_not_production_ready(readme_text: str, adr: Path, row_key: str, label: str) -> None:
    if _runtime_is_live(adr):
        pytest.skip(f"{label} declares its runtime live; matrix caveat no longer required.")
    matrix = _section(readme_text, MATRIX_HEADING, max_chars=4000)
    row = next(
        (line for line in matrix.splitlines() if row_key in line and "|" in line),
        None,
    )
    assert row is not None, f"Maturity matrix row for {row_key!r} is missing."
    _assert_discloses(row, f"maturity-matrix row for {row_key!r}", label)
    assert "production-ready" not in row.lower(), (
        f"Maturity matrix row for {row_key!r} claims production-ready while "
        f"{adr.name} has not declared its runtime live."
    )


# ---------------------------------------------------------------------------
# Invariant 5 — the detail page discloses, and agrees with the ADR's phase.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adr", "doc", "label"),
    [
        (ADR_018, MEMORY_DOC, "ADR-018"),
        (ADR_019, SELF_HEALING_DOC, "ADR-019"),
    ],
    ids=["ADR-018", "ADR-019"],
)
def test_detail_page_discloses_and_matches_phase(adr: Path, doc: Path, label: str) -> None:
    if _runtime_is_live(adr):
        pytest.skip(f"{label} declares its runtime live; detail-page disclosure no longer required.")
    body = _read(doc)
    banner = body[: body.find("\n## ")] if "\n## " in body else body
    _assert_discloses(banner, f"{doc.relative_to(REPO_ROOT)} (above its first section)", label)
    phase = _declared_phase(adr)
    assert re.search(rf"phase\s+{phase}\b", banner, re.IGNORECASE), (
        f"{doc.relative_to(REPO_ROOT)} does not state 'Phase {phase}' above its first section, "
        f"but {adr.name} declares Phase {phase}."
    )
