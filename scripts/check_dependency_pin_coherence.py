#!/usr/bin/env python3
"""Verify that requirements files installed into the SAME environment agree on pins.

Why this exists
---------------
A generated service ships more than one requirements file, and
``docs/TUTORIAL.md`` walks the adopter through installing several of them into
one environment. Nothing checked that they could coexist. They could not::

    ERROR: Cannot install scikit-learn~=1.5.0 and scikit-learn~=1.9 because
    these package versions have conflicting dependencies.
    ERROR: ResolutionImpossible

``templates/service/requirements.txt`` pinned ``scikit-learn ~= 1.5.0`` and
``pandera ~= 0.23.0``; ``templates/service/eda/requirements.txt`` pinned
``~= 1.9`` and ``~= 0.33``. pip does not error, because the tutorial installs
them in two separate commands — it silently *upgrades* the first set. So the
adopter trains a model against scikit-learn 1.9 and serves it from an image
built with 1.5.2. That is precisely the joblib version-skew failure the
``~=`` policy (D-05) exists to prevent, arriving through the one door nothing
was watching.

How it got there is the part worth designing against: Dependabot raised the
bump on both lanes. The EDA-lane PRs were merged (#116, #135) and the
service-lane PRs for the same packages were closed (#132, #134). Each decision
was defensible alone; together they opened a gap, and no gate compared the two
files, so the gap was invisible.

What this checks
----------------
1. **Pin coherence.** Within a co-installation group, a distribution declared
   in more than one file must carry a byte-identical specifier. Identical, not
   merely compatible: ``~=1.26`` (>=1.26,<2.0) and ``~=1.26.0``
   (>=1.26.0,<1.27.0) are both "numpy 1.x" to a reader and different resolvers
   to pip, and the looser one silently readmits the version the tighter one
   was written to exclude.

2. **Dependabot watches every requirements file.** ``.github/dependabot.yml``
   resolves its ``directory``/``directories`` literally, and its own comment
   says so: *"Adding a requirements file without adding an entry here puts it
   back in the blind spot."* Nothing enforced that. The pip ecosystem was
   absent entirely once, while the repository carried four requirements files,
   and the consequence was three nominal controls over Python dependencies all
   inert at the same time. A stated risk with no control is the shape this
   whole gate exists to reject, so it is checked here.

3. **Group membership is total.** Every tracked ``*requirements*.txt`` must
   belong to a declared group. A new requirements file cannot join the tree
   without a human deciding what it is installed alongside — otherwise this
   gate would narrow exactly the way the defects it hunts do, and would keep
   reporting OK at its smaller size.

What this deliberately does NOT check
-------------------------------------
Transitive resolvability. Two files can agree on every shared *direct* pin and
still conflict three levels down. Catching that needs a resolver and a network,
which the CI already spends once in
``scripts/resolve_python_dependencies.py``. This gate is the static, offline,
sub-second half: it catches the class that actually occurred, on every commit,
including pre-push.

Exit codes
----------
- 0: every co-installation group is internally coherent.
- 1: a group disagrees on a shared pin, or a requirements file belongs to no
  group.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"

# Files installed into a SHARED environment, and therefore required to agree.
#
# Membership is a claim about how the files are USED, so each group cites the
# instruction that puts them in one environment. Splitting a group is a
# decision: it asserts that two files never meet, and that assertion is what
# stops being true when someone writes a new tutorial step.
CO_INSTALLATION_GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "generated-service": (
        "docs/TUTORIAL.md §3 installs eda/requirements.txt into the environment "
        "created from requirements.txt, then §4 runs `make train` in it; "
        "requirements-train.txt, requirements-dev.txt and eda/requirements-heavy.txt "
        "each open with `-r requirements.txt`",
        (
            "templates/service/requirements.txt",
            "templates/service/requirements-train.txt",
            "templates/service/requirements-dev.txt",
            "templates/service/eda/requirements.txt",
            "templates/service/eda/requirements-heavy.txt",
        ),
    ),
    "minimal-example": (
        "examples/minimal is a standalone runnable demo with its own venv "
        "(README: `cd examples/minimal && pip install -r requirements.txt`); "
        "it never shares an environment with the generated service, which is "
        "why its pins may differ",
        ("examples/minimal/requirements.txt",),
    ),
}

# Packages whose pin exists for a CORRECTNESS reason rather than a convenience
# one. Every Dependabot pip entry that watches a file declaring one of these
# must ignore its major updates, or the boundary gets proposed by a robot that
# cannot read the comment next to it.
#
# numpy earned its place the hard way. #159 bumped it to ~=2.5.3 while carrying
# `# numpy 2.x silently corrupts joblib models` forward unchanged on the same
# line. #161 added the ignore to the service entry and MISSED
# examples/minimal — which trains and serves a joblib model too — so #163
# arrived the next day proposing exactly the same crossing. A fix scoped to one
# of two files that share a reason is a control narrower than its surface: the
# shape this gate exists to reject, reproduced by hand.
CORRECTNESS_PINNED: dict[str, str] = {
    "numpy": "numpy 2.x silently corrupts joblib models (D-05)",
}

# `name spec` on one line, ignoring comments, blank lines, `-r` includes and
# pip flags. Extras and environment markers are kept out of the name so
# `foo[bar] ~= 1.0` and `foo ~= 1.0` are recognised as the same distribution.
_REQ = re.compile(
    r"""^\s*
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)      # distribution name
    \s*(?:\[[^\]]*\])?                        # optional extras
    \s*(?P<spec>[~=<>!][^;#]*?)?              # optional version specifier
    \s*(?:;[^#]*)?                            # optional environment marker
    \s*(?:\#.*)?$                             # optional trailing comment
    """,
    re.VERBOSE,
)


def _normalise(name: str) -> str:
    """PEP 503 normalisation — `scikit_learn` and `scikit-learn` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declarations(path: Path) -> dict[str, str]:
    """Direct pins in one file: normalised name -> specifier as written."""
    found: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "--")):
            continue
        match = _REQ.match(line)
        if not match:
            continue
        spec = (match.group("spec") or "").strip()
        found[_normalise(match.group("name"))] = re.sub(r"\s+", "", spec)
    return found


def _tracked_requirements() -> list[str]:
    """Discovered from git, not listed here — a listed set cannot notice a new file."""
    proc = subprocess.run(
        ["git", "ls-files", "*requirements*.txt"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line for line in proc.stdout.split() if line)


def _pip_entries() -> list[tuple[set[str], set[str]]] | None:
    """Per pip entry: (directories it watches, package names it ignores majors for)."""
    if not DEPENDABOT.is_file():
        return None
    doc = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8")) or {}
    entries: list[tuple[set[str], set[str]]] = []
    for entry in doc.get("updates") or []:
        if not isinstance(entry, dict) or entry.get("package-ecosystem") != "pip":
            continue
        dirs: set[str] = set()
        single = entry.get("directory")
        if isinstance(single, str):
            dirs.add(single.strip("/"))
        for many in entry.get("directories") or []:
            if isinstance(many, str):
                dirs.add(many.strip("/"))
        majors_ignored = {
            str(rule.get("dependency-name", "")).lower()
            for rule in (entry.get("ignore") or [])
            if isinstance(rule, dict) and "version-update:semver-major" in (rule.get("update-types") or [])
        }
        entries.append((dirs, majors_ignored))
    return entries


def _dependabot_pip_directories() -> set[str] | None:
    """Every directory the pip ecosystem watches, or None when the file is absent."""
    entries = _pip_entries()
    if entries is None:
        return None
    return {d for dirs, _ in entries for d in dirs}


def main() -> int:
    tracked = _tracked_requirements()
    declared = {p for _, members in CO_INSTALLATION_GROUPS.values() for p in members}

    failures: list[str] = []

    ungrouped = sorted(set(tracked) - declared)
    if ungrouped:
        failures.append(
            "requirements file(s) belong to no co-installation group:\n"
            + "\n".join(f"    - {p}" for p in ungrouped)
            + "\n  Add each to a group in CO_INSTALLATION_GROUPS, or open a new one.\n"
            "  An ungrouped file is unchecked, and an unchecked file is how the\n"
            "  scikit-learn 1.5/1.9 split happened in the first place."
        )

    missing = sorted(declared - set(tracked))
    if missing:
        failures.append(
            "group member(s) are declared but not tracked by git:\n"
            + "\n".join(f"    - {p}" for p in missing)
            + "\n  Either the file was removed and the group is stale, or it was\n"
            "  never committed."
        )

    watched = _dependabot_pip_directories()
    if watched is None:
        failures.append(
            f"{DEPENDABOT.relative_to(REPO_ROOT)} is missing, so no requirements file is\n"
            "  watched for vulnerable versions. The pip ecosystem was absent once already."
        )
    else:
        unwatched = sorted(p for p in tracked if str(Path(p).parent).strip("/") not in watched)
        if unwatched:
            failures.append(
                "requirements file(s) no Dependabot pip entry watches:\n"
                + "\n".join(f"    - {p}" for p in unwatched)
                + "\n  Add the directory to a pip entry's `directories:` in\n"
                f"  {DEPENDABOT.relative_to(REPO_ROOT)}. Its own comment warns that a new\n"
                "  requirements file lands in the blind spot without one, and nothing\n"
                "  enforced that until this check."
            )

    entries = _pip_entries()
    if entries:
        for pkg, reason in sorted(CORRECTNESS_PINNED.items()):
            declaring = {str(Path(req).parent).strip("/") for req in tracked if pkg in _declarations(REPO_ROOT / req)}
            for dirs, majors_ignored in entries:
                if dirs & declaring and pkg not in majors_ignored:
                    failures.append(
                        f"'{pkg}' is declared under {sorted(dirs & declaring)} and that Dependabot\n"
                        f"  pip entry does not ignore its major updates.\n"
                        f"  Reason the pin exists: {reason}.\n"
                        "  Add to that entry:\n"
                        f'    ignore:\n      - dependency-name: "{pkg}"\n'
                        '        update-types: ["version-update:semver-major"]\n'
                        "  A pin with a correctness reason that only a comment defends gets\n"
                        "  crossed by a robot that cannot read the comment. That happened twice."
                    )

    compared = 0
    for group, (rationale, members) in CO_INSTALLATION_GROUPS.items():
        pins: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for member in members:
            path = REPO_ROOT / member
            if not path.is_file():
                continue
            for name, spec in _declarations(path).items():
                pins[name].append((member, spec))

        for name, sites in sorted(pins.items()):
            if len(sites) < 2:
                continue
            compared += 1
            specs = {spec for _, spec in sites}
            if len(specs) == 1:
                continue
            detail = "\n".join(f"    {spec or '(unpinned)':<16} {member}" for member, spec in sorted(sites))
            failures.append(
                f"group '{group}' disagrees on '{name}':\n{detail}\n"
                f"  These files are installed into one environment:\n"
                f"    {rationale}.\n"
                f"  Make the specifiers identical. Whichever version wins, the\n"
                f"  environment that trains the model and the image that serves it\n"
                f"  must resolve to the same one."
            )

    if failures:
        print("FAIL: requirements files that share an environment disagree on their pins.")
        print()
        for item in failures:
            print(f"  - {item}\n")
        return 1

    print(
        f"[dependency-pins] OK — {len(tracked)} requirements file(s) in "
        f"{len(CO_INSTALLATION_GROUPS)} co-installation group(s); "
        f"{compared} shared pin(s) agree, all watched by Dependabot; "
        f"{len(CORRECTNESS_PINNED)} correctness-pinned package(s) boundary-protected."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
