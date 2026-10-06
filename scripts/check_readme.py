#!/usr/bin/env python3
"""The README follows the shared README standard, and its status is generated.

`docs/governance/readme-standard.md` — the same file in `ml-platform` and here —
fixes the README's sections, their order, a length budget, which badges may
appear, and a related-repositories table both READMEs carry. This reads the
README against it.

It exists because the README had grown to 5,700 words that repeated the
documents it linked to, with the facts an adopter needs — what version, how
mature each capability is, what has actually been verified — spread across
eight sections and typed by hand.

The status block is not typed. It is generated from `VERSION` and its dated
`CHANGELOG.md` heading, the maturity matrix in `docs/ADOPTION.md`, the
anti-pattern catalogue in `AGENTS.md`, the verification lanes that exist in
`.github/workflows/`, and the latest entry in `VALIDATION_LOG.md`. Each of those
is the source of truth rule 16 names, so the README can only restate them.

    python3 scripts/check_readme.py            # check (CI)
    python3 scripts/check_readme.py --write    # regenerate the generated blocks
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
STANDARD = REPO_ROOT / "docs" / "governance" / "readme-standard.md"
VERSION = REPO_ROOT / "VERSION"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
ADOPTION = REPO_ROOT / "docs" / "ADOPTION.md"
AGENTS = REPO_ROOT / "AGENTS.md"
VALIDATION_LOG = REPO_ROOT / "VALIDATION_LOG.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: The lanes that produce each evidence layer here, in order. A lane that stops
#: existing drops out of the status, and the README says so on the next run.
LANES = (
    ("L1", "contract tests", "validate-templates.yml"),
    ("L2", "scaffold smoke", "pr-smoke-lane.yml"),
    ("L3", "golden path on kind", "golden-path.yml"),
)

SECTIONS = (
    "Status",
    "What it is",
    "Quick start",
    "What you get",
    "Architecture",
    "How claims are verified",
    "Documentation",
    "Related repositories",
    "Contributing, security and support",
    "License",
)
MAX_LINES = 250
MAX_WORDS = 2000
MAX_BADGES = 6
MAX_QUICK_START_COMMANDS = 5
MAX_DESCRIPTION = 120

STATUS_BEGIN = "<!-- BEGIN README STATUS -->"
STATUS_END = "<!-- END README STATUS -->"
RELATED_BEGIN = "<!-- BEGIN RELATED REPOSITORIES -->"
RELATED_END = "<!-- END RELATED REPOSITORIES -->"

#: Badges that report a check rather than state a claim: a workflow's status,
#: the latest release, the licence, the supported Pythons. Matched on the image
#: URL, which is what decides what the badge displays.
ALLOWED_BADGES = (
    re.compile(r"/actions/workflows/[^/\s)]+/badge\.svg"),
    re.compile(r"img\.shields\.io/github/(?:actions/workflow/status|v/release|license)/"),
    re.compile(r"img\.shields\.io/badge/(?:[Ll]icen[cs]e|[Pp]ython)\b"),
)
#: Badges that show a NUMBER, allowed only because a gate in this repository
#: already compares that number with its source — each paired with that gate.
GATED_BADGES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"img\.shields\.io/badge/anti--patterns-\d+%20encoded"),
        "templates/tests/governance/test_readme_verification_status.py::test_antipattern_badge_count_matches_canon",
    ),
)
_BADGE = re.compile(r"\[!\[[^\]]*\]\(([^)\s]+)\)\]\([^)]*\)")
_FENCE = re.compile(r"^(```|~~~)")


def _between(text: str, begin: str, end: str) -> str | None:
    start = text.find(begin)
    stop = text.find(end, start + len(begin)) if start != -1 else -1
    if start == -1 or stop == -1:
        return None
    return text[start + len(begin) : stop].strip("\n")


def _outside_fences(text: str) -> list[str]:
    """Lines not inside a fenced code block, so a `## ` in an example is not a heading."""
    lines, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


# --- the status block: generated, never typed ------------------------------

_RELEASED = re.compile(r"^## \[v(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})", re.M)
_MATRIX_ROW = re.compile(
    r"^\| [^|]+ \| (?:ready|partial|roadmap) \| (?:ready|partial|roadmap) \| (ready|partial|roadmap) \|", re.M
)
_ANTI_PATTERN = re.compile(r"\bD-(\d{2})\b")
_ENTRY = re.compile(r"^## Entry (\d{3}) — (.+)$", re.M)


def _wrap(paragraph: str) -> str:
    """Prose wrapped at the markdown line limit both repositories lint to; links are never split mid-token."""
    return textwrap.fill(paragraph, width=120, break_long_words=False, break_on_hyphens=False)


def _shown(path: Path) -> str:
    """A path as a reader finds it: relative to the repository when it is inside it."""
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def status() -> str:
    """The README's status block, from the sources rule 16 names as canonical."""
    version = VERSION.read_text(encoding="utf-8").strip()
    released = {v: d for v, d in _RELEASED.findall(CHANGELOG.read_text(encoding="utf-8"))}
    if version not in released:
        raise ValueError(f"{_shown(VERSION)} says {version}, and {_shown(CHANGELOG)} has no dated v{version} heading")

    ratings = _MATRIX_ROW.findall(ADOPTION.read_text(encoding="utf-8"))
    if not ratings:
        raise ValueError(f"{_shown(ADOPTION)} no longer carries its maturity matrix")
    ready, partial, roadmap = (ratings.count(r) for r in ("ready", "partial", "roadmap"))

    anti_patterns = max((int(n) for n in _ANTI_PATTERN.findall(AGENTS.read_text(encoding="utf-8"))), default=0)
    if not anti_patterns:
        raise ValueError(f"{_shown(AGENTS)} defines no D-NN anti-pattern")

    lanes = [f"{layer} {what} (`{name}`)" for layer, what, name in LANES if (WORKFLOWS / name).is_file()]
    entries = [(number, title) for number, title in _ENTRY.findall(VALIDATION_LOG.read_text(encoding="utf-8"))]
    if not entries:
        raise ValueError(f"{_shown(VALIDATION_LOG)} has no numbered entry")
    number, title = entries[-1]

    return "\n".join(
        [
            "<!-- Generated by `python3 scripts/check_readme.py --write` from VERSION, CHANGELOG.md,",
            "     docs/ADOPTION.md, AGENTS.md, .github/workflows/ and VALIDATION_LOG.md.",
            "     Edit those, then regenerate; never edit this block. -->",
            "",
            _wrap(f"**v{version}**, released {released[version]} — [CHANGELOG](CHANGELOG.md), [releases](releases/)."),
            "",
            _wrap(
                f"**Production maturity, per capability: {ready} ready · {partial} partial · {roadmap} roadmap** of "
                f"{len(ratings)}, rated per cloud and environment in the [adoption matrix](docs/ADOPTION.md). "
                f"**{anti_patterns} anti-patterns** encoded and contract-tested ([AGENTS.md](AGENTS.md))."
            ),
            "",
            _wrap(
                "Verified in this repository: " + " · ".join(lanes) + ". **L4, your cloud and your traffic, is not "
                "assertable from this repository** — it is the adopter's to run and record."
            ),
            "",
            _wrap(
                f"Latest execution record: [VALIDATION_LOG.md](VALIDATION_LOG.md), Entry {number} — {title.strip()}."
            ),
        ]
    )


