"""Contract test for R4 audit finding C1 — cadence-anticipated model names.

R4 found a model-routing table that presented anticipated model names
(``gpt-5.x``, ``gemini-3.x``) as if a vendor had verified them. The fix was a
disclaimer and a verification procedure; this test keeps both attached to the
names, wherever the names are.

WHERE THE NAMES LIVE
--------------------
Until the README restructure the routing table was a README section, and this
test read ``README.md`` § "Model routing policy" and nothing else. The table
now lives in ``docs/agentic/model-routing.md``. Re-pointing the test at the new
file would have repeated the defect it was written against in a new shape: a
control that guards one location, while the thing it guards can be pasted
anywhere. So it no longer names a file. It sweeps every live document —
the same set ``scripts/check_doc_coherence.py`` reconciles, imported rather
than re-derived — and every document that carries a speculative name must also
carry the disclaimer and the verification procedure.

Invariants, per document that carries a speculative name:

1. **No "verified" claim** — the misleading ``(verified 2026-04)`` suffix is
   absent, and the document declares the names ``cadence-anticipated``.
2. **Disclaimer present** — both canonical disclaimer phrases appear.
3. **Verification procedure present** — the "Verifying model availability
   before adoption" subsection links all three provider catalogues.

Plus two that stop the sweep from passing on nothing:

4. At least one live document carries the routing table. If the table is
   deleted or moved outside the swept set, the test fails instead of reporting
   a vacuous pass.
5. ``README.md`` links to the routing document, so an adopter can find it.

Authority: R4 audit C1, ADR-020, ACTION_PLAN_R4 §S0-1.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
ROUTING_DOC = REPO_ROOT / "docs" / "agentic" / "model-routing.md"

# Patterns that count as "speculative / cadence-anticipated".
SPECULATIVE_PATTERNS = [
    re.compile(r"gpt-5\.\d"),
    re.compile(r"gemini-3\.\d"),
]

# Phrases that MUST appear in any document carrying a speculative name.
REQUIRED_DISCLAIMER_PHRASES = [
    "cadence-anticipated",
    "Verifying model availability before adoption",
]

REQUIRED_DASHBOARDS = [
    "platform.openai.com/docs/models",
    "docs.anthropic.com",
    "ai.google.dev",
]


def _load_coherence_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_doc_coherence", REPO_ROOT / "scripts" / "check_doc_coherence.py"
    )
    assert spec and spec.loader, "scripts/check_doc_coherence.py could not be loaded"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _documents_with_speculative_names() -> list[Path]:
    gate = _load_coherence_gate()
    return [
        doc
        for doc in gate.live_documents(REPO_ROOT)
        if any(p.search(doc.read_text(encoding="utf-8")) for p in SPECULATIVE_PATTERNS)
    ]


CARRIERS = _documents_with_speculative_names()


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_the_routing_table_is_somewhere_the_sweep_can_see() -> None:
    """Invariant 4: the sweep must find the table, or it proves nothing."""
    assert CARRIERS, (
        "No live document carries a speculative model name, so every other invariant here "
        "would pass on nothing. If the routing table was deliberately rewritten to verified "
        "names, delete this test with a note in ADR-020; if it moved, move it back into docs/."
    )
    assert ROUTING_DOC in CARRIERS, (
        f"The routing table is expected in {_rel(ROUTING_DOC)}; found in {[_rel(p) for p in CARRIERS]}"
    )


@pytest.mark.parametrize("doc", CARRIERS, ids=_rel)
def test_no_verified_claim(doc: Path) -> None:
    """Invariant 1: anticipated names are never labelled as verified."""
    text = doc.read_text(encoding="utf-8")
    assert "(verified 2026-04)" not in text, (
        f"{_rel(doc)} labels the model baseline 'verified' — R4 finding C1 requires "
        "'cadence-anticipated, NOT vendor-verified'. See docs/audit/ACTION_PLAN_R4.md §S0-1."
    )
    assert "cadence-anticipated" in text.lower(), f"{_rel(doc)} must declare the names cadence-anticipated."


@pytest.mark.parametrize("doc", CARRIERS, ids=_rel)
def test_speculative_names_carry_disclaimer(doc: Path) -> None:
    """Invariant 2: a speculative name never travels without its disclaimer."""
    text = doc.read_text(encoding="utf-8")
    for phrase in REQUIRED_DISCLAIMER_PHRASES:
        assert phrase in text, (
            f"{_rel(doc)} carries speculative model names but not the disclaimer phrase {phrase!r}. "
            "See docs/audit/ACTION_PLAN_R4.md §S0-1."
        )


@pytest.mark.parametrize("doc", CARRIERS, ids=_rel)
def test_verification_procedure_lists_three_providers(doc: Path) -> None:
    """Invariant 3: the adopter is told where to verify each name."""
    text = doc.read_text(encoding="utf-8")
    for dashboard in REQUIRED_DASHBOARDS:
        assert dashboard in text, (
            f"{_rel(doc)} must link {dashboard!r} in its verification procedure. "
            "See docs/audit/ACTION_PLAN_R4.md §S0-1."
        )


def test_readme_links_the_routing_document() -> None:
    """Invariant 5: the README is where an adopter starts; it must point here."""
    readme = README.read_text(encoding="utf-8")
    assert "docs/agentic/model-routing.md" in readme, "README.md must link docs/agentic/model-routing.md"
