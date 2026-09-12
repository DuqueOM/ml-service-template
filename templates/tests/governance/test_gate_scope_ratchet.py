"""Contract — a gate may not quietly start checking less than it used to.

Why this exists
---------------
This repository relies on nineteen gates to detect its own drift, and **none of
them was tested**. `grep -rl "import scripts"` over both test trees returned
nothing. Every control that catches a regression could itself regress, silently.

That is not hypothetical. Twice in one week a gate narrowed and reported
success at its smaller size:

* ``check_control_claims.py`` scanned **36** anti-patterns instead of 38, after
  two table rows gained a Jinja ``raw`` wrapper that its row pattern no longer
  matched. It printed "OK — 5 of 36" and nothing asked why the number moved.
* ``check_doc_coherence.py``'s overlay check matched ``N overlays`` but not
  ``N overlay renders``, so two documents kept a stale count the check existed
  to catch.

Both are the same shape as the defects those gates hunt: **a control whose
scope is narrower than the surface it guards**. The difference is that the
gates announce their scope in their success line — so the narrowing is
measurable, if anything measures it.

How this works
--------------
Each gate is run, and the number it reports is compared against a recorded
floor. A floor is a **ratchet at the measured value**, exactly like
``fail_under`` for coverage: it costs nothing today, and it makes any later
shrinkage a failure rather than a smaller number nobody reads.

Growth is welcome and does not fail. Shrinkage fails, and the fix is either to
restore the scope or to lower the floor **deliberately**, in a diff someone
reviews.

The count is extracted with a per-gate pattern rather than "the first integer
in the line": ``check_gitleaks_pin`` reports a pinned version, ``8.30.1``,
whose leading 8 is not a scope at all.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"
MAKEFILE = REPO_ROOT / "Makefile"

# gate -> (regex capturing the scope count, floor, what the number counts)
#
# Floors were measured on 2026-09-09. Raise one when the repository grows;
# lowering one is a decision, not a chore.
SCOPE: dict[str, tuple[str, int, str]] = {
    "check_doc_coherence": (r"all (\d+) cross-document checks", 8, "checks registered"),
    "check_doc_path_refs": (r"OK — (\d+) documents", 590, "documents scanned"),
    "check_cicd_template_drift": (r"OK — (\d+) shared actions", 10, "actions compared"),
    "check_vendored_runtime_drift": (r"OK — (\d+) vendored file", 34, "vendored files compared"),
    "check_common_utils_drift": (r"OK — (\d+) files scanned", 20, "files scanned"),
    "check_dashboard_inventory": (r"OK — (\d+) dashboard", 5, "dashboards registered"),
    # Lowered 27 -> 7 on 2026-09-11, deliberately: the mlflow migration
    # (ADR-047) removed twenty accepted findings because mlflow 3.16 carries
    # none of them. A baseline shrinking because the debt was PAID is the
    # one case where lowering a floor is the correct move — and the ratchet
    # still made it a decision in a reviewed diff rather than a number
    # nobody read. It goes back up only if new findings are accepted.
    "check_baselines_expiry": (r"OK — (\d+) entr", 7, "baseline entries checked"),
    "check_adopter_scaffold_ref": (r"\] (\d+) adopter scaffold command", 4, "commands checked"),
    "check_service_adr_references": (r"\] (\d+) template ADRs referenced", 42, "ADR references"),
    "check_template_render_safety": (r"OK — (\d+) files under", 400, "payload files parsed"),
    "check_payload_test_scope": (r"OK — (\d+) payload tests", 46, "payload tests checked"),
    "check_markdownlint_parity": (r"OK — (\d+) rule settings", 7, "rule settings compared"),
    "check_test_clock_isolation": (r"scanned (\d+) test file", 17, "test files scanned"),
    "check_control_claims": (r"OK — \d+ of (\d+) anti-patterns", 38, "anti-patterns scanned"),
    "check_dependency_pin_coherence": (r"OK — (\d+) requirements file", 6, "requirements files grouped"),
    "check_dependency_partition": (r"OK — (\d+) runtime module", 16, "runtime modules walked"),
    "check_config_is_read": (r"OK — (\d+) declared field", 51, "config fields checked"),
    "check_deploy_contract_documented": (r"OK — (\d+) name\(s\)", 15, "deploy names checked"),
}

# Gates whose success line carries no scope number, with the reason. Their exit
# code is still asserted; only the ratchet does not apply.
NO_SCOPE: dict[str, str] = {
    "check_gitleaks_pin": (
        "reports a pinned version, not a scope — the count of sites is in the "
        "message but the leading number is the version"
    ),
    "validate_agentic": "prints a bare verdict; its own suite covers the surface counts",
}


def _gates() -> list[str]:
    """The gate list, read from the Makefile rather than retyped here."""
    if not MAKEFILE.is_file():
        return []
    block = re.search(r"^GATES\s*:?=\s*\\\n(.*?)\n\n", MAKEFILE.read_text(encoding="utf-8"), re.S | re.M)
    if not block:
        return []
    return re.findall(r"(check_[a-z_]+|validate_[a-z_]+)", block.group(1))


GATES = _gates()


def _run(gate: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{gate}.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def test_gate_list_was_discovered() -> None:
    """An empty parametrize set is reported as a pass; make it a failure."""
    assert GATES, f"no GATES found in {MAKEFILE} — this whole module would check nothing"
    assert len(GATES) >= 19, f"only {len(GATES)} gates discovered; the suite has had 19 since 2026-09-12"


def test_every_gate_is_accounted_for() -> None:
    """A new gate must declare a floor or an explicit reason it has none.

    Without this, adding a gate would silently opt it out of the ratchet — the
    registry would narrow exactly the way the gates themselves have.
    """
    unaccounted = [g for g in GATES if g not in SCOPE and g not in NO_SCOPE]
    assert not unaccounted, (
        f"gate(s) {unaccounted} are in the Makefile's GATES but declare neither a scope "
        f"floor in SCOPE nor a reason in NO_SCOPE. Add the measured count as a floor, or "
        f"state why the gate reports no scope."
    )


@pytest.mark.parametrize("gate", GATES, ids=GATES or ["none"])
def test_gate_passes_on_a_clean_tree(gate: str) -> None:
    code, output = _run(gate)
    assert code == 0, f"{gate} failed on a clean checkout:\n{output[-1500:]}"


@pytest.mark.parametrize("gate", sorted(SCOPE), ids=sorted(SCOPE) or ["none"])
def test_gate_scope_has_not_shrunk(gate: str) -> None:
    pattern, floor, what = SCOPE[gate]
    code, output = _run(gate)
    assert code == 0, f"{gate} failed:\n{output[-800:]}"

    match = re.search(pattern, output)
    assert match, (
        f"{gate} passed but its success line no longer reports a scope matching "
        f"{pattern!r}. A gate that stops saying how much it checked cannot be "
        f"told apart from one that checked nothing.\nOutput:\n{output[-500:]}"
    )
    actual = int(match.group(1))
    assert actual >= floor, (
        f"{gate} now reports {actual} {what}, down from a floor of {floor}. It is "
        f"still passing — at a smaller size. Either its scope regressed (the "
        f"`check_control_claims` case: a row shape changed and the pattern stopped "
        f"matching, 38 -> 36, silently), or the repository genuinely shrank and this "
        f"floor should be lowered deliberately."
    )
