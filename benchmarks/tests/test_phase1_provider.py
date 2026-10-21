"""Provider-interface tests (no network, no inference API calls)."""

from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.provider import (  # noqa: E402
    DYNAMIC_MODEL_ALIASES,
    LiveProvider,
    MockProvider,
    ProviderError,
    ProviderResult,
    redact_secrets,
)
from model_dispatcher import (  # noqa: E402
    LiveModeError,
    ModelConfigurationError,
    ModelDispatcher,
    QuotaExceededError,
)


class TestMissingProviderConfiguration:

    def test_missing_credential_fails_closed(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: None))
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert excinfo.value.category == "configuration_error"

    def test_missing_provider_name_fails_closed(self):
        provider = LiveProvider(provider="", model_id="vendor/model")
        with pytest.raises(ProviderError):
            provider.preflight()

    def test_missing_model_id_fails_closed(self):
        provider = LiveProvider(provider="openrouter", model_id="")
        with pytest.raises(ProviderError):
            provider.preflight()

    def test_dynamic_model_alias_is_refused_by_default(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))
        provider = LiveProvider(provider="openrouter", model_id="openrouter/free")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert "dynamic alias" in str(excinfo.value)

    def test_dynamic_model_alias_can_be_opted_into(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))
        provider = LiveProvider(
            provider="openrouter",
            model_id="openrouter/free",
            allow_dynamic_alias=True,
        )
        provider.preflight()  # must not raise

    def test_dynamic_alias_list_is_non_empty(self):
        assert "openrouter/free" in DYNAMIC_MODEL_ALIASES


