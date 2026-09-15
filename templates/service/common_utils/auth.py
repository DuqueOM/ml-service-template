"""API authentication dependencies for FastAPI services.

Provides two FastAPI ``Depends()`` providers used by ``app/main.py``
and ``app/fastapi_app.py``:

* :func:`verify_api_key` — baseline request-time auth for inference
  endpoints (``/predict``, ``/predict_batch``). Reads
  ``X-API-Key`` header (preferred) or ``Authorization: Bearer <token>``;
  compares with constant-time digest against the secret resolved by
  :func:`common_utils.secrets.get_secret` (key ``API_KEY``).
* :func:`require_admin` — strict provider for administrative
  endpoints (``/model/reload``, future hot-reconfig hooks). Uses a
  separate ``ADMIN_API_KEY`` secret AND requires
  ``ADMIN_API_ENABLED=true``. Refuses to construct in
  staging/production with ``ADMIN_API_ENABLED=true`` and no
  admin secret configured.

Default rollout (PR-R2-1, ADR-016):

* ``API_AUTH_ENABLED`` defaults to ``false`` for backwards
  compatibility with existing scaffolded services. The Kustomize
  ``*-prod`` and ``*-staging`` overlays will flip it to ``true`` in
  PR-R2-3 (neutralize K8s base manifests).
* ``ADMIN_API_ENABLED`` defaults to ``false`` — ``/model/reload`` is
  HIDDEN entirely (returns 404, never 405) unless explicitly opted in.

Security invariants (D-17, D-18, D-32):

* Never log the credential (only its presence/absence + key id).
* Never compare with ``==`` (timing oracle); always
  :func:`secrets.compare_digest`.
* Never fall back to a raw environment-variable read for the API
  credential in staging/production — always resolve via
  :mod:`common_utils.secrets` (which itself enforces D-17/D-18).
"""

from __future__ import annotations

import logging
import os
import secrets as _secrets
from typing import Optional

from fastapi import Header, HTTPException, status

from common_utils.secrets import SecretBackendError, SecretNotFoundError, get_secret
from common_utils.secrets import _detect_environment as _secrets_detect_environment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _extract_token(api_key_header: str | None, authorization_header: str | None) -> str | None:
    """Return the presented credential or ``None`` if no header is present."""
    if api_key_header:
        return api_key_header.strip()
    if authorization_header:
        # Accept "Bearer <token>" (RFC 6750). Reject any other scheme.
        parts = authorization_header.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return None


def _detect_environment() -> str:
    """The environment :mod:`common_utils.secrets` resolves against.

    Delegates rather than mirroring: two detectors that disagreed are how an
    overlay setting ``ENVIRONMENT=production`` produced a pod whose auth layer
    said "production" while its secret loader said "staging".
    """
    return _secrets_detect_environment()


def _secret_namespace() -> str | None:
    """Cloud secret prefix set by the overlay (``SECRETS_PREFIX``, ADR-051)."""
    return os.getenv("SECRETS_PREFIX") or None


def _resolve_secret(key: str, *, namespace: str | None) -> str | None:
    """Best-effort secret lookup. Returns ``None`` if the secret is unset.

    A backend fault (SDK missing, identity not bound, project unresolvable)
    also returns ``None`` after logging its cause, so callers fail CLOSED with
    a 503 in staging/production instead of surfacing an unhandled 500 — the
    caller's missing-secret branch already owns that decision.
    """
    try:
        return get_secret(key, namespace=namespace, default=None)
    except SecretNotFoundError:
        return None
    except SecretBackendError as exc:
        # The message names the secret id and the error type, never a value.
        logger.error("Secret backend failed resolving %s: %s", key, exc)
        return None


# ---------------------------------------------------------------------------
# Public dependencies
# ---------------------------------------------------------------------------
def verify_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> str:
    """FastAPI dependency: enforce API key auth on inference endpoints.

    Returns the validated credential's identity (a stable hash prefix) so
    handlers can attach it to structured logs / Prometheus labels without
    leaking the value itself.

    Behavior matrix:

    +-------------------------+-----------------------+------------------+
    | API_AUTH_ENABLED        | Credential present?   | Outcome          |
    +-------------------------+-----------------------+------------------+
    | unset / false           | irrelevant            | pass-through     |
    | true                    | no                    | 401 Unauthorized |
    | true                    | yes, mismatch         | 401 Unauthorized |
    | true                    | yes, match            | OK               |
    +-------------------------+-----------------------+------------------+
    """
    if not _is_truthy(os.getenv("API_AUTH_ENABLED")):
        return "anonymous"

    presented = _extract_token(x_api_key, authorization)
    if not presented:
        # Distinct response from "wrong key" so legitimate callers get a
        # clear message; both share the same status code so we don't leak
        # a verification oracle to attackers.
        logger.warning("API request missing credential", extra={"endpoint_auth": "missing"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API credential. Provide X-API-Key header or Authorization: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    namespace = _secret_namespace()
    expected = _resolve_secret("API_KEY", namespace=namespace)
    if not expected:
        # Misconfiguration: auth is on but no key is provisioned.
        # Fail closed in staging/prod, fail open in local/CI with a loud
        # warning so dev iteration isn't blocked.
        env = _detect_environment()
        if env in {"staging", "production"}:
            logger.error(
                "API_AUTH_ENABLED=true but API_KEY is not resolvable in %s (SECRETS_PREFIX=%r)", env, namespace
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service misconfigured.",
            )
        logger.warning("API_AUTH_ENABLED=true but API_KEY missing (env=%s) — allowing", env)
        return "dev-noauth"

    if not _secrets.compare_digest(presented, expected):
        logger.warning("API request rejected: credential mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Identity = first 8 chars of SHA-256 of the credential. Stable, opaque,
    # safe to log (one-way, no timing oracle since hashing is constant work).
    import hashlib

    identity = hashlib.sha256(presented.encode("utf-8")).hexdigest()[:8]
    return f"key:{identity}"


def require_admin(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> str:
    """FastAPI dependency: enforce ADMIN credential on admin endpoints.

    Refuses to authorize — always 404 — unless ``ADMIN_API_ENABLED=true``.
    The 404 (not 401) is intentional: a hidden admin endpoint should not
    advertise its existence to unauthenticated probes.

    When enabled, validates against the separate ``ADMIN_API_KEY`` secret.
    """
    if not _is_truthy(os.getenv("ADMIN_API_ENABLED")):
        # Hidden endpoint: pretend it doesn't exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    namespace = _secret_namespace()
    expected = _resolve_secret("ADMIN_API_KEY", namespace=namespace)
    env = _detect_environment()
    if not expected:
        if env in {"staging", "production"}:
            # In prod we refuse to operate at all if admin is on without a key.
            logger.error("ADMIN_API_ENABLED=true but ADMIN_API_KEY missing in %s — refusing", env)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Admin endpoint misconfigured.",
            )
        # local/CI: still require a credential so tests cover the real path.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    presented = _extract_token(x_api_key, authorization)
    if not presented or not _secrets.compare_digest(presented, expected):
        # 404 again — don't differentiate "wrong key" from "endpoint
        # off"; admin surface stays invisible to unauthorized callers.
        logger.warning("Admin request rejected", extra={"reason": "missing-or-mismatch"})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    import hashlib

    identity = hashlib.sha256(presented.encode("utf-8")).hexdigest()[:8]
    logger.info("Admin operation authorized (identity=admin:%s)", identity)
    return f"admin:{identity}"


__all__ = ["verify_api_key", "require_admin"]
