#!/usr/bin/env bash
# Install the template's CI tooling into the Python on PATH, exactly as uv.lock
# pins it (ADR-054).
#
# Why this exists
# ---------------
# A dozen CI jobs used to run `pip install "copier>=9.0.0" jsonschema` (or
# `pip install pyyaml`): no upper bound, no hashes, a different answer on every
# run. The repository already had the answer — the `dev` group of the root
# pyproject.toml, locked in uv.lock since ADR-053 — and nothing used it.
#
# Why --python and not `uv sync`
# ------------------------------
# `uv sync` builds a .venv, and a job that then calls `python3` on one of the
# repository's gate scripts, or `pip`, would silently use a different
# interpreter than the one setup-python installed. Installing the locked set into THAT interpreter changes nothing
# else about the job: same `python`, same `pip`, same PATH — only the tooling
# is now exactly the locked version.
#
# Why this fails instead of installing less
# -----------------------------------------
# The export is written to a file first. Piped straight into the installer, a
# failed `uv export` leaves the installer reading an empty requirements list,
# which is a success that installs nothing — a gate that passes because its
# parser is missing is the failure mode these jobs exist to catch.
#
# Requires `uv` and `python` on PATH: astral-sh/setup-uv and actions/setup-python
# run first (held by templates/tests/governance/test_ci_tooling_locked.py).
set -euo pipefail

command -v uv >/dev/null || { echo "::error::uv is not on PATH; run astral-sh/setup-uv first"; exit 1; }
command -v python >/dev/null || { echo "::error::python is not on PATH; run actions/setup-python first"; exit 1; }

cd "$(dirname "${BASH_SOURCE[0]}")/.."

requirements="$(mktemp)"
trap 'rm -f "$requirements"' EXIT

# --frozen: use uv.lock as it is; never re-resolve. A lock that does not match
# pyproject.toml is `uv lock --check`'s job, not something to paper over here.
uv export --frozen --only-group dev --no-emit-project --quiet --output-file "$requirements"

if ! grep -q -- '--hash=sha256:' "$requirements"; then
  echo "::error::uv export produced no hashed requirements; refusing to install an unverified set"
  exit 1
fi

# --require-hashes: every wheel is checked against uv.lock.
# --strict: fail if the installed set is not internally consistent.
uv pip install --python "$(command -v python)" --require-hashes --strict --quiet -r "$requirements"

count="$(grep -cE '^[A-Za-z0-9_.-]+==' "$requirements")"
echo "[ci-tooling] installed ${count} locked packages into $(python -c 'import sys; print(sys.executable)')"
