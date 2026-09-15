"""Cloud-native secret loader with environment-aware resolution.

Usage (production code):
    from common_utils.secrets import get_secret

    api_key = get_secret("EXTERNAL_API_KEY")
    db_creds = get_secret("DB_PASSWORD", namespace=os.environ.get("SECRETS_PREFIX"))

Resolution order (environment read from ENV, ENVIRONMENT or APP_ENV):
    - local/dev: `.env.local` file (not committed), then os.environ
    - ci:        `os.environ` (from GitHub Secrets)
    - staging:   AWS Secrets Manager / GCP Secret Manager (via IRSA/WI)
    - production: AWS Secrets Manager / GCP Secret Manager (via IRSA/WI, required)

Secret addressing in staging/production (ADR-051)
-------------------------------------------------
The cloud secret id is derived from ``namespace`` and ``key`` with the SAME
scheme Terraform uses to create the secret, so what the pod asks for is what
exists:

    GCP  ``<namespace>-<key lowercased>``   e.g. acme-fraud-detector-api_key
    AWS  ``<namespace>/<key lowercased>``   e.g. acme/fraud-detector/api_key

``namespace`` is the ``SECRETS_PREFIX`` each cloud overlay sets
(``<project_name>-<service>`` on GCP, ``<project_name>/<service>`` on AWS).
The ``key`` is lowercased because Terraform's ``secret_names`` are lowercase
(``api_key``) while code reads ``API_KEY``. Before this contract the loader
asked GCP for ``<slug>-API_KEY`` and AWS for ``<slug>/API_KEY`` — names nothing
creates — and read ``ENV`` while every overlay sets ``ENVIRONMENT``.

Cloud lookups are cached for ``SECRETS_CACHE_TTL_SECONDS`` (default 300) so an
authenticated request does not become a Secret Manager round-trip, while a
rotated value is still picked up within the TTL.

Invariants enforced (D-17, D-18):
    - Never falls through to os.environ in staging/production
    - Never logs the secret value
    - Never falls back silently — raises on miss

ADR-001 compliance:
    - HashiCorp Vault is NOT supported here (explicitly deferred)
    - To use Vault: set SECRETS_BACKEND=vault and provide your own adapter
      (out of scope for this module)
"""

from __future__ import annotations

import functools
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENVIRONMENT_VARIABLES = ("ENV", "ENVIRONMENT", "APP_ENV")
_DEFAULT_CACHE_TTL_SECONDS = 300.0


class SecretNotFoundError(Exception):
    """Raised when a requested secret cannot be resolved in the current environment."""


class SecretBackendError(Exception):
    """Raised when the configured backend is unreachable or misconfigured."""


# ═══════════════════════════════════════════════════════════════════
# Environment detection
# ═══════════════════════════════════════════════════════════════════


def _detect_environment() -> str:
    """Return one of: local, ci, staging, production.

    Reads ``ENV``, then ``ENVIRONMENT`` (what the Kustomize overlays set), then
    ``APP_ENV``. Reading only ``ENV`` made every staging/production pod resolve
    to ``staging`` through the Kubernetes heuristic below, whatever its overlay
    declared.
    """
    raw = next((os.environ[name] for name in _ENVIRONMENT_VARIABLES if os.environ.get(name)), "").lower()
    if raw in {"local", "dev", "development"}:
        return "local"
    if raw in {"ci", "test"}:
        return "ci"
    if raw in {"staging", "stage"}:
        return "staging"
    if raw in {"production", "prod"}:
        return "production"

    # Fallback heuristics
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "ci"
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        # Running in K8s — assume staging unless overridden
        ns = os.environ.get("POD_NAMESPACE", "")
        return "production" if "prod" in ns else "staging"

    return "local"


