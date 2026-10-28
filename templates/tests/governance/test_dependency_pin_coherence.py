"""Contract — the pin-coherence gate catches the defect it was written for.

Why this exists
---------------
``scripts/check_dependency_pin_coherence.py`` passes on a clean tree, and
``test_gate_scope_ratchet.py`` already asserts that much. Passing on a clean
tree is exactly what the *broken* gates in this repository's history also did.
The question a control has to answer is the other one: **does it fail when the
thing it guards is wrong?**

So each test here reintroduces a specific real-world shape of the defect into a
throwaway copy of the repository layout, and asserts the gate rejects it —
naming the package, so the failure is actionable rather than a bare exit code.

The defect being guarded, for the record: ``templates/service/requirements.txt``
pinned ``scikit-learn ~= 1.5.0`` and ``pandera ~= 0.23.0`` while
``templates/service/eda/requirements.txt`` pinned ``~= 1.9`` and ``~= 0.33``.
``docs/TUTORIAL.md`` installs both into one environment in two separate
commands, so pip never reports the conflict — it silently upgrades. The adopter
trained against scikit-learn 1.9 and served from an image built with 1.5.2.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / "scripts" / "check_dependency_pin_coherence.py"


def _correctness_pinned() -> list[str]:
    """Packages the gate requires a major-ignore for, read from the gate itself."""
    source = GATE.read_text(encoding="utf-8")
    block = source.split("CORRECTNESS_PINNED: dict[str, str] = {", 1)[1].split("\n}", 1)[0]
    return re.findall(r'^\s*"([\w.-]+)":', block, re.M)


def _declared_members() -> list[str]:
    """Group membership, read from the gate itself rather than retyped here.

    The gate fails when a declared member is not tracked by git, which is the
    right behaviour — a stale group is a group that stopped checking something.
    It also means a fixture with a hardcoded file list breaks the moment a
    member is added, and the natural repair is to weaken the gate. Reading the
    real declaration keeps the pressure off the useful half.
    """
    source = GATE.read_text(encoding="utf-8")
    body = source.split("CO_INSTALLATION_GROUPS", 1)[1].split("\n_REQ", 1)[0]
    return re.findall(r'"((?:templates|examples)/[\w./-]*requirements[\w.-]*\.txt)"', body)


def _sandbox(tmp_path: Path, service: str, eda: str, *, extra: dict[str, str] | None = None) -> Path:
    """A minimal git repo shaped like this one, so the gate's discovery works.

    The gate finds requirements files with ``git ls-files``, deliberately: a
    hardcoded list cannot notice a new file. That means the fixture has to be a
    real repository with real tracked files, not a bare directory.
    """
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)

    # Every declared member exists, so the "declared but not tracked" branch
    # does not fire in tests that are about something else. Members the test
    # does not care about are inert `-r requirements.txt` includes.
    members = _declared_members()
    assert members, "no group members parsed out of the gate — this fixture would test nothing"
    for member in members:
        target = root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("-r requirements.txt\n", encoding="utf-8")

    (root / "templates" / "service" / "requirements.txt").write_text(service, encoding="utf-8")
    (root / "templates" / "service" / "eda" / "requirements.txt").write_text(eda, encoding="utf-8")
    (root / "examples" / "minimal" / "requirements.txt").write_text("pytest ~= 9.1.1\n", encoding="utf-8")
    for rel, body in (extra or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    # The gate resolves REPO_ROOT from its own location (parents[1]), so it has
    # to be copied into the sandbox rather than run from this repository.
    (root / "scripts" / GATE.name).write_text(GATE.read_text(encoding="utf-8"), encoding="utf-8")

    # A Dependabot config covering every member, so the coverage check does not
    # fire in tests that are about pin coherence. Tests that are about coverage
    # overwrite this.
    (root / ".github").mkdir(parents=True, exist_ok=True)
    # Correctness-pinned packages get their major-ignore here too, derived from
    # the gate. Otherwise the boundary check fires in every test that is about
    # something else, and the natural repair is to weaken the boundary check.
    watched = sorted({str(Path(m).parent).strip(".") or "/" for m in members})
    (root / ".github" / "dependabot.yml").write_text(
        "version: 2\nupdates:\n"
        + "".join(
            f'  - package-ecosystem: "pip"\n    directory: "/{d.lstrip("/")}"\n' + _IGNORE_BLOCK for d in watched
        ),
        encoding="utf-8",
    )

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def _run(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / GATE.name)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout + proc.stderr


COHERENT_SERVICE = "scikit-learn ~= 1.5.0\npandera ~= 0.23.0\nnumpy ~= 1.26.0\n"
_IGNORE_BLOCK = "    ignore:\n" + "".join(
    f'      - dependency-name: "{pkg}"\n        update-types: ["version-update:semver-major"]\n'
    for pkg in _correctness_pinned()
)
COHERENT_EDA = "numpy ~= 1.26.0\nchardet ~= 7.6\n"


def test_a_coherent_tree_passes(tmp_path: Path) -> None:
    """The control: without this, every assertion below could be a false alarm."""
    code, out = _run(_sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA))
    assert code == 0, out
    assert "shared pin(s) agree" in out


def test_the_historical_divergence_is_rejected(tmp_path: Path) -> None:
    """The exact pins that shipped: scikit-learn 1.5.0 vs 1.9, pandera 0.23.0 vs 0.33."""
    code, out = _run(
        _sandbox(
            tmp_path,
            COHERENT_SERVICE,
            "scikit-learn ~= 1.9\npandera ~= 0.33\nnumpy ~= 1.26.0\n",
        )
    )
    assert code == 1, f"the gate accepted the very divergence it exists to catch:\n{out}"
    assert "scikit-learn" in out and "pandera" in out, f"failure must name the packages:\n{out}"
    assert "~=1.5.0" in out and "~=1.9" in out, f"failure must show both specifiers:\n{out}"


@pytest.mark.parametrize(
    ("service_spec", "eda_spec"),
    [
        ("numpy ~= 1.26.0", "numpy ~= 1.26"),  # the shape that shipped
        ("numpy ~= 1.26.0", "numpy >= 1.26, < 2.0"),  # same intent, different operator
        ("numpy ~= 1.26.0", "numpy"),  # unpinned re-declaration
    ],
)
def test_specifiers_must_be_identical_not_merely_similar(tmp_path: Path, service_spec: str, eda_spec: str) -> None:
    """``~=1.26`` and ``~=1.26.0`` read alike and resolve differently.

    ``~=1.26`` is ``>=1.26,<2.0``; ``~=1.26.0`` is ``>=1.26.0,<1.27.0``. The
    looser one silently readmits the versions the tighter one was written to
    exclude — which for numpy is the case D-05 names explicitly.
    """
    code, out = _run(_sandbox(tmp_path, f"{service_spec}\n", f"{eda_spec}\n"))
    assert code == 1, f"'{service_spec}' and '{eda_spec}' were accepted as equivalent:\n{out}"
    assert "numpy" in out


def test_a_new_requirements_file_cannot_join_unchecked(tmp_path: Path) -> None:
    """Group membership is total, so the gate cannot narrow the way its quarry does.

    If an ungrouped file were merely skipped, adding one would be the cheapest
    possible way to reintroduce the defect — and the gate would keep printing
    OK at its smaller size, which is the failure mode the whole ratchet exists
    to make impossible.
    """
    code, out = _run(
        _sandbox(
            tmp_path,
            COHERENT_SERVICE,
            COHERENT_EDA,
            extra={"templates/service/requirements-extra.txt": "scikit-learn ~= 1.9\n"},
        )
    )
    assert code == 1, f"an ungrouped requirements file was silently ignored:\n{out}"
    assert "requirements-extra.txt" in out
    assert "no co-installation group" in out


def test_an_unwatched_requirements_file_is_rejected(tmp_path: Path) -> None:
    """Dependabot's own comment warned about this and nothing enforced it.

    `.github/dependabot.yml` resolves its directories literally, so a
    requirements file in an unlisted directory is unwatched for vulnerable
    versions. The pip ecosystem was absent entirely once, while the repository
    carried four requirements files — three nominal controls over Python
    dependencies inert at the same time.
    """
    root = _sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA)
    (root / ".github").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "dependabot.yml").write_text(
        'version: 2\nupdates:\n  - package-ecosystem: "pip"\n    directory: "/examples/minimal"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)

    code, out = _run(root)
    assert code == 1, f"requirements files outside every pip entry were accepted:\n{out}"
    assert "no Dependabot pip entry watches" in out
    assert "templates/service/requirements.txt" in out


def test_dependabot_directories_plural_is_understood(tmp_path: Path) -> None:
    """`directories:` is how one entry covers the service and its EDA lane.

    Separate entries produced separate PRs for a shared package, and the
    coherence check above rejects each on its own — #148 (pandas) and #152
    (pyarrow) both failed that way. Reading only `directory:` would report the
    grouped config as a blind spot and push the fix back to the broken shape.
    """
    root = _sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA)
    (root / ".github").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "dependabot.yml").write_text(
        "version: 2\nupdates:\n"
        '  - package-ecosystem: "pip"\n'
        "    directories:\n"
        '      - "/templates/service"\n'
        '      - "/templates/service/eda"\n' + _IGNORE_BLOCK + '  - package-ecosystem: "pip"\n'
        '    directory: "/examples/minimal"\n' + _IGNORE_BLOCK,
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)

    code, out = _run(root)
    assert code == 0, f"a `directories:` (plural) entry was not recognised:\n{out}"
    assert "watched by Dependabot" in out


def test_a_correctness_pin_must_be_boundary_protected(tmp_path: Path) -> None:
    """The gap that produced #163, one day after #161 tried to close it.

    #159 bumped numpy to ~=2.5.3 while carrying
    `# numpy 2.x silently corrupts joblib models` forward unchanged on the same
    line. #161 added the major-ignore to the service entry and missed
    examples/minimal — which trains and serves a joblib model too — so #163
    arrived proposing exactly the same crossing.

    A fix scoped to one of two files that share a reason is a control narrower
    than its surface: the shape this gate exists to reject, reproduced by hand.
    """
    root = _sandbox(tmp_path, COHERENT_SERVICE + "numpy ~= 1.26.0\n", COHERENT_EDA)
    (root / ".github").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "dependabot.yml").write_text(
        "version: 2\nupdates:\n"
        '  - package-ecosystem: "pip"\n'
        "    directories:\n"
        '      - "/templates/service"\n'
        '      - "/templates/service/eda"\n'
        "    ignore:\n"
        '      - dependency-name: "numpy"\n'
        '        update-types: ["version-update:semver-major"]\n'
        '  - package-ecosystem: "pip"\n'
        '    directory: "/examples/minimal"\n',  # <- the #163 gap
        encoding="utf-8",
    )
    (root / "examples" / "minimal" / "requirements.txt").write_text("numpy ~= 1.26.0\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)

    code, out = _run(root)
    assert code == 1, f"an unprotected correctness pin was accepted:\n{out}"
    assert "numpy" in out
    assert "examples/minimal" in out
    assert "correctness reason" in out


def test_a_fully_protected_boundary_passes(tmp_path: Path) -> None:
    """The control. Without it the assertion above could be a false alarm."""
    root = _sandbox(tmp_path, COHERENT_SERVICE + "numpy ~= 1.26.0\n", COHERENT_EDA)
    (root / ".github").mkdir(parents=True, exist_ok=True)
    ignore = '    ignore:\n      - dependency-name: "numpy"\n        update-types: ["version-update:semver-major"]\n'
    (root / ".github" / "dependabot.yml").write_text(
        "version: 2\nupdates:\n"
        '  - package-ecosystem: "pip"\n'
        "    directories:\n"
        '      - "/templates/service"\n'
        '      - "/templates/service/eda"\n' + ignore + '  - package-ecosystem: "pip"\n'
        '    directory: "/examples/minimal"\n' + ignore,
        encoding="utf-8",
    )
    (root / "examples" / "minimal" / "requirements.txt").write_text("numpy ~= 1.26.0\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)

    code, out = _run(root)
    assert code == 0, f"a fully protected boundary was rejected:\n{out}"
    assert "boundary-protected" in out


INSTALLER = REPO_ROOT / "scripts" / "install_test_env.py"
_UV_ROOT_ENTRY = '  - package-ecosystem: "uv"\n    directory: "/"\n'


def _with_root_groups(root: Path, groups: str, *, uv_entry: bool = True) -> Path:
    """Give the sandbox a root pyproject with dependency groups (ADR-053).

    The gate reads SERVICE_PIN_NAMES from the installer, so the installer is
    copied too; a fixture that stubbed the list would test a gate that can
    drift from the installer, which is the drift the import exists to remove.
    """
    (root / "pyproject.toml").write_text(f"[dependency-groups]\n{groups}", encoding="utf-8")
    (root / "scripts" / INSTALLER.name).write_text(INSTALLER.read_text(encoding="utf-8"), encoding="utf-8")
    if uv_entry:
        dependabot = root / ".github" / "dependabot.yml"
        dependabot.write_text(dependabot.read_text(encoding="utf-8") + _UV_ROOT_ENTRY, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


ONE_OWNER_GROUPS = 'dev = ["pre-commit ~= 4.7.0", "copier ~= 9.19.1"]\ntemplate-tests = ["pytest-asyncio ~= 1.4.0"]\n'


def test_root_groups_with_one_owner_per_pin_pass(tmp_path: Path) -> None:
    """The control for ADR-053: the shape the repository ships must pass."""
    root = _with_root_groups(_sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA), ONE_OWNER_GROUPS)
    code, out = _run(root)
    assert code == 0, out
    assert "2 root pyproject group(s)" in out


def test_a_group_may_not_retype_a_pin_its_environment_already_owns(tmp_path: Path) -> None:
    """The copy ADR-053 refuses, and why: it would deadlock ADR-052's auto-merge.

    examples/minimal/requirements.txt owns pytest in the test environment. A
    copy in `template-tests` would have to stay byte-identical (rule 1), and
    Dependabot's pip PR bumping the original cannot bump the copy and uv.lock —
    so every such PR would fail. The failure has to name the owner.
    """
    groups = 'template-tests = ["pytest-asyncio ~= 1.4.0", "pytest ~= 9.1.1"]\n'
    code, out = _run(_with_root_groups(_sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA), groups))
    assert code == 1, f"a pin owned by a requirements file was retyped in pyproject and accepted:\n{out}"
    assert "redeclares 'pytest'" in out and "examples/minimal/requirements.txt" in out


def test_a_service_pin_may_not_be_retyped_either(tmp_path: Path) -> None:
    """The service pins reach the test environment through the installer, not a file of their own."""
    groups = 'template-tests = ["pytest-asyncio ~= 1.4.0", "pyarrow ~= 24.0.0"]\n'
    code, out = _run(_with_root_groups(_sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA), groups))
    assert code == 1, f"a service pin was retyped in pyproject and accepted:\n{out}"
    assert "redeclares 'pyarrow'" in out and "install_test_env.py" in out


def test_the_tooling_environment_may_declare_what_the_test_environment_owns(tmp_path: Path) -> None:
    """Separate environments may differ — the same reasoning as two separate requirements groups.

    `dev` is installed alone into the root .venv, where no requirements file is
    installed, so pytest there has no other owner. A gate that rejected it
    would be enforcing repo-wide agreement ADR-048 explicitly does not require.
    """
    groups = 'dev = ["pre-commit ~= 4.7.0", "pytest ~= 9.2.0"]\ntemplate-tests = ["pytest-asyncio ~= 1.4.0"]\n'
    code, out = _run(_with_root_groups(_sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA), groups))
    assert code == 0, f"a pin in an environment no requirements file shares was rejected:\n{out}"


def test_a_root_group_must_join_a_declared_environment(tmp_path: Path) -> None:
    """Membership is total for pyproject groups too, or a new group is the cheap way around the rule."""
    groups = ONE_OWNER_GROUPS + 'lint = ["ruff ~= 0.15.0"]\n'
    code, out = _run(_with_root_groups(_sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA), groups))
    assert code == 1, f"a group that joins no declared environment was accepted:\n{out}"
    assert "[dependency-groups].lint" in out and "join no declared environment" in out


def test_root_groups_must_be_watched_by_a_uv_entry(tmp_path: Path) -> None:
    """uv.lock without a Dependabot uv entry ages unwatched — rule 2's blind spot, for the lock."""
    root = _with_root_groups(_sandbox(tmp_path, COHERENT_SERVICE, COHERENT_EDA), ONE_OWNER_GROUPS, uv_entry=False)
    code, out = _run(root)
    assert code == 1, f"root dependency groups with no Dependabot uv entry were accepted:\n{out}"
    assert "Dependabot `uv` entry" in out


def test_separate_groups_may_legitimately_differ(tmp_path: Path) -> None:
    """examples/minimal has its own venv, so its pytest pin need not match.

    A gate that forced repo-wide agreement would be wrong, and the pressure to
    silence it would land on the useful half.
    """
    code, out = _run(
        _sandbox(
            tmp_path,
            COHERENT_SERVICE + "pytest ~= 8.3.0\n",
            COHERENT_EDA,
        )
    )
    assert code == 0, f"pytest differs across two groups that never share an environment:\n{out}"
