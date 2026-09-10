#!/usr/bin/env python3
"""Verify that nothing the served image runs imports a training-only package.

Why this exists
---------------
``templates/service/requirements.txt`` is what the Dockerfile installs, and it
carried mlflow, optuna, pytest, pytest-cov, locust and httpx — none of which
any code in the image imports. Measured on that set:

    resolved packages                     151  ->  35
    trivy CRITICAL/HIGH, fixable           24  ->   4
    of which CRITICAL                       7  ->   0

So twenty of the twenty-four advisories that a generated service carried, and
every CRITICAL one, were in packages the inference pods installed and never
called. See ADR-049.

That split is only worth what keeps it true. A single ``import mlflow`` added
to a module the drift CronJob reaches would silently put the whole tree back —
and it would not fail any test, because the runtime image is never built in the
unit lanes.

What this checks
----------------
1. The **import closure** of everything the image actually runs, walked from
   the entrypoints the Dockerfile itself smoke-imports plus its ``CMD``. The
   seeds are parsed out of the Dockerfile rather than listed here: a hand-kept
   list would drift from the image the moment someone added a CronJob, and a
   gate that checks a stale set of entrypoints is worse than none.

2. **No module in that closure imports a distribution declared in
   requirements-train.txt or requirements-dev.txt** — unless the import is
   guarded (inside ``try:``/``except ImportError`` or deferred into a
   function). Guarded imports are how ``common_utils`` already reaches boto3,
   google-cloud and opentelemetry: the package is optional, absence is handled,
   and the image does not need it.

3. **No script or workflow installs the runtime set and then runs a dev tool.**
   Before the split, ``requirements.txt`` carried pytest, httpx and locust, so
   any lane could install one file and run the suite. Two lanes were doing
   exactly that, and the split broke both: this repository's own
   ``template-context-tests.yml``, and ``scripts/test_scaffold.sh``, whose
   smoke chain installed the runtime set into a venv and then invoked ``pytest``
   — which resolved to the *system* interpreter outside that venv and reported

       ModuleNotFoundError: No module named 'numpy'

   pointing at numpy, which was installed, rather than at pytest, which was
   not. The first was caught by reading; the second by CI, after the fix for
   the first had already shipped. So this is the third check: a control as wide
   as the surface, rather than one fix per consumer.

4. **The closure is not empty.** A walk that resolves nothing reports as a
   pass, which is the failure mode every gate in this repository has had at
   least once.

What this deliberately does NOT check
-------------------------------------
Transitive third-party imports. If the image imports pandas and pandas imports
something else, that is pip's business, not this gate's. The question here is
narrower and answerable statically: **does OUR code, on the path the image
runs, name a package we deliberately left out of the image?**

Nor does it claim the CVEs are gone. They are still reported against
requirements-train.txt by the repository's own dependency scan, which is
correct — the risk moved out of the inference pods, it did not evaporate.

Exit codes
----------
- 0: the runtime closure imports nothing from the training or dev sets.
- 1: a runtime module imports a training-only package, the closure is empty, or
  the Dockerfile's entrypoints could not be parsed.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE = REPO_ROOT / "templates" / "service"
DOCKERFILE = SERVICE / "Dockerfile"
RUNTIME_REQ = SERVICE / "requirements.txt"
EXCLUDED_REQS = (SERVICE / "requirements-train.txt", SERVICE / "requirements-dev.txt")

# `python -c "import a.b, c"` inside the Dockerfile's smoke RUN steps, and the
# uvicorn CMD's `app.main:app`.
_SMOKE_IMPORT = re.compile(r"""python\s+-c\s+["']import\s+(?P<mods>[^"']+)["']""")
_CMD_APP = re.compile(r'"uvicorn",\s*"(?P<app>[\w.]+):')

# Distributions whose import name differs from the name pip installs.
_IMPORT_NAME = {
    "pytest-cov": "pytest_cov",
    "pre-commit": "pre_commit",
    "scikit-learn": "sklearn",
    "pyyaml": "yaml",
    "prometheus-client": "prometheus_client",
    "imbalanced-learn": "imblearn",
}
# Declared but never imported by anything (linters, formatters, runners).
_NOT_IMPORTABLE = {"ruff", "mypy", "bandit", "pre_commit", "locust"}

_REQ_LINE = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:[~=<>!].*)?$")

# Files that install dependencies and then run things. Scanned for the
# "installs runtime, invokes dev tooling" mistake described above.
_LANE_GLOBS = (
    "scripts/*.sh",
    "templates/scripts/*.sh",
    ".github/workflows/*.yml",
    "templates/service/.github/workflows/*.yml",
)
# `pip install -r <...>requirements.txt` — the SERVICE runtime set only.
# `examples/minimal/requirements.txt` is its own co-installation group
# (ADR-048) and legitimately carries its own pytest pin, so it is excluded by
# path rather than by hoping the pattern misses it.
_INSTALLS_RUNTIME = re.compile(r"(?:pip|uv pip)\s+install[^\n]*?-r\s+(?!\S*examples/)(\S*?)(?<![-\w])requirements\.txt")
_INSTALLS_SUPERSET = re.compile(r"(?:pip|uv pip)\s+install[^\n]*?-r\s+\S*requirements-(?:dev|train)\.txt")
_DEV_TOOLS = re.compile(r"(?<![-\w/])(?:pytest|locust|ruff|mypy|bandit|pre-commit)(?![-\w])")


def _declared(path: Path) -> set[str]:
    """Import names for the distributions a requirements file declares directly."""
    names: set[str] = set()
    if not path.is_file():
        return names
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQ_LINE.match(line.split("#")[0].strip())
        if not match:
            continue
        dist = match.group("name").lower()
        names.add(_IMPORT_NAME.get(dist, dist.replace("-", "_")))
    return names


def _seeds() -> list[str]:
    """Entrypoint modules, read from the Dockerfile so they cannot drift."""
    if not DOCKERFILE.is_file():
        return []
    text = DOCKERFILE.read_text(encoding="utf-8")
    slug_dirs = [d.name for d in (SERVICE / "src").iterdir() if d.is_dir() and not d.name.startswith("__")]
    slug = slug_dirs[0] if len(slug_dirs) == 1 else None

    found: list[str] = []
    for match in _SMOKE_IMPORT.finditer(text):
        for mod in match.group("mods").split(","):
            mod = mod.strip()
            if slug:
                mod = mod.replace("{@ service_slug @}", slug)
            if mod:
                found.append(mod)
    cmd = _CMD_APP.search(text)
    if cmd:
        found.append(cmd.group("app"))
    return sorted(set(found))


def _resolve(dotted: str) -> Path | None:
    parts = dotted.split(".")
    for base in (SERVICE, SERVICE / "src"):
        module = base.joinpath(*parts).with_suffix(".py")
        if module.is_file():
            return module
        package = base.joinpath(*parts) / "__init__.py"
        if package.is_file():
            return package
    return None


def _guarded(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when an import is optional: inside try/except, or deferred into a def."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        if isinstance(current, ast.Try):
            return True
        current = parents.get(current)
    return False


def _walk(seeds: list[str]) -> tuple[set[Path], list[tuple[Path, int, str]]]:
    """Return the reachable internal modules and every unguarded third-party import."""
    seen: set[Path] = set()
    external: list[tuple[Path, int, str]] = []
    stack = [p for p in (_resolve(s) for s in seeds) if p is not None]

    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)

        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        package = _dotted(path).rsplit(".", 1)[0]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                base = package
                if node.level:
                    segments = package.split(".")
                    base = ".".join(segments[: max(len(segments) - node.level + 1, 0)])
                targets = [f"{base}.{node.module}" if (node.level and node.module) else (node.module or base)]
            for target in targets:
                resolved = _resolve(target)
                if resolved is not None:
                    stack.append(resolved)
                elif not _guarded(node, parents):
                    external.append((path, node.lineno, target.split(".")[0]))
    return seen, external


def _dotted(path: Path) -> str:
    rel = path.relative_to(SERVICE).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _strip_comments(text: str) -> str:
    """Comment lines carry the explanation for these very rules — a mention is not a call."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _scopes(path: Path) -> list[tuple[str, str]]:
    """(scope label, shell text) pairs to check independently.

    A workflow is split per JOB, not per file. `validate-templates.yml`
    installs the service requirements in one job and runs ruff, mypy and bandit
    in others; treating the file as one scope would flag five pairs that never
    share a runner, and a gate that cries wolf five times gets deleted rather
    than obeyed. A shell script is a single scope, because it is one process.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".yml":
        return [(path.name, _strip_comments(text))]
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:  # pragma: no cover - other gates own YAML validity
        return []
    scopes: list[tuple[str, str]] = []
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        runs = [str(s.get("run", "")) for s in (job.get("steps") or []) if isinstance(s, dict)]
        scopes.append((f"job '{job_name}'", _strip_comments("\n".join(runs))))
    return scopes


def _lane_mismatches() -> list[tuple[str, str, str]]:
    """Scopes that install the runtime set and then invoke dev-only tooling."""
    found: list[tuple[str, str, str]] = []
    for pattern in _LANE_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if not path.is_file():
                continue
            for label, body in _scopes(path):
                if not _INSTALLS_RUNTIME.search(body) or _INSTALLS_SUPERSET.search(body):
                    continue
                for tool in sorted(set(_DEV_TOOLS.findall(body))):
                    found.append((path.relative_to(REPO_ROOT).as_posix(), label, tool))
    return found


def main() -> int:
    seeds = _seeds()
    if not seeds:
        print(f"FAIL: no runtime entrypoints found in {DOCKERFILE.relative_to(REPO_ROOT)}.")
        print("  The gate reads them from the smoke-import RUN steps and the uvicorn CMD.")
        print("  If those moved, this gate is checking nothing and must be updated.")
        return 1

    excluded: dict[str, Path] = {}
    for req in EXCLUDED_REQS:
        for name in _declared(req) - _declared(RUNTIME_REQ) - _NOT_IMPORTABLE:
            excluded[name] = req

    lane_failures = _lane_mismatches()

    modules, external = _walk(seeds)
    if not modules:
        print(f"FAIL: the import walk resolved no modules from {seeds}.")
        return 1

    violations = [(p, ln, mod) for p, ln, mod in external if mod in excluded]

    if lane_failures:
        print("FAIL: a lane installs the runtime dependency set and then runs a dev tool.")
        print()
        for lane, scope, tool in sorted(lane_failures):
            print(f"  - {lane} ({scope}) installs `-r requirements.txt` and invokes '{tool}'")
        print()
        print("  requirements.txt is the SERVING set (ADR-049). pytest, httpx, locust and")
        print("  the linters live in requirements-dev.txt, which opens with")
        print("  `-r requirements.txt` and is therefore a strict superset. Install that.")
        print()
        print("  This exact mistake broke two lanes when the split landed, and the second")
        print("  one blamed numpy — a package that WAS installed — because `pytest` fell")
        print("  through to an interpreter outside the venv.")
        return 1

    if violations:
        print("FAIL: code the served image runs imports a package the image does not install.")
        print()
        for module_path, lineno, mod in sorted(violations):
            declared_in = excluded[mod].relative_to(REPO_ROOT)
            print(f"  - {module_path.relative_to(REPO_ROOT)}:{lineno} imports '{mod}', declared in {declared_in}")
        print()
        print("  These packages are deliberately absent from the image: they carried 20 of the")
        print("  24 fixable CRITICAL/HIGH advisories a generated service reported, and all 7")
        print("  CRITICAL ones (ADR-049). An unguarded import here means the pod crashes on")
        print("  first boot, or the package goes back into the image and the split is over.")
        print()
        print("  Either move the code out of the runtime path, or guard the import")
        print("  (try/except ImportError, or defer it into a function) the way")
        print("  common_utils already does for boto3, google-cloud and opentelemetry.")
        return 1

    print(
        f"[dependency-partition] OK — {len(modules)} runtime module(s) reachable from "
        f"{len(seeds)} Dockerfile entrypoint(s); none imports any of the "
        f"{len(excluded)} training/dev-only package(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