def _detect_cloud() -> str:
    """Return one of: aws, gcp, unknown.

    Detection order:
    1. Explicit CLOUD_PROVIDER env var (every cloud overlay sets it)
    2. AWS presence (IRSA token path)
    3. GCP presence (metadata server env var)
    """
    explicit = os.environ.get("CLOUD_PROVIDER", "").lower()
    if explicit in {"aws", "gcp"}:
        return explicit
    if os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
        return "aws"
    if os.environ.get("GCE_METADATA_HOST") or Path("/var/run/secrets/tokens").exists():
        return "gcp"
    return "unknown"


def secret_id(key: str, namespace: str | None, cloud: str) -> str:
    """Return the cloud secret id for ``key`` — Terraform's naming scheme (ADR-051).

    GCP joins with ``-`` (Secret Manager ids cannot contain ``/``); AWS joins
    with ``/``. The key is lowercased to match Terraform's ``secret_names``.
    """
    name = key.lower()
    if not namespace:
        return name
    separator = "/" if cloud == "aws" else "-"
    return f"{namespace}{separator}{name}"


# ═══════════════════════════════════════════════════════════════════
# Backends
# ═══════════════════════════════════════════════════════════════════


@functools.lru_cache(maxsize=1)
def _load_dotenv_local() -> dict[str, str]:
    """Parse .env.local from repo root. Returns {} if absent."""
    candidates = [Path.cwd() / ".env.local", Path(__file__).resolve().parent.parent / ".env.local"]
    for path in candidates:
        if path.exists():
            env: dict[str, str] = {}
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
            return env
    return {}


def _get_local(key: str) -> str:
    """Read from .env.local (dev convenience only)."""
    env = _load_dotenv_local()
    if key in env:
        return env[key]
    if key in os.environ:
        # Dev only — explicit os.environ fallback allowed locally
        return os.environ[key]
    raise SecretNotFoundError(f"{key} not in .env.local or os.environ (local mode)")


def _get_ci(key: str) -> str:
    """Read from os.environ (GitHub Secrets → workflow env → os.environ)."""
    if key in os.environ:
        return os.environ[key]
    raise SecretNotFoundError(f"{key} not in CI environment")


def _is_not_found(exc: Exception) -> bool:
    """True when a cloud SDK error means "no such secret" rather than a backend fault."""
    if type(exc).__name__ in {"NotFound", "ResourceNotFoundException"}:
        return True
    response = getattr(exc, "response", None)
    return isinstance(response, dict) and response.get("Error", {}).get("Code") == "ResourceNotFoundException"


@functools.lru_cache(maxsize=1)
def _aws_client() -> Any:
    try:
        import boto3
    except ImportError as e:
        raise SecretBackendError(
            "boto3 is not installed in this image. Build it with --build-arg CLOUD_PROVIDER=aws (ADR-051)."
        ) from e
    return boto3.client("secretsmanager")


def _get_aws(resolved_id: str) -> str:
    """Read from AWS Secrets Manager via IRSA (D-18)."""
    try:
        response = _aws_client().get_secret_value(SecretId=resolved_id)
    except SecretBackendError:
        raise
    except Exception as e:
        if _is_not_found(e):
            raise SecretNotFoundError(f"AWS Secrets Manager has no secret {resolved_id}") from e
        raise SecretBackendError(f"AWS Secrets Manager lookup failed for {resolved_id}: {type(e).__name__}") from e
    return response.get("SecretString", "")


@functools.lru_cache(maxsize=1)
def _gcp_client() -> Any:
    try:
        from google.cloud import secretmanager
    except ImportError as e:
        raise SecretBackendError(
            "google-cloud-secret-manager is not installed in this image. "
            "Build it with --build-arg CLOUD_PROVIDER=gcp (ADR-051)."
        ) from e
    return secretmanager.SecretManagerServiceClient()


def _gcp_project() -> str:
    """``GCP_PROJECT_ID`` if set, else the project Workload Identity reports."""
    project = os.environ.get("GCP_PROJECT_ID")
    if project:
        return project
    try:
        import google.auth

        _, project = google.auth.default()
    except Exception as e:
        raise SecretBackendError(
            f"GCP project not resolvable: set GCP_PROJECT_ID or run under Workload Identity ({type(e).__name__})"
        ) from e
    if not project:
        raise SecretBackendError("GCP project not resolvable: set GCP_PROJECT_ID")
    return str(project)


