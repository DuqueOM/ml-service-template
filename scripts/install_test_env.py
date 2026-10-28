#!/usr/bin/env python3
"""Build the template's TEST environment from the files that own its pins (ADR-053).

Why this exists
---------------
The CI-Examples full suite runs in one environment assembled from three
sources: ``examples/minimal/requirements.txt`` as is, four of the generated
service's pins READ from its requirements files, and ``pytest-asyncio``. Until
ADR-053 that assembly was inline shell in ``ci-examples.yml``, so a contributor
could only reproduce it by copying the workflow by hand — and the documented
`uv sync` route installed nothing at all.

This script is now the single place that assembly lives. CI calls it with
``--installer pip`` (no behaviour change for the runner); ``make test-env``
calls it with ``--installer uv`` to build ``.venv-test`` locally. One
definition, two callers, so the local suite and the CI suite cannot drift.

One owner per pin
-----------------
Nothing here carries a version. The example's stack is owned by its
requirements file, the service pins by the service's files (where Dependabot
bumps them, ADR-052), and ``pytest-asyncio`` by the root ``pyproject.toml``
group ``template-tests``. ``check_dependency_pin_coherence.py`` imports
``SERVICE_PIN_NAMES`` from here, so the gate and the installer agree on what the
environment contains by construction.

The whole set is installed in ONE resolver run. Separate commands let the
second silently upgrade the first, which is the failure ADR-048 was written
about; one run either resolves the environment or says why it cannot.

Exit codes
----------
- 0: the environment was installed (or, with ``--dry-run``, would be).
- 1: a pin source is missing or malformed, or the installer failed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

EXAMPLE_REQUIREMENTS = "examples/minimal/requirements.txt"
SERVICE_REQUIREMENTS = (
    "templates/service/requirements.txt",
    "templates/service/requirements-dev.txt",
)
# pyarrow: Parquet for the prediction_logger tests. pyyaml, jsonschema: the
# template's unit tests. pytest-cov: the coverage matrix. Audit Medium-2 found
# pyarrow and pytest-asyncio missing, so tests skipped silently while coverage
# looked clean — the reason this list is counted, not trusted.
SERVICE_PIN_NAMES = ("pyarrow", "pyyaml", "jsonschema", "pytest-cov")
PYPROJECT_GROUP = "template-tests"
DEFAULT_VENV = ".venv-test"


def service_pins(root: Path = REPO_ROOT) -> list[str]:
    """The four service pins, exactly as their owning files declare them."""
    names = "|".join(re.escape(name) for name in SERVICE_PIN_NAMES)
    pattern = re.compile(rf"^({names})\s*[~=<>!]", re.IGNORECASE)
    found: list[str] = []
    for rel in SERVICE_REQUIREMENTS:
        for raw in (root / rel).read_text(encoding="utf-8").splitlines():
            if pattern.match(raw):
                found.append(raw.split("#", 1)[0].strip())
    declared = {re.split(r"[\s~=<>!]", pin, maxsplit=1)[0].lower() for pin in found}
    if len(found) != len(SERVICE_PIN_NAMES) or declared != set(SERVICE_PIN_NAMES):
        raise SystemExit(
            f"FAIL: expected {len(SERVICE_PIN_NAMES)} service pins ({', '.join(SERVICE_PIN_NAMES)}) "
            f"in {', '.join(SERVICE_REQUIREMENTS)}; found {found or 'none'}.\n"
            "  Installing a partial set is how tests skip silently while coverage looks clean."
        )
    return found


def template_test_specs(root: Path = REPO_ROOT) -> list[str]:
    """The root pyproject group that joins this environment."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    group = (data.get("dependency-groups") or {}).get(PYPROJECT_GROUP)
    if not group:
        raise SystemExit(f"FAIL: pyproject.toml declares no [dependency-groups].{PYPROJECT_GROUP}.")
    return [entry for entry in group if isinstance(entry, str)]


def _default_python(root: Path) -> str:
    pinned = root / ".python-version"
    return pinned.read_text(encoding="utf-8").strip() if pinned.is_file() else "3.11"


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--installer",
        choices=("pip", "uv"),
        required=True,
        help="pip installs into the running interpreter (CI); uv builds a separate venv",
    )
    parser.add_argument("--venv", default=DEFAULT_VENV, help=f"uv only: environment path (default {DEFAULT_VENV})")
    parser.add_argument("--python", default=None, help="uv only: interpreter version (default .python-version)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, install nothing")
    args = parser.parse_args(argv)

    pins = service_pins()
    extras = template_test_specs()
    print(f"[test-env] example stack: {EXAMPLE_REQUIREMENTS}")
    print("[test-env] service pins:")
    for pin in pins:
        print(f"    {pin}")
    print(f"[test-env] pyproject group {PYPROJECT_GROUP}: {', '.join(extras)}")

    with tempfile.TemporaryDirectory() as tmp:
        pins_file = Path(tmp) / "service-pins.txt"
        pins_file.write_text("\n".join(pins) + "\n", encoding="utf-8")
        sources = ["-r", str(REPO_ROOT / EXAMPLE_REQUIREMENTS), "-r", str(pins_file), *extras]

        commands: list[list[str]] = []
        if args.installer == "pip":
            commands.append([sys.executable, "-m", "pip", "install", *sources])
        else:
            uv = shutil.which("uv")
            if not uv:
                print("FAIL: --installer uv but `uv` is not on PATH. See CONTRIBUTING.md.", file=sys.stderr)
                return 1
            venv = (REPO_ROOT / args.venv).resolve()
            if not _venv_python(venv).is_file():
                commands.append([uv, "venv", str(venv), "--python", args.python or _default_python(REPO_ROOT)])
            commands.append([uv, "pip", "install", "--python", str(_venv_python(venv)), *sources])

        for command in commands:
            print("[test-env] $ " + " ".join(command))
            if args.dry_run:
                continue
            if subprocess.run(command, cwd=REPO_ROOT, check=False).returncode != 0:
                print(
                    "FAIL: the test environment did not install. The resolver output above says why.", file=sys.stderr
                )
                return 1

    print("[test-env] OK" + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
