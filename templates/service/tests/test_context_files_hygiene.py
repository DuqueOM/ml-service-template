"""Context-file hygiene — the enforcement `docs/agentic/contextualization.md` §7 promises.

That section states, as settled policy, that this test "enforces at every
PR" four properties of the agentic context files. It was cited as the
enforcing control and never written, so the properties were documented and
unchecked: a contributor could commit a real AWS key inside
``company_context.example.yaml`` and nothing would object.

The four properties, verbatim from §7:

1. No ``*_context.local*.yaml`` tracked by git.
2. ``*.example.yaml`` files parse against ``context.schema.json``.
3. ``*.example.yaml`` files contain no real-looking secret patterns
   (AKIA-prefix AWS keys, AIza-prefix GCP keys, PEM blocks, bearer
   tokens, credential-laden URLs).
4. Placeholders in ``*.example.yaml`` use the ``{PlaceholderName}`` format
   so the validator can detect unreplaced values.

Two implementation notes where §7 is looser than code has to be:

*Which schema.* §7 names ``context.schema.json``, which is the schema for
the company and project context pair. Two sibling files carry their own
schemas (``adopter_context.schema.json``, ``service_spec.schema.json``).
The test validates each example against the schema that governs it,
discovered by filename, which is what §7 means rather than what it says.

*Properties 2 and 4 conflict, and §7 does not say how.* ``service_spec``
declares ``service_slug`` with ``pattern: ^[a-z][a-z0-9_]*$``, and the
example sets it to ``{service_slug}`` — correct under property 4, invalid
under property 2. An example file is by definition not filled in, so it
cannot satisfy value-level constraints on the fields it deliberately
leaves blank. The resolution taken here: **structure is enforced, values
that are placeholders are exempt.** Required keys, types and every
non-placeholder value must satisfy the schema; a validation error is
tolerated only when the instance that failed is exactly a
``{Placeholder}`` string. Anything else fails.

*Where placeholders are checked.* Property 4 exists "so the validator can
detect unreplaced values", so it is enforced against parsed **values**.
Comment prose that names a file pattern — ``<service_slug>_context.local.yaml``
— is documentation, not an unreplaced value. Property 3 is the opposite:
secrets are scanned in the **raw text**, because a leaked key in a comment
is still leaked.

This test runs both in the template repo and inside a scaffolded service;
it discovers files rather than hard-coding either layout.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

# Walk up to the repository root — works from templates/service/tests/ in the
# template repo and from tests/ in a generated service.
#
# The fallback is the SERVICE ROOT, not `parents[2]`. A freshly scaffolded
# service has no `.git` yet — Copier's own closing message lists
# "Initialize git" as step 3 — so the walk finds nothing, and the old fallback
# landed two directories above the service, on whatever the adopter happened
# to have there. Every path built from it was then wrong, and the two
# git-dependent checks below died with "not a git repository" pointing at a
# directory the adopter never asked about.
_HERE = Path(__file__).resolve()
_SERVICE_ROOT = _HERE.parents[1]
REPO_ROOT = next(
    (parent for parent in _HERE.parents if (parent / ".git").exists()),
    _SERVICE_ROOT,
)
_IS_GIT_REPO = (REPO_ROOT / ".git").exists()

# These two properties are about what git tracks and ignores, so they need a
# repository. Before `git init` there is nothing to assert — this is an absent
# precondition, not a control that failed, and it starts evaluating the moment
# the adopter runs step 3 of the scaffold instructions.
_needs_git = pytest.mark.skipif(
    not _IS_GIT_REPO,
    reason=(
        f"no git repository at {REPO_ROOT} — run `git init` (scaffold step 3); these two checks evaluate from then on"
    ),
)

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache"}

# Schema selected by filename stem. Anything not listed is schema-less by
# design and only gets the secret and placeholder checks.
_SCHEMA_FOR_STEM = {
    "company_context": "context.schema.json",
    "project_context": "context.schema.json",
    "adopter_context": "adopter_context.schema.json",
    "service_spec": "service_spec.schema.json",
}

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GCP API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("PEM private key block", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("bearer token", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{20,}")),
    ("credentials embedded in URL", re.compile(r"://[^/\s:@]+:[^/\s@]{3,}@")),
]

# Placeholder styles that defeat the `{Name}` convention: an unreplaced value
# written any other way is invisible to the validator.
_BAD_PLACEHOLDER = re.compile(r"<[A-Za-z_][A-Za-z0-9_ .\-]*>|\bTODO\b|\bCHANGEME\b|\bFIXME\b|\bXXXX+\b")

# The sanctioned form: a value that is nothing but `{Name}` is an unfilled
# placeholder and is exempt from value-level schema constraints.
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _example_files() -> list[Path]:
    return sorted(
        path for path in REPO_ROOT.rglob("*.example.yaml") if not _SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts)
    )


def _iter_string_values(node: object, trail: str = "") -> list[tuple[str, str]]:
    """Flatten a parsed YAML document to ``(dotted.path, string value)`` pairs."""
    if isinstance(node, dict):
        out: list[tuple[str, str]] = []
        for key, value in node.items():
            out += _iter_string_values(value, f"{trail}.{key}" if trail else str(key))
        return out
    if isinstance(node, list):
        out = []
        for index, value in enumerate(node):
            out += _iter_string_values(value, f"{trail}[{index}]")
        return out
    return [(trail, node)] if isinstance(node, str) else []


EXAMPLES = _example_files()


def test_example_files_are_discovered() -> None:
    """Guard the guard: a discovery bug would make every test below vacuous."""
    assert EXAMPLES, f"no *.example.yaml found under {REPO_ROOT}"


# --------------------------------------------------------------------------
# Property 1 — no local context file is ever tracked
# --------------------------------------------------------------------------


@_needs_git
def test_no_local_context_file_is_tracked() -> None:
    """`*_context.local*.yaml` holds real values and is gitignored by design."""
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    offenders = [path for path in tracked if re.search(r"_context\.local.*\.ya?ml$", path)]
    assert not offenders, f"local context files must never be tracked: {offenders}"


@_needs_git
def test_local_context_pattern_is_gitignored() -> None:
    """Being untracked today is luck; being ignored is the control."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "my_service_context.local.yaml"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, "*_context.local*.yaml is not covered by .gitignore"


