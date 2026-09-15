"""Unit tests for ``common_utils.secrets``.

Closes external-feedback gap 5.1 (May 2026): the secrets resolver
enforces invariants D-17 (never log values) and D-18 (no os.environ
fallback in staging/production) but had ZERO unit-test coverage.
This file exercises every resolution branch + the negative cases
that prove the invariants hold.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import pytest

import common_utils.secrets as secrets
from common_utils.secrets import (
    SecretBackendError,
    SecretNotFoundError,
    _detect_cloud,
    _detect_environment,
    _load_dotenv_local,
    clear_cache,
    get_secret,
    secret_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test starts AND ends with a clean environment and cleared caches.

    Clearing only on entry leaked the last test's parsed ``.env.local`` into
    whatever ran next in the session: ``test_auth.py`` then compared a request
    against this file's sentinel instead of its own key and got 401 — but only
    when this module happened to be collected first.
    """
    for var in (
        "ENV",
        "ENVIRONMENT",
        "APP_ENV",
        "SECRETS_CACHE_TTL_SECONDS",
        "CLOUD_PROVIDER",
        "GITHUB_ACTIONS",
        "KUBERNETES_SERVICE_HOST",
        "POD_NAMESPACE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "GCE_METADATA_HOST",
        "GCP_PROJECT_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    _load_dotenv_local.cache_clear()
    clear_cache()
    yield
    _load_dotenv_local.cache_clear()
    clear_cache()


# ---------------------------------------------------------------------------
# _detect_environment
# ---------------------------------------------------------------------------
class TestDetectEnvironment:
    @pytest.mark.parametrize(
        "env_value,expected",
        [
            ("local", "local"),
            ("dev", "local"),
            ("development", "local"),
            ("ci", "ci"),
            ("test", "ci"),
            ("staging", "staging"),
            ("stage", "staging"),
            ("production", "production"),
            ("prod", "production"),
        ],
    )
    def test_explicit_env_var(self, monkeypatch: pytest.MonkeyPatch, env_value: str, expected: str) -> None:
        monkeypatch.setenv("ENV", env_value)
        assert _detect_environment() == expected

    @pytest.mark.parametrize("variable", ["ENVIRONMENT", "APP_ENV"])
    def test_overlay_variable_names_are_honoured(self, monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
        """Every overlay sets ENVIRONMENT; reading only ENV sent prod pods down the heuristic."""
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
        monkeypatch.setenv(variable, "production")
        assert _detect_environment() == "production"

    def test_env_takes_precedence_over_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "ci")
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert _detect_environment() == "ci"

    def test_github_actions_heuristic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert _detect_environment() == "ci"

    def test_kubernetes_namespace_with_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
        monkeypatch.setenv("POD_NAMESPACE", "fraud-prod")
        assert _detect_environment() == "production"

    def test_kubernetes_namespace_without_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
        monkeypatch.setenv("POD_NAMESPACE", "fraud-staging")
        assert _detect_environment() == "staging"

    def test_default_is_local(self) -> None:
        assert _detect_environment() == "local"


# ---------------------------------------------------------------------------
# _detect_cloud
# ---------------------------------------------------------------------------
class TestDetectCloud:
    @pytest.mark.parametrize("explicit", ["aws", "AWS", "gcp", "GCP"])
    def test_explicit_provider_wins(self, monkeypatch: pytest.MonkeyPatch, explicit: str) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", explicit)
        assert _detect_cloud() == explicit.lower()

    def test_aws_via_irsa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", "/var/run/secrets/eks.amazonaws.com/serviceaccount/token")
        assert _detect_cloud() == "aws"

    def test_gcp_via_metadata_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCE_METADATA_HOST", "metadata.google.internal")
        assert _detect_cloud() == "gcp"

    def test_unknown_when_no_signal(self) -> None:
        assert _detect_cloud() == "unknown"


# ---------------------------------------------------------------------------
# .env.local + local backend
# ---------------------------------------------------------------------------
class TestLocalBackend:
    def test_dotenv_parsing_strips_quotes_and_whitespace(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        env_file = tmp_path / ".env.local"
        env_file.write_text(
            "API_KEY = \"sk_test_123\"\nDB_PASSWORD = 'pw'\nBARE_VAL=plain\n# COMMENT=ignored\nMALFORMED_NO_EQUALS\n"
        )
        monkeypatch.chdir(tmp_path)
        _load_dotenv_local.cache_clear()
        assert _load_dotenv_local() == {
            "API_KEY": "sk_test_123",
            "DB_PASSWORD": "pw",
            "BARE_VAL": "plain",
        }

    def test_local_resolves_from_dotenv(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        (tmp_path / ".env.local").write_text("API_KEY=local_value\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ENV", "local")
        _load_dotenv_local.cache_clear()
        assert get_secret("API_KEY") == "local_value"

    def test_local_falls_back_to_os_environ(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ENV", "local")
        monkeypatch.setenv("API_KEY", "from_environ")
        _load_dotenv_local.cache_clear()
        assert get_secret("API_KEY") == "from_environ"

    def test_local_miss_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ENV", "local")
        _load_dotenv_local.cache_clear()
        with pytest.raises(SecretNotFoundError):
            get_secret("UNKNOWN_KEY")

    def test_local_miss_with_default_returns_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ENV", "local")
        _load_dotenv_local.cache_clear()
        assert get_secret("MISSING", default="fallback") == "fallback"


# ---------------------------------------------------------------------------
# CI backend
# ---------------------------------------------------------------------------
class TestCIBackend:
    def test_ci_resolves_from_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "ci")
        monkeypatch.setenv("API_KEY", "ci_value")
        assert get_secret("API_KEY") == "ci_value"

    def test_ci_miss_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "ci")
        with pytest.raises(SecretNotFoundError):
            get_secret("MISSING")


# ---------------------------------------------------------------------------
# Staging / production — D-18 invariant
# ---------------------------------------------------------------------------
class TestProductionInvariants:
    """The D-18 invariant: staging/production NEVER fall through to os.environ.

    These tests are the canonical guarantees the secrets module ships.
    """

    @pytest.mark.parametrize("env_label", ["staging", "production"])
    def test_unknown_cloud_in_non_local_raises(self, monkeypatch: pytest.MonkeyPatch, env_label: str) -> None:
        monkeypatch.setenv("ENV", env_label)
        # No CLOUD_PROVIDER, no IRSA, no GCP metadata.
        # Even if API_KEY is in os.environ, get_secret MUST refuse it.
        monkeypatch.setenv("API_KEY", "this_must_not_be_returned")
        with pytest.raises(SecretBackendError, match="CLOUD_PROVIDER not detected"):
            get_secret("API_KEY")

    @pytest.mark.parametrize(
        ("cloud", "expected_id"),
        [("aws", "acme/fraud-detector/api_key"), ("gcp", "acme-fraud-detector-api_key")],
    )
    def test_cloud_backend_receives_terraform_secret_id(
        self, monkeypatch: pytest.MonkeyPatch, cloud: str, expected_id: str
    ) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("CLOUD_PROVIDER", cloud)
        prefix = "acme/fraud-detector" if cloud == "aws" else "acme-fraud-detector"
        called: list[str] = []

        def _fake(resolved_id: str) -> str:
            called.append(resolved_id)
            return f"from_{cloud}"

        monkeypatch.setattr(secrets, f"_get_{cloud}", _fake)
        assert get_secret("API_KEY", namespace=prefix) == f"from_{cloud}"
        assert called == [expected_id]

    def test_default_returned_on_miss_in_staging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "staging")
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        monkeypatch.setattr(secrets, "_get_aws", lambda *a, **kw: (_ for _ in ()).throw(SecretNotFoundError("miss")))
        assert get_secret("MISSING", default="fallback") == "fallback"


# ---------------------------------------------------------------------------
# D-17 invariant — value never logged
# ---------------------------------------------------------------------------
class TestD17NeverLogValue:
    """D-17: the secret value MUST NOT appear in any log record."""

    def test_value_never_in_log_messages(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sentinel = "TOPSECRET-SHOULD-NEVER-LEAK-9b3a7f"
        (tmp_path / ".env.local").write_text(f"API_KEY={sentinel}\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ENV", "local")
        _load_dotenv_local.cache_clear()

        with caplog.at_level(logging.DEBUG, logger="common_utils.secrets"):
            value = get_secret("API_KEY")

        assert value == sentinel
        # The sentinel must not appear in ANY captured record's
        # message, args, OR the structured `extra` payload.
        for record in caplog.records:
            assert sentinel not in record.getMessage()
            for arg in record.args or ():
                assert sentinel not in str(arg)
            # `extra` is merged into __dict__; check there too.
            for key, val in record.__dict__.items():
                if key in {"args", "msg", "message"}:
                    continue
                assert sentinel not in str(val), f"Secret leaked into log record field {key!r}: {val!r}"


# ---------------------------------------------------------------------------
# ADR-051 — secret addressing, not-found mapping, caching
# ---------------------------------------------------------------------------
class TestSecretId:
    @pytest.mark.parametrize(
        ("key", "namespace", "cloud", "expected"),
        [
            ("API_KEY", "acme-svc", "gcp", "acme-svc-api_key"),
            ("API_KEY", "acme/svc", "aws", "acme/svc/api_key"),
            ("ADMIN_API_KEY", None, "gcp", "admin_api_key"),
        ],
    )
    def test_scheme(self, key: str, namespace: str | None, cloud: str, expected: str) -> None:
        assert secret_id(key, namespace, cloud) == expected


class _FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class NotFound(Exception):
    """Same class name as google.api_core.exceptions.NotFound."""


class TestNotFoundMapping:
    def test_aws_resource_not_found_is_a_miss_not_a_backend_fault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Client:
            def get_secret_value(self, SecretId: str) -> dict:
                raise _FakeClientError("ResourceNotFoundException")

        monkeypatch.setattr(secrets, "_aws_client", lambda: _Client())
        with pytest.raises(SecretNotFoundError):
            secrets._get_aws("acme/svc/api_key")

    def test_aws_access_denied_stays_a_backend_fault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Client:
            def get_secret_value(self, SecretId: str) -> dict:
                raise _FakeClientError("AccessDeniedException")

        monkeypatch.setattr(secrets, "_aws_client", lambda: _Client())
        with pytest.raises(SecretBackendError, match="acme/svc/api_key"):
            secrets._get_aws("acme/svc/api_key")

    def test_gcp_not_found_is_a_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Client:
            def access_secret_version(self, request: dict) -> None:
                raise NotFound("gone")

        monkeypatch.setenv("GCP_PROJECT_ID", "proj")
        monkeypatch.setattr(secrets, "_gcp_client", lambda: _Client())
        with pytest.raises(SecretNotFoundError):
            secrets._get_gcp("acme-svc-api_key")

    def test_gcp_project_unresolvable_is_a_backend_fault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(secrets, "_gcp_client", lambda: object())
        import builtins

        real_import = builtins.__import__

        def _no_google_auth(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("google.auth") or name == "google":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_google_auth)
        with pytest.raises(SecretBackendError, match="GCP project not resolvable"):
            secrets._get_gcp("acme-svc-api_key")


class TestCloudCache:
    def _count_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        calls: list[str] = []

        def _fake(resolved_id: str) -> str:
            calls.append(resolved_id)
            return f"value-{len(calls)}"

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        monkeypatch.setattr(secrets, "_get_gcp", _fake)
        return calls

    def test_repeat_lookups_within_ttl_hit_the_backend_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._count_calls(monkeypatch)
        assert get_secret("API_KEY", namespace="p-s") == get_secret("API_KEY", namespace="p-s") == "value-1"
        assert calls == ["p-s-api_key"]

    def test_expired_entry_is_refetched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._count_calls(monkeypatch)
        clock = iter([100.0, 100.0 + 301.0])
        monkeypatch.setattr(secrets.time, "monotonic", lambda: next(clock))
        get_secret("API_KEY", namespace="p-s")
        assert get_secret("API_KEY", namespace="p-s") == "value-2"
        assert len(calls) == 2

    def test_ttl_zero_disables_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._count_calls(monkeypatch)
        monkeypatch.setenv("SECRETS_CACHE_TTL_SECONDS", "0")
        get_secret("API_KEY", namespace="p-s")
        get_secret("API_KEY", namespace="p-s")
        assert len(calls) == 2

    def test_a_miss_is_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        outcomes = iter([SecretNotFoundError("not yet"), "provisioned"])

        def _fake(resolved_id: str) -> str:
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(secrets, "_get_aws", _fake)
        assert get_secret("API_KEY", namespace="p/s", default=None) is None
        assert get_secret("API_KEY", namespace="p/s") == "provisioned"