def _get_gcp(resolved_id: str) -> str:
    """Read from GCP Secret Manager via Workload Identity (D-18)."""
    client = _gcp_client()
    name = f"projects/{_gcp_project()}/secrets/{resolved_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
    except Exception as e:
        if _is_not_found(e):
            raise SecretNotFoundError(f"GCP Secret Manager has no enabled version of {name}") from e
        raise SecretBackendError(f"GCP Secret Manager lookup failed for {name}: {type(e).__name__}") from e
    return str(response.payload.data.decode("utf-8"))


# ═══════════════════════════════════════════════════════════════════
# Cloud lookup cache
# ═══════════════════════════════════════════════════════════════════

_cache: dict[tuple[str, str], tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _cache_ttl_seconds() -> float:
    raw = os.environ.get("SECRETS_CACHE_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_CACHE_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("Ignoring non-numeric SECRETS_CACHE_TTL_SECONDS", extra={"value": raw})
        return _DEFAULT_CACHE_TTL_SECONDS


def clear_cache() -> None:
    """Drop cached cloud lookups (tests, or an operator forcing a re-read after rotation)."""
    with _cache_lock:
        _cache.clear()


def _get_cloud(cloud: str, resolved_id: str) -> str:
    ttl = _cache_ttl_seconds()
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get((cloud, resolved_id))
    if hit and ttl and now - hit[0] < ttl:
        return hit[1]
    value = _get_aws(resolved_id) if cloud == "aws" else _get_gcp(resolved_id)
    if ttl:
        with _cache_lock:
            _cache[(cloud, resolved_id)] = (now, value)
    return value


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════


def get_secret(
    key: str,
    namespace: str | None = None,
    *,
    default: Any = ...,
) -> str:
    """Resolve a secret by key, based on the current environment.

    Args:
        key: Secret name (e.g., 'DB_PASSWORD', 'EXTERNAL_API_KEY').
        namespace: Cloud secret prefix — normally ``SECRETS_PREFIX``. Ignored
            locally and in CI, where ``key`` is read as given.
        default: If provided, return this on miss. If ..., raise SecretNotFoundError.

    Returns:
        The secret value as a string.

    Raises:
        SecretNotFoundError: Secret not present in the configured backend (no default).
        SecretBackendError: Backend is misconfigured or unreachable.

    Invariants:
        - Never logs the secret value (D-17)
        - Never falls through to os.environ in staging/production (D-18)
    """
    env = _detect_environment()
    try:
        if env == "local":
            value = _get_local(key)
        elif env == "ci":
            value = _get_ci(key)
        elif env in {"staging", "production"}:
            cloud = _detect_cloud()
            if cloud not in {"aws", "gcp"}:
                raise SecretBackendError(
                    f"In {env} but CLOUD_PROVIDER not detected. Set CLOUD_PROVIDER=aws|gcp. "
                    "os.environ fallback is disabled in non-local/non-CI environments (D-18)."
                )
            value = _get_cloud(cloud, secret_id(key, namespace, cloud))
        else:
            raise SecretBackendError(f"Unknown environment: {env!r}")
    except SecretNotFoundError:
        if default is not ...:
            return default
        raise

    # NEVER log the value. Only log resolution success with redaction.
    logger.debug("Secret resolved", extra={"key": key, "namespace": namespace, "env": env})
    return value


def require_secret(key: str, namespace: str | None = None) -> str:
    """Strict variant: raises SecretNotFoundError on miss; no default allowed.

    Use this for secrets that MUST exist in prod (DB credentials, API keys).
    """
    return get_secret(key, namespace=namespace)


__all__ = [
    "get_secret",
    "require_secret",
    "secret_id",
    "clear_cache",
    "SecretNotFoundError",
    "SecretBackendError",
]