class TestNoMockFallbackInLiveMode:

    def test_generate_live_refuses_force_mock(self):
        dispatcher = ModelDispatcher(force_mock=True)
        with pytest.raises(ModelConfigurationError):
            dispatcher.generate_live(prompt="x", model_name="vendor/model")

    def test_generate_live_raises_when_credential_missing(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: None))
        dispatcher = ModelDispatcher(force_mock=False, strict_live=True)
        with pytest.raises(ModelConfigurationError):
            dispatcher.generate_live(prompt="x", model_name="vendor/model")

    def test_live_provider_does_not_substitute_a_mock_response(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))

        class FailingDispatcher:
            def generate_live(self, **kwargs):
                raise LiveModeError("upstream exploded", category="provider_error", status_code=500)

        provider = LiveProvider(
            provider="openrouter",
            model_id="vendor/model",
            dispatcher=FailingDispatcher(),
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "provider_error"
        assert "mock" not in str(excinfo.value).lower()

    def test_mock_provider_is_flagged_as_mock(self):
        result = MockProvider().complete("prompt")
        assert result.is_mock is True
        assert result.provider == "deterministic-mock"


class TestNoApiCallFromUnitTests:

    def test_outbound_connections_are_blocked(self):
        import socket

        with pytest.raises(OSError):
            socket.create_connection(("example.invalid", 80), timeout=1)

    def test_http_client_requests_fail_without_network(self):
        import httpx

        with pytest.raises(Exception):
            httpx.get("https://example.invalid/", timeout=1)

    def test_mock_provider_performs_no_io(self):
        provider = MockProvider()
        result = provider.complete("some prompt")
        assert isinstance(result, ProviderResult)


class TestProviderErrorTaxonomy:

    def _provider_raising(self, exc):
        class Dispatcher:
            def generate_live(self, **kwargs):
                raise exc

        return Dispatcher()

    def test_quota_error_is_categorised(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))
        provider = LiveProvider(
            provider="openrouter",
            model_id="vendor/model",
            dispatcher=self._provider_raising(
                QuotaExceededError("daily cap reached", status_code=429)
            ),
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "quota_exceeded"
        assert excinfo.value.status_code == 429

    def test_auth_error_is_categorised(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))
        provider = LiveProvider(
            provider="openrouter",
            model_id="vendor/model",
            dispatcher=self._provider_raising(
                LiveModeError("bad key", category="auth_error", status_code=401)
            ),
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "auth_error"

    def test_timeout_is_categorised(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))
        provider = LiveProvider(
            provider="openrouter",
            model_id="vendor/model",
            dispatcher=self._provider_raising(
                LiveModeError("timed out", category="timeout")
            ),
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "timeout"

    def test_network_error_is_categorised(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))
        provider = LiveProvider(
            provider="openrouter",
            model_id="vendor/model",
            dispatcher=self._provider_raising(
                LiveModeError("dns failure", category="network_error")
            ),
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "network_error"


class TestUsageAndCostHonesty:

    def test_mock_usage_is_not_reported_as_provider_usage(self):
        result = MockProvider().complete("prompt")
        assert result.prompt_tokens is None
        assert result.completion_tokens is None
        assert result.total_tokens is None
        assert result.cost_usd is None
        assert result.usage_source != "provider_reported"

    def test_missing_usage_stays_null(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))

        class Dispatcher:
            def generate_live(self, **kwargs):
                from model_dispatcher import ModelResponse

                return ModelResponse(
                    content="print('hi')",
                    raw_tokens_in=0,
                    cached_tokens_in=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                    model_name="vendor/model",
                    provider="OpenRouter",
                    latency_ms=1.0,
                    is_mock=False,
                    usage_source="local_estimate",
                    finish_reason="stop",
                    served_model_id="vendor/model-20260101",
                )

        provider = LiveProvider(
            provider="openrouter", model_id="vendor/model", dispatcher=Dispatcher()
        )
        result = provider.complete("prompt")
        assert result.prompt_tokens is None
        assert result.completion_tokens is None
        assert result.total_tokens is None
        assert result.cost_usd is None
        assert result.served_model_id == "vendor/model-20260101"
        assert result.finish_reason == "stop"

    def test_provider_reported_usage_is_kept(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test"))

        class Dispatcher:
            def generate_live(self, **kwargs):
                from model_dispatcher import ModelResponse

                return ModelResponse(
                    content="ok",
                    raw_tokens_in=120,
                    cached_tokens_in=0,
                    output_tokens=30,
                    reasoning_tokens=0,
                    model_name="vendor/model",
                    provider="OpenRouter",
                    latency_ms=1.0,
                    is_mock=False,
                    usage_source="provider_reported",
                    finish_reason="stop",
                    served_model_id="vendor/model",
                    retry_count=1,
                    cost_usd=0.0001,
                    cost_source="provider_reported",
                    provider_prompt_tokens=120,
                    provider_completion_tokens=30,
                    provider_total_tokens=150,
                )

        provider = LiveProvider(
            provider="openrouter", model_id="vendor/model", dispatcher=Dispatcher()
        )
        result = provider.complete("prompt")
        assert result.prompt_tokens == 120
        assert result.completion_tokens == 30
        assert result.total_tokens == 150
        assert result.cost_usd == 0.0001
        assert result.retry_count == 1


class TestSecretRedaction:

    def test_bearer_token_is_redacted(self):
        assert "abcdef123456" not in redact_secrets("Authorization: Bearer abcdef123456")

    def test_openai_style_key_is_redacted(self):
        assert "sk-abcdef123456" not in redact_secrets("key=sk-abcdef123456")

    def test_api_key_assignment_is_redacted(self):
        redacted = redact_secrets("api_key='supersecretvalue'")
        assert "supersecretvalue" not in redacted

    def test_redaction_keeps_non_secret_text(self):
        assert redact_secrets("connection refused") == "connection refused"


class TestTruncatedOutput:

    def test_truncated_code_is_reported_as_a_parse_problem(self):
        from benchmarks.benchmark.adapters.full_file import FullFileRegenerationAdapter

        adapter = FullFileRegenerationAdapter()
        truncated = "```python\ndef f():\n    return 1\n"  # no closing fence
        result = adapter.parse_and_apply(_FakeTask(), 1, truncated)
        assert result.applied is False
        assert result.failure_reason in ("no_code_block", "syntax_error")


class _FakeTask:
    task_id = "FAKE-001"
    target_function = "f"
    mutated_source = "def f():\n    return 1\n"
    official_test = ""
