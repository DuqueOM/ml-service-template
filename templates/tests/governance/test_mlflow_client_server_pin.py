"""Contract — the MLflow client and the tracking server it talks to are one version.

Why this exists
---------------
The compose files pin ``ghcr.io/mlflow/mlflow`` to "the version the client is
pinned to", because a server that moves independently of its client makes
compatibility unverifiable. Nothing held that sentence. Dependabot updates the
client through pip and the server through docker — two ecosystems it cannot
group — and #292 moved the client to 3.17.1 while both servers stayed at
v3.16.0, with every check green.

The split also hid a security fix. CVE-2026-53995 (arbitrary file write through
FileStore path traversal) is fixed in 3.17.2 and lives in the SERVER: raising
the client alone would have left every adopter's tracking server exposed.

So this asserts one version across every place it is written: the training
requirements, the service ``pyproject.toml``, and each compose file that runs
the server.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE = REPO_ROOT / "templates" / "service"

CLIENT_PINS = (SERVICE / "requirements-train.txt", SERVICE / "pyproject.toml")
SERVER_IMAGES = (REPO_ROOT / "docker-compose.yml", SERVICE / "infra" / "docker-compose.mlflow.yml")

_CLIENT = re.compile(r"""^\s*"?mlflow\s*~=\s*([\d.]+)""", re.MULTILINE)
_SERVER = re.compile(r"image:\s*ghcr\.io/mlflow/mlflow:v([\d.]+)")


def _versions(path: Path, pattern: re.Pattern[str]) -> list[str]:
    return pattern.findall(path.read_text(encoding="utf-8"))


def test_every_place_names_exactly_one_version() -> None:
    for path in CLIENT_PINS:
        assert len(_versions(path, _CLIENT)) == 1, f"{path.relative_to(REPO_ROOT)} must pin mlflow once, with ~="
    for path in SERVER_IMAGES:
        assert len(_versions(path, _SERVER)) == 1, f"{path.relative_to(REPO_ROOT)} must run one pinned MLflow server"


def test_the_client_and_every_server_agree() -> None:
    found = {
        str(path.relative_to(REPO_ROOT)): version
        for path, pattern in [*((p, _CLIENT) for p in CLIENT_PINS), *((p, _SERVER) for p in SERVER_IMAGES)]
        for version in _versions(path, pattern)
    }
    assert len(set(found.values())) == 1, (
        f"the MLflow client and server are pinned apart: {found}. Move every one of them in the same "
        f"change — Dependabot raises them separately, and a server left behind keeps its CVEs"
    )


def test_the_pin_is_at_least_the_cve_fix() -> None:
    """CVE-2026-53995 is fixed in 3.17.2; a floor below it admits the vulnerable release."""
    (version,) = set(_versions(CLIENT_PINS[0], _CLIENT))
    assert tuple(int(part) for part in version.split(".")) >= (3, 17, 2)
