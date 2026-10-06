"""Contract test — the README follows the shared README standard, and its status is generated.

`scripts/check_readme.py` reads `README.md` against
`docs/governance/readme-standard.md`, the same file in ml-platform. These tests
make it fail in every way the standard names, hold the checker and the standard
to the same sections, and check that the status block is derived from the
sources rule 16 names — `VERSION`, the dated CHANGELOG heading, the adoption
matrix, the anti-pattern catalogue, the verification lanes and the validation
log — rather than typed.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_readme.py"


def _load_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("check_readme", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_readme"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()
README = gate.README.read_text(encoding="utf-8")
STANDARD = gate.STANDARD.read_text(encoding="utf-8")
GENERATED = gate.status()


def _flat(text: str) -> str:
    """Whitespace-insensitive, as markdown reads it: the generator wraps prose at 120 columns."""
    return " ".join(text.split())


def _failures(readme: str) -> list[str]:
    return gate.check(readme, STANDARD, GENERATED)


def test_the_readme_conforms() -> None:
    assert _failures(README) == []


def test_the_checker_and_the_standard_name_the_same_sections() -> None:
    in_standard = tuple(re.findall(r"^\| \d+ \| `## ([^`]+)` \|", STANDARD, re.M))
    assert in_standard == gate.SECTIONS


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda r: r.replace("\n## Quick start\n", "\n## Getting started\n"), "missing ['Quick start']", id="renamed"
        ),
        pytest.param(
            lambda r: r.replace("\n## License\n", "\n## Roadmap\n\nLater.\n\n## License\n"),
            "not in the standard ['Roadmap']",
            id="extra-section",
        ),
        pytest.param(
            lambda r: (
                r.replace("\n## What you get\n", "\n## TMP\n")
                .replace("\n## Architecture\n", "\n## What you get\n")
                .replace("\n## TMP\n", "\n## Architecture\n")
            ),
            "out of order",
            id="reordered",
        ),
        pytest.param(
            lambda r: re.sub(r"\*\*v\d+\.\d+\.\d+\*\*", "**v9.9.9**", r, count=1),
            "status block is stale",
            id="hand-edited-status",
        ),
        pytest.param(
            lambda r: r.replace(gate.STATUS_BEGIN, ""), "no generated status block", id="status-markers-removed"
        ),
        pytest.param(
            lambda r: r.replace("a one-way export of it", "retired"),
            "related-repositories table differs",
            id="related-edited",
        ),
        pytest.param(
            lambda r: r.replace(
                "\n\n## Status",
                "\n[![coverage](https://img.shields.io/badge/coverage-99%25-green.svg)](x)\n\n## Status",
                1,
            ),
            "states a claim",
            id="claim-badge",
        ),
        pytest.param(
            lambda r: r.replace("cd ../ChurnPredictor && pytest", "cd ../ChurnPredictor\npytest", 1),
            "the quick start runs 6 commands",
            id="long-quick-start",
        ),
        pytest.param(lambda r: r + "\nfiller\n" * 200, "lines; the standard's budget is 250", id="over-line-budget"),
        pytest.param(
            lambda r: r.replace("## License\n", "## License\n\n" + "word " * 1500 + "\n"),
            "words; the standard's budget is 2000",
            id="over-word-budget",
        ),
    ],
)
def test_each_departure_fails(mutate, expected: str) -> None:  # type: ignore[no-untyped-def]
    mutated = mutate(README)
    assert mutated != README, "the mutation did not apply; this case tests nothing"

    failures = _failures(mutated)

    assert any(expected in failure for failure in failures), failures


def test_the_anti_pattern_badge_is_allowed_only_because_a_gate_holds_its_number() -> None:
    """Gated badges are named with the test that gates them; that test must exist."""
    for _, holder in gate.GATED_BADGES:
        path, _, name = holder.partition("::")
        assert name in (REPO_ROOT / path).read_text(encoding="utf-8"), f"{holder} no longer exists"


# --- the status block is derived, not typed ---------------------------------


def test_the_status_states_the_released_version() -> None:
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert f"**v{version}**" in GENERATED


def test_the_status_counts_the_adoption_matrix_by_its_production_column() -> None:
    adoption = (REPO_ROOT / "docs" / "ADOPTION.md").read_text(encoding="utf-8")
    prod = re.findall(
        r"^\| [^|]+ \| (?:ready|partial|roadmap) \| (?:ready|partial|roadmap) \| (ready|partial|roadmap) \|",
        adoption,
        re.M,
    )
    flat = _flat(GENERATED)
    assert f"{prod.count('ready')} ready · {prod.count('partial')} partial · {prod.count('roadmap')} roadmap" in flat
    assert f"of {len(prod)}," in flat


def test_the_status_count_of_anti_patterns_is_the_catalogue_maximum() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    canonical = max(int(n) for n in re.findall(r"\bD-(\d{2})\b", agents))
    assert f"**{canonical} anti-patterns**" in _flat(GENERATED)


def test_l4_is_never_claimed() -> None:
    assert "L4, your cloud and your traffic, is not assertable from this repository" in _flat(GENERATED)


def test_a_removed_lane_drops_out_of_the_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "validate-templates.yml").write_text("name: x\n", encoding="utf-8")
    monkeypatch.setattr(gate, "WORKFLOWS", workflows)

    status = _flat(gate.status())

    assert "L1 contract tests" in status
    assert "golden path" not in status, "a lane that no longer exists is still reported as verifying"


def test_a_version_without_a_dated_release_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    version = tmp_path / "VERSION"
    version.write_text("9.9.9\n", encoding="utf-8")
    monkeypatch.setattr(gate, "VERSION", version)

    with pytest.raises(ValueError, match="has no dated v9.9.9 heading"):
        gate.status()


def test_the_latest_validation_entry_is_cited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "VALIDATION_LOG.md"
    log.write_text("## Entry 001 — first\n\n## Entry 002 — second\n\n## Entry NNN — <short title>\n", encoding="utf-8")
    monkeypatch.setattr(gate, "VALIDATION_LOG", log)

    assert "Entry 002 — second" in _flat(gate.status())


# --- regeneration -----------------------------------------------------------


def test_write_regenerates_both_blocks_and_is_idempotent() -> None:
    emptied = gate._replace(
        gate._replace(README, gate.STATUS_BEGIN, gate.STATUS_END, "stale"),
        gate.RELATED_BEGIN,
        gate.RELATED_END,
        "stale",
    )
    assert _failures(emptied)

    rewritten = gate.write(emptied, GENERATED, STANDARD)

    assert rewritten == README
    assert gate.write(rewritten, GENERATED, STANDARD) == rewritten


def test_the_cli_fails_a_stale_readme_and_write_repairs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    readme = tmp_path / "README.md"
    readme.write_text(gate._replace(README, gate.STATUS_BEGIN, gate.STATUS_END, "typed by hand"), encoding="utf-8")
    monkeypatch.setattr(gate, "README", readme)

    assert gate.main([]) == 1
    assert "status block is stale" in capsys.readouterr().out

    assert gate.main(["--write"]) == 0
    assert readme.read_text(encoding="utf-8") == README