# --- the checks -------------------------------------------------------------


def check(readme: str, standard: str, generated: str) -> list[str]:
    """One message per departure from the standard. Empty means the README conforms."""
    failures: list[str] = []
    lines = readme.splitlines()
    visible = _outside_fences(readme)

    if not lines or not lines[0].startswith("# "):
        failures.append("the README does not open with its `# <repository name>` title")
    description = next((line for line in lines[1:] if line.strip()), "")
    if not description.startswith("> "):
        failures.append("the title is not followed by a one-line `> ` description")
    elif len(description) - 2 > MAX_DESCRIPTION:
        failures.append(f"the description is {len(description) - 2} characters; the standard allows {MAX_DESCRIPTION}")

    found = tuple(line[3:].strip() for line in visible if line.startswith("## "))
    if found != SECTIONS:
        missing = [name for name in SECTIONS if name not in found]
        extra = [name for name in found if name not in SECTIONS]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"not in the standard {extra}")
        if not missing and not extra:
            detail.append(f"out of order: {list(found)}")
        failures.append("level-2 sections differ from the standard: " + "; ".join(detail))

    block = _between(readme, STATUS_BEGIN, STATUS_END)
    if block is None:
        failures.append(f"no generated status block between `{STATUS_BEGIN}` and `{STATUS_END}`")
    elif block.strip() != generated.strip():
        failures.append("the status block is stale; run `python3 scripts/check_readme.py --write`")

    canonical = _between(standard, RELATED_BEGIN, RELATED_END)
    carried = _between(readme, RELATED_BEGIN, RELATED_END)
    if canonical is None:
        failures.append("the standard no longer carries its related-repositories table")
    elif carried is None or carried.strip() != canonical.strip():
        failures.append("the related-repositories table differs from the one in docs/governance/readme-standard.md")

    first_section = next((n for n, line in enumerate(lines) if line.startswith("## ")), len(lines))
    badges = [url for line in lines[:first_section] for url in _BADGE.findall(line)]
    if len(badges) > MAX_BADGES:
        failures.append(f"{len(badges)} badges; the standard allows {MAX_BADGES}")
    for url in badges:
        if not any(pattern.search(url) for pattern in (*ALLOWED_BADGES, *(gated for gated, _ in GATED_BADGES))):
            failures.append(f"badge {url} states a claim rather than reporting a check")

    quick = _section(readme, "Quick start")
    commands = [line for line in _fenced(quick) if line.strip() and not line.strip().startswith("#")]
    if len(commands) > MAX_QUICK_START_COMMANDS:
        failures.append(
            f"the quick start runs {len(commands)} commands; the standard allows {MAX_QUICK_START_COMMANDS}"
        )

    if len(lines) > MAX_LINES:
        failures.append(f"{len(lines)} lines; the standard's budget is {MAX_LINES}")
    words = len(readme.split())
    if words > MAX_WORDS:
        failures.append(f"{words} words; the standard's budget is {MAX_WORDS}")
    return failures


