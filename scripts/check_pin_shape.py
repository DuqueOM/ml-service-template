#!/usr/bin/env python3
"""Verify every declared Python dependency uses compatible-release pinning.

Why this exists
---------------
D-05 requires ``~=`` for every ML dependency, because a *range* admits the
next major silently: numpy 2.x corrupts joblib-serialised models with no error
raised, which is the failure that wrote the rule.

The rule was policy, prose and a Dependabot setting — and never an assertion.
On 2026-09-21 Dependabot opened a branch named

    dependabot/pip/templates/service/pytest-gte-8.3-and-lt-9.2

which is the shape it proposes when it **widens** a constraint:
``pytest>=8.3,<9.2`` in place of ``pytest ~= 9.1.1``. That is pip's default
behaviour for a project it reads as a library, and `versioning-strategy:
increase` does not always prevent it. Nothing in the repository would have
noticed the shape change: the coherence gate compares the two lanes' pins to
each other, the partition gate looks at *which* distributions appear, and both
would have passed on a widened range present in both files.

A widened pin is worse than a wrong one. It reports as "pinned", it satisfies
every other gate, and it hands the next major to whoever runs `pip install`
first.

What this checks
----------------
Every requirement line and every ``pyproject.toml`` dependency entry in the
payload, the EDA lanes and the worked example must carry a ``~=`` specifier.
Rejected: ``>=``, ``<=``, ``<``, ``>``, ``!=`` alone, ``==`` (that belongs in a
lockfile, not a declaration — D-05), and a bare, unpinned name.

Deliberately NOT checked
------------------------
* ``requirements-dev.txt`` style hash-pinned lockfiles are not generated here,
  so there is no lockfile exemption to encode. If one appears, give it an
  explicit entry rather than loosening the rule.
* The *values*. Whether ``~= 1.26.0`` is the right numpy floor is D-05's
  business and the boundary lives in `.github/dependabot.yml`; this gate only
  asserts the shape that keeps the boundary meaningful.

Exit codes
----------
- 0: every declared dependency uses ``~=``.
- 1: at least one range, equality pin or unpinned name was found.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIREMENT_GLOBS = (
    "templates/service/requirements*.txt",
    "templates/service/eda/requirements*.txt",
    "examples/minimal/requirements.txt",
)
PYPROJECTS = ("templates/service/pyproject.toml",)

# `-r other.txt`, comments, blank lines, and pip flags are not declarations.
_SKIP = re.compile(r"^\s*(#|-r\s|--)")
_NAME = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(\[[^\]]+\])?\s*(?P<spec>.*)$")
_COMPATIBLE = re.compile(r"^~=\s*[0-9]")


def _requirement_files() -> list[Path]:
    found: list[Path] = []
    for pattern in REQUIREMENT_GLOBS:
        found.extend(sorted(REPO_ROOT.glob(pattern)))
    return found


def _declarations() -> list[tuple[str, str, str]]:
    """(file, dependency, specifier) for every declaration we police."""
    out: list[tuple[str, str, str]] = []
    for path in _requirement_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or _SKIP.match(raw):
                continue
            match = _NAME.match(line)
            if not match:
                continue
            out.append((rel, match.group(1), match.group("spec").strip()))
    for name in PYPROJECTS:
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        project = data.get("project", {})
        groups = [("dependencies", project.get("dependencies", []))]
        groups += list((project.get("optional-dependencies") or {}).items())
        for group, entries in groups:
            for entry in entries:
                match = _NAME.match(entry)
                if match:
                    out.append((f"{name} [{group}]", match.group(1), match.group("spec").strip()))
    return out


def main() -> int:
    declarations = _declarations()
    if not declarations:
        print("FAIL: no dependency declarations found.")
        print("  Either the requirement files moved or the patterns stopped matching —")
        print("  and a gate that checks nothing reports success, which is the defect")
        print("  this one exists to prevent.")
        return 1

    offenders = [(f, dep, spec) for f, dep, spec in declarations if not _COMPATIBLE.match(spec)]
    if offenders:
        print("FAIL: a dependency is declared without compatible-release pinning (D-05).")
        print()
        for file, dep, spec in offenders:
            shown = spec or "(no specifier)"
            print(f"  - {file}: {dep} {shown}")
        print()
        print("  `~=` is the only accepted shape here. A range reports as pinned, passes")
        print("  every other dependency gate, and still admits the next major: numpy 2.x")
        print("  silently corrupts joblib-serialised models, which is the failure D-05")
        print("  was written for. `==` belongs in a lockfile, not in a declaration.")
        print()
        print("  Dependabot proposes the widened form by default for projects it reads")
        print("  as libraries — `dependabot/pip/.../pytest-gte-8.3-and-lt-9.2` is what")
        print("  that looks like. Keep `versioning-strategy: increase` and this gate.")
        return 1

    files = len({f for f, _, _ in declarations})
    print(f"[pin-shape] OK — {len(declarations)} pin(s) across {files} file(s) use compatible-release (~=).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