# --------------------------------------------------------------------------
# Property 2 — every example validates against the schema that governs it
# --------------------------------------------------------------------------


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_example_parses_as_yaml(example: Path) -> None:
    loaded = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{example} did not parse to a mapping"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_example_validates_against_its_schema(example: Path) -> None:
    stem = example.name.removesuffix(".example.yaml")
    schema_name = _SCHEMA_FOR_STEM.get(stem)
    if schema_name is None:
        pytest.skip(f"{stem} has no governing schema by design")
    schema_path = example.parent / schema_name
    if not schema_path.exists():
        pytest.skip(f"{schema_name} not present beside {example.name}")

    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    document = yaml.safe_load(example.read_text(encoding="utf-8"))

    # Structure is enforced; values left as `{Placeholder}` are exempt,
    # because an example file is by construction not filled in.
    real = [
        error
        for error in jsonschema.Draft202012Validator(schema).iter_errors(document)
        if not (isinstance(error.instance, str) and _PLACEHOLDER.fullmatch(error.instance))
    ]
    assert not real, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in real)


# --------------------------------------------------------------------------
# Property 3 — no real-looking secret, anywhere in the file including comments
# --------------------------------------------------------------------------


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_example_contains_no_real_looking_secret(example: Path) -> None:
    text = example.read_text(encoding="utf-8")
    hits = [
        f"{label} at line {text[: match.start()].count(chr(10)) + 1}"
        for label, pattern in _SECRET_PATTERNS
        for match in pattern.finditer(text)
    ]
    assert not hits, f"{example.relative_to(REPO_ROOT)} looks like it carries a real secret: {hits}"


def test_secret_patterns_actually_match_known_shapes() -> None:
    """A scanner that matches nothing passes everything. Pin the patterns."""
    samples = {
        "AWS access key id": "AKIAIOSFODNN7EXAMPLE",
        # Assembled at runtime, and obviously fake: a literal key-shaped string
        # is what GitHub secret scanning alerts on, in every repository that
        # copies this file. It did, in the template and in a generated service.
        "GCP API key": "AIza" + "Example" * 5,
        # Assembled like the key above: a literal PEM header trips every
        # private-key hook in every repository that copies this file.
        "PEM private key block": "-----BEGIN RSA " + "PRIVATE KEY-----",
        "bearer token": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123",
        "credentials embedded in URL": "postgres://user:hunter2@db.internal:5432/app",
    }
    for label, pattern in _SECRET_PATTERNS:
        assert pattern.search(samples[label]), f"{label} pattern no longer matches its own sample"


# --------------------------------------------------------------------------
# Property 4 — placeholders use {Name}, so unreplaced values stay detectable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_placeholder_values_use_the_brace_convention(example: Path) -> None:
    document = yaml.safe_load(example.read_text(encoding="utf-8"))
    offenders = [
        f"{path} = {value!r}" for path, value in _iter_string_values(document) if _BAD_PLACEHOLDER.search(value)
    ]
    assert not offenders, (
        f"{example.relative_to(REPO_ROOT)} uses a placeholder style other than "
        f"{{PlaceholderName}}, which the validator cannot detect as unreplaced: {offenders}"
    )