def _section(readme: str, name: str) -> str:
    match = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
    return match.group(1) if match else ""


def _fenced(text: str) -> list[str]:
    inside, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if fenced:
            inside.append(line)
    return inside


def _replace(text: str, begin: str, end: str, body: str) -> str:
    start, stop = text.find(begin), text.find(end)
    if start == -1 or stop == -1 or stop < start:
        raise ValueError(f"the README has no `{begin}` … `{end}` block to regenerate")
    return text[: start + len(begin)] + "\n" + body + "\n" + text[stop:]


def write(readme: str, generated: str, standard: str) -> str:
    """The README with its status block and its related-repositories table regenerated."""
    related = _between(standard, RELATED_BEGIN, RELATED_END)
    if related is None:
        raise ValueError("the standard no longer carries its related-repositories table")
    return _replace(_replace(readme, STATUS_BEGIN, STATUS_END, generated), RELATED_BEGIN, RELATED_END, related)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="regenerate the status block and the related table")
    args = parser.parse_args(argv)

    generated = status()
    readme = README.read_text(encoding="utf-8")
    standard = STANDARD.read_text(encoding="utf-8")
    if args.write:
        updated = write(readme, generated, standard)
        if updated != readme:
            README.write_text(updated, encoding="utf-8")
            print("[readme] status block regenerated")
        readme = updated

    failures = check(readme, standard, generated)
    if failures:
        for failure in failures:
            print(f"  FAIL [readme] {failure}")
        print("\n[readme] FAILED — README.md departs from docs/governance/readme-standard.md")
        return 1
    print(f"[readme] OK — {len(SECTIONS)} sections in order, status generated, within budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
