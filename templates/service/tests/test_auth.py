"""Tests for API authentication middleware (PR-R2-1, ADR-016).

Covers the four behavioral states of :func:`common_utils.auth.verify_api_key`
and :func:`common_utils.auth.require_admin` plus the CORS default-deny
contract:

* Auth disabled \u2192 inference endpoints pass through unauthenticated.
* Auth enabled, no credential \u2192 401 with ``WWW-Authenticate``.
* Auth enabled, wrong credential \u2192 401 (no oracle).
* Auth enabled, correct credential \u2192 200.
* ``/model/reload`` hidden by default (404).
* ``/model/reload`` rejects wrong admin credential with 404
  (intentionally indistinguishable from "endpoint off").
* CORS unset \u2192 no Access-Control-Allow-Origin header (default-deny).
* CORS set \u2192 only listed origins are echoed back.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> Iterator[TestClient]:
    """Re-export the standard ``client`` with API auth enabled.

    Uses the existing conftest fixture (which mocks out model loading
    and prediction logging) so we exercise only the auth path.
    """
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "local")
    # The local secrets backend reads from os.environ on dev/CI
    monkeypatch.setenv("API_KEY", "test-key-value")
    yield client


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> Iterator[TestClient]:
    monkeypatch.setenv("ADMIN_API_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-key-value")
    yield client


# ---------------------------------------------------------------------------
# verify_api_key
# ---------------------------------------------------------------------------
class TestApiAuthDisabled:
    """Default rollout (API_AUTH_ENABLED unset) preserves backward compat."""

    def test_predict_passthrough_without_credential(self, client: TestClient, valid_payload: dict) -> None:
        # Existing services continue to work; auth is opt-in until overlays
        # flip API_AUTH_ENABLED=true (PR-R2-3).
        response = client.post("/predict", json=valid_payload)
        assert response.status_code == 200


class TestApiAuthEnabled:
    """API_AUTH_ENABLED=true \u2014 the contract that matters in staging/prod."""

    def test_predict_rejects_missing_credential(self, auth_client: TestClient, valid_payload: dict) -> None:
        response = auth_client.post("/predict", json=valid_payload)
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers
        # No leak of the expected key shape
        assert "test-key-value" not in response.text

    def test_predict_rejects_wrong_credential(self, auth_client: TestClient, valid_payload: dict) -> None:
        response = auth_client.post("/predict", json=valid_payload, headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401

    def test_predict_accepts_correct_credential_via_x_api_key(
        self, auth_client: TestClient, valid_payload: dict
    ) -> None:
        response = auth_client.post("/predict", json=valid_payload, headers={"X-API-Key": "test-key-value"})
        assert response.status_code == 200

    def test_predict_accepts_correct_credential_via_bearer(self, auth_client: TestClient, valid_payload: dict) -> None:
        response = auth_client.post("/predict", json=valid_payload, headers={"Authorization": "Bearer test-key-value"})
        assert response.status_code == 200

    def test_predict_rejects_non_bearer_authorization(self, auth_client: TestClient, valid_payload: dict) -> None:
        # "Basic" / "Digest" / arbitrary schemes must not auth.
        response = auth_client.post(
            "/predict",
            json=valid_payload,
            headers={"Authorization": "Basic dGVzdC1rZXktdmFsdWU="},
        )
        assert response.status_code == 401


class TestCloudSecretResolution:
    """Staging/production resolve API_KEY from the cloud secret manager (ADR-051)."""

    @pytest.fixture
    def cloud_client(self, monkeypatch: pytest.MonkeyPatch, client: TestClient) -> Iterator[TestClient]:
        import common_utils.secrets as secrets

        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        secrets.clear_cache()
        yield client
        secrets.clear_cache()

    def test_prefix_addresses_the_terraform_secret(
        self, monkeypatch: pytest.MonkeyPatch, cloud_client: TestClient, valid_payload: dict
    ) -> None:
        import common_utils.secrets as secrets

        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        monkeypatch.setenv("SECRETS_PREFIX", "acme/fraud-detector")
        requested: list[str] = []

        def _fake_aws(resolved_id: str) -> str:
            requested.append(resolved_id)
            return "cloud-key"

        monkeypatch.setattr(secrets, "_get_aws", _fake_aws)
        ok = cloud_client.post("/predict", json=valid_payload, headers={"X-API-Key": "cloud-key"})
        # The deploy smoke probe's contract: a resolvable secret rejects a wrong key with 401.
        wrong = cloud_client.post("/predict", json=valid_payload, headers={"X-API-Key": "deploy-smoke"})
        assert (ok.status_code, wrong.status_code) == (200, 401)
        assert set(requested) == {"acme/fraud-detector/api_key"}

    def test_backend_fault_fails_closed_with_503(
        self, monkeypatch: pytest.MonkeyPatch, cloud_client: TestClient, valid_payload: dict
    ) -> None:
        import common_utils.secrets as secrets

        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        monkeypatch.setenv("SECRETS_PREFIX", "acme-fraud-detector")

        def _unbound_identity(resolved_id: str) -> str:
            raise secrets.SecretBackendError(f"PermissionDenied reading {resolved_id}")

        monkeypatch.setattr(secrets, "_get_gcp", _unbound_identity)
        response = cloud_client.post("/predict", json=valid_payload, headers={"X-API-Key": "anything"})
        assert response.status_code == 503
        assert "PermissionDenied" not in response.text


# ---------------------------------------------------------------------------
# require_admin
# ---------------------------------------------------------------------------
class TestAdminEndpointHidden:
    """Default: /model/reload is invisible (returns 404, never 405/401)."""

    def test_reload_returns_404_when_admin_disabled(self, client: TestClient) -> None:
        response = client.post("/model/reload")
        assert response.status_code == 404

    def test_reload_404_with_credential_when_admin_disabled(self, client: TestClient) -> None:
        # Even a correctly-formatted credential must not reveal the endpoint
        response = client.post("/model/reload", headers={"X-API-Key": "anything"})
        assert response.status_code == 404


class TestAdminEndpointEnabled:
    def test_reload_404_without_credential(self, admin_client: TestClient) -> None:
        # Still 404 (not 401) so admin surface stays invisible.
        response = admin_client.post("/model/reload")
        assert response.status_code == 404

    def test_reload_404_with_wrong_credential(self, admin_client: TestClient) -> None:
        response = admin_client.post("/model/reload", headers={"X-API-Key": "wrong"})
        assert response.status_code == 404

    def test_reload_200_with_correct_credential(self, admin_client: TestClient) -> None:
        response = admin_client.post("/model/reload", headers={"X-API-Key": "admin-key-value"})
        assert response.status_code == 200
        assert response.json()["status"] == "reloaded"


# ---------------------------------------------------------------------------
# CORS default-deny
# ---------------------------------------------------------------------------
class TestCorsDefaultDeny:
    """Wildcard "*" must not be the implicit default."""

    def test_no_cors_header_when_unset(self, client: TestClient) -> None:
        # Without CORS_ORIGINS set, no CORSMiddleware is mounted, so no
        # Access-Control-Allow-Origin header is emitted regardless of
        # the request's Origin.
        response = client.get("/health", headers={"Origin": "https://attacker.example.com"})
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


# ---------------------------------------------------------------------------
# Error response sanitization (D-32, audit R2 finding)
# ---------------------------------------------------------------------------
class TestErrorSanitization:
    def test_predict_error_does_not_leak_exception_message(
        self, client: TestClient, valid_payload: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the internal predict to raise a recognizable exception
        from app import fastapi_app

        sentinel = "SENSITIVE_INTERNAL_DETAIL_xyz123"

        def _boom(*_args, **_kwargs):
            raise RuntimeError(sentinel)

        monkeypatch.setattr(fastapi_app, "_sync_predict", _boom)
        response = client.post("/predict", json=valid_payload)
        assert response.status_code == 500
        # The body must NOT contain the raw exception message
        assert sentinel not in response.text
        # Generic message instead
        assert "Internal prediction error" in response.text
