"""
Unit tests for OpenRouter provider support in the model dispatcher.

These tests are fully offline: every network path is replaced by an injected
payload or a stubbed HTTP client. No test contacts OpenRouter, OpenAI, or any
other host.
"""

import os
import unittest
from unittest import mock

import httpx

from config import AppConfig
from model_dispatcher import (
    ChatSupportResult,
    ModelConfigurationError,
    ModelDispatcher,
    QuotaExceededError,
    verify_openrouter_chat_support,
)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Minimal but realistic slice of the OpenRouter /models catalog.
FAKE_CATALOG = {
    "data": [
        {
            "id": "openrouter/free",
            "name": "Free Models Router",
            "supported_parameters": ["max_tokens", "temperature", "top_p", "tools"],
            "endpoints": None,
        },
        {
            "id": "meta-llama/llama-3.3-70b-instruct:free",
            "name": "Meta: Llama 3.3 70B Instruct (free)",
            "supported_parameters": ["max_tokens", "temperature", "top_p"],
            "endpoints": None,
        },
        {
            "id": "some/embeddings-only-model",
            "name": "Embedding Only Model",
            "supported_parameters": ["dimensions", "input"],
            "endpoints": None,
        },
    ]
}


class _StubResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://example.invalid"),
                response=httpx.Response(self.status_code),
            )


class _StubClient:
    """Minimal httpx.Client stand-in that never touches the network."""

    calls = []
    response_payload = {}
    status_code = 200
    raise_on_post = False
    response_headers = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, url, json=None, headers=None, **kwargs):
        type(self).calls.append({
            "url": url,
            "json": json,
            "headers": headers or {},
        })
        if type(self).raise_on_post:
            raise httpx.ConnectError("simulated network failure")
        return _StubResponse(
            type(self).response_payload,
            type(self).status_code,
            dict(type(self).response_headers),
        )

    def get(self, url, headers=None, **kwargs):
        type(self).calls.append({
            "url": url,
            "json": None,
            "headers": headers or {},
        })
        return _StubResponse(FAKE_CATALOG, 200)


class _StopRun(Exception):
    """Sentinel used to abort a benchmark run once construction is observed."""


class _EnvGuard:
    """Context manager that temporarily replaces environment variables."""

    def __init__(self, **values):
        self.values = values
        self._saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self._saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc_info):
        for key, original in self._saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        return False


class TestOpenRouterProviderSelection(unittest.TestCase):
    """Provider resolution for explicit OpenRouter model IDs."""

    def test_explicit_openrouter_ids_resolve_to_openrouter(self):
        for model_id in (
            "openrouter/free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-coder:free",
            "mistralai/mistral-small-3.2-24b-instruct",
        ):
            with self.subTest(model=model_id):
                self.assertEqual(ModelDispatcher._resolve_provider(model_id), "openrouter")

    def test_legacy_providers_are_unchanged(self):
        self.assertEqual(ModelDispatcher._resolve_provider("Gemini 3.5 Flash"), "gemini")
        self.assertEqual(ModelDispatcher._resolve_provider("Claude Sonnet 5.5"), "anthropic")
        self.assertEqual(ModelDispatcher._resolve_provider("Claude Sonnet 5.5 / Fable"), "anthropic")
        self.assertEqual(ModelDispatcher._resolve_provider("GPT-5.6 Astra"), "openai")
        self.assertEqual(ModelDispatcher._resolve_provider("GPT-4o-mini"), "openai")
        self.assertEqual(ModelDispatcher._resolve_provider("DeepSeek V4"), "deepseek")

    def test_configured_providers_include_openrouter(self):
        with _EnvGuard(OPENROUTER_API_KEY="or-test-key-1234", OPENAI_API_KEY="sk-openai-test-key"):
            providers = AppConfig.get_configured_providers()
            self.assertIn("OpenRouter", providers)
            self.assertTrue(providers["OpenRouter"])


class TestOpenRouterBaseUrlResolution(unittest.TestCase):
    """Base URL selection for the OpenAI-compatible client."""

    def test_openrouter_provider_uses_openrouter_endpoint(self):
        with _EnvGuard(OPENAI_BASE_URL=None, OPENROUTER_BASE_URL=None):
            self.assertEqual(AppConfig.resolve_base_url("openrouter"), OPENROUTER_BASE)

    def test_explicit_openrouter_base_url_override(self):
        with _EnvGuard(OPENAI_BASE_URL=None, OPENROUTER_BASE_URL="https://gateway.example.test/v1"):
            self.assertEqual(AppConfig.resolve_base_url("openrouter"), "https://gateway.example.test/v1")

    def test_openai_base_url_pointing_at_openrouter_is_honoured(self):
        with _EnvGuard(OPENAI_BASE_URL=OPENROUTER_BASE, OPENROUTER_BASE_URL=None):
            self.assertEqual(AppConfig.resolve_base_url("openrouter"), OPENROUTER_BASE)
            self.assertEqual(AppConfig.resolve_base_url("openai"), OPENROUTER_BASE)

    def test_openai_default_endpoint_unchanged(self):
        with _EnvGuard(OPENAI_BASE_URL=None):
            self.assertEqual(AppConfig.resolve_base_url("openai"), "https://api.openai.com/v1")

    def test_openrouter_key_is_preferred_for_openrouter_gateway(self):
        with _EnvGuard(
            OPENAI_BASE_URL=OPENROUTER_BASE,
            OPENROUTER_BASE_URL=None,
            OPENAI_API_KEY="sk-openai-test-key",
            OPENROUTER_API_KEY="or-openrouter-test-key",
        ):
            self.assertEqual(AppConfig.resolve_api_key("openai"), "or-openrouter-test-key")
            self.assertEqual(AppConfig.resolve_api_key("openrouter"), "or-openrouter-test-key")

    def test_openai_key_is_not_overwritten_or_deleted(self):
        with _EnvGuard(OPENAI_API_KEY="sk-openai-test-key", OPENROUTER_API_KEY="or-openrouter-test-key"):
            self.assertEqual(AppConfig.get_api_key("openai"), "sk-openai-test-key")
            self.assertEqual(AppConfig.get_api_key("openrouter"), "or-openrouter-test-key")
            self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-openai-test-key")

    def test_openai_key_still_used_for_official_openai_endpoint(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENAI_API_KEY="sk-openai-test-key",
            OPENROUTER_API_KEY="or-openrouter-test-key",
        ):
            self.assertEqual(AppConfig.resolve_api_key("openai"), "sk-openai-test-key")


class TestOpenRouterModelIdPassThrough(unittest.TestCase):
    """Explicit OpenRouter model IDs must never be remapped."""

    def test_openrouter_free_is_passed_through_verbatim(self):
        with _EnvGuard(OPENAI_BASE_URL=None, OPENROUTER_BASE_URL=None):
            _, remote_id = ModelDispatcher._prepare_openai_target("openrouter/free", "openrouter")
        self.assertEqual(remote_id, "openrouter/free")
        self.assertNotEqual(remote_id, "gpt-4o-mini")
        self.assertNotEqual(remote_id, "gpt-4o")

    def test_vendor_suffixed_free_model_is_passed_through_verbatim(self):
        model_id = "meta-llama/llama-3.3-70b-instruct:free"
        with _EnvGuard(OPENAI_BASE_URL=None, OPENROUTER_BASE_URL=None):
            _, remote_id = ModelDispatcher._prepare_openai_target(model_id, "openrouter")
        self.assertEqual(remote_id, model_id)

    def test_legacy_openai_mapping_preserved(self):
        with _EnvGuard(OPENAI_BASE_URL=None, OPENROUTER_BASE_URL=None):
            _, mini = ModelDispatcher._prepare_openai_target("GPT-4o-mini", "openai")
            _, full = ModelDispatcher._prepare_openai_target("GPT-5.6 Astra", "openai")
        self.assertEqual(mini, "gpt-4o-mini")
        self.assertEqual(full, "gpt-4o")

    def test_request_payload_uses_passthrough_model_id(self):
        _StubClient.calls = []
        _StubClient.status_code = 200
        _StubClient.raise_on_post = False
        _StubClient.response_payload = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            resp = dispatcher.generate(
                prompt="say hello",
                model_name="openrouter/free",
                is_tier1=True,
            )

        self.assertFalse(resp.is_mock)
        self.assertEqual(resp.provider, "OpenRouter")
        posts = [c for c in _StubClient.calls if c["json"] is not None]
        self.assertTrue(posts)
        self.assertEqual(posts[0]["json"]["model"], "openrouter/free")
        self.assertEqual(posts[0]["url"], f"{OPENROUTER_BASE}/chat/completions")
        self.assertEqual(posts[0]["headers"].get("Authorization"), "Bearer or-openrouter-test-key")


class TestOpenRouterChatSupportVerification(unittest.TestCase):
    """Chat-completions capability verification, exercised without network."""

    def test_listed_model_with_chat_parameters_is_supported(self):
        result = verify_openrouter_chat_support(
            "openrouter/free",
            models_payload=FAKE_CATALOG,
        )
        self.assertTrue(result.supported)
        self.assertTrue(result.is_usable)

    def test_model_without_chat_parameters_is_rejected(self):
        result = verify_openrouter_chat_support(
            "some/embeddings-only-model",
            models_payload=FAKE_CATALOG,
        )
        self.assertFalse(result.supported)
        self.assertFalse(result.is_usable)

    def test_unlisted_model_is_unknown_not_guaranteed(self):
        result = verify_openrouter_chat_support(
            "vendor/not-in-catalog",
            models_payload=FAKE_CATALOG,
        )
        self.assertIsNone(result.supported)
        self.assertTrue(result.is_usable)

    def test_verification_uses_injected_fetcher(self):
        calls = []

        def fake_fetch():
            calls.append(1)
            return FAKE_CATALOG

        result = verify_openrouter_chat_support("openrouter/free", fetch_models=fake_fetch)
        self.assertTrue(result.supported)
        self.assertEqual(len(calls), 1)

    def test_failing_catalog_lookup_is_unknown_not_fatal(self):
        def failing_fetch():
            raise RuntimeError("catalog unreachable")

        result = verify_openrouter_chat_support("openrouter/free", fetch_models=failing_fetch)
        self.assertIsNone(result.supported)
        self.assertTrue(result.is_usable)

    def test_dispatcher_raises_on_unsupported_model(self):
        dispatcher = ModelDispatcher(strict_live=True)
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.fetch_openrouter_models", return_value=FAKE_CATALOG):
            with self.assertRaises(ModelConfigurationError):
                dispatcher.generate(
                    prompt="say hello",
                    model_name="some/embeddings-only-model",
                    is_tier1=True,
                )


class TestStrictLiveBehaviour(unittest.TestCase):
    """strict_live=True must raise on API errors and never mock-fallback."""

    def setUp(self):
        _StubClient.calls = []
        _StubClient.status_code = 500
        _StubClient.raise_on_post = False
        _StubClient.response_payload = {}

    def test_api_error_raises_in_strict_live_mode(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            with self.assertRaises(RuntimeError) as ctx:
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )
        self.assertIn("openrouter/free", str(ctx.exception))

    def test_network_failure_raises_in_strict_live_mode(self):
        _StubClient.raise_on_post = True
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            with self.assertRaises(RuntimeError):
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )

    def test_missing_api_key_raises_in_strict_live_mode(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY=None,
            OPENAI_API_KEY=None,
        ):
            dispatcher = ModelDispatcher(strict_live=True)
            with self.assertRaises(RuntimeError) as ctx:
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )
        self.assertIn("No API key configured", str(ctx.exception))

    def test_strict_live_rejects_force_mock(self):
        with _EnvGuard():
            dispatcher = ModelDispatcher(force_mock=True, strict_live=True)
            with self.assertRaises(RuntimeError):
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )

    def test_non_strict_mode_still_falls_back_to_mock(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=False)
            resp = dispatcher.generate(
                prompt="say hello",
                model_name="openrouter/free",
                is_tier1=True,
            )
        self.assertTrue(resp.is_mock)


class TestQuotaExhaustedError(unittest.TestCase):
    """A 429/402 quota failure must raise a clear error, never mock silently."""

    def setUp(self):
        _StubClient.calls = []
        _StubClient.raise_on_post = False
        _StubClient.status_code = 429
        _StubClient.response_headers = {
            "x-ratelimit-limit": "50",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": "1792454400000",
        }
        _StubClient.response_payload = {
            "error": {
                "message": (
                    "Rate limit exceeded: free-models-per-day. "
                    "Add 10 credits to unlock 1000 free model requests per day"
                ),
                "code": 429,
            }
        }

    def test_quota_error_message_is_clear(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            with self.assertRaises(QuotaExceededError) as ctx:
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )
        message = str(ctx.exception)
        self.assertIn("Quota exhausted", message)
        self.assertIn("openrouter/free", message)
        self.assertIn("free-models-per-day", message)
        self.assertIn("1792454400000", message)

    def test_quota_error_is_not_masked_by_mock_in_non_strict_mode(self):
        """Even outside strict-live, a quota failure must not become a mock."""
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=False)
            with self.assertRaises(QuotaExceededError):
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )

    def test_quota_error_is_not_retried(self):
        """A daily quota cannot clear within a run, so it must not be retried."""
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            with self.assertRaises(QuotaExceededError):
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )
        posts = [c for c in _StubClient.calls if c["json"] is not None]
        self.assertEqual(len(posts), 1)

    def test_transient_per_minute_limit_is_retried_then_raises(self):
        """A per-minute limit is transient, so it is retried before raising."""
        _StubClient.response_payload = {
            "error": {"message": "Rate limit exceeded", "code": 429}
        }
        _StubClient.response_headers = {}
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient), \
                mock.patch("model_dispatcher.time.sleep", lambda *_: None):
            dispatcher = ModelDispatcher(strict_live=True)
            with self.assertRaises(QuotaExceededError):
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )
        posts = [c for c in _StubClient.calls if c["json"] is not None]
        self.assertEqual(len(posts), 3, "a transient limit should be retried twice")

    def test_insufficient_credits_402_raises_quota_error(self):
        _StubClient.status_code = 402
        _StubClient.response_payload = {
            "error": {"message": "This request requires more credits", "code": 402}
        }
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            with self.assertRaises(QuotaExceededError):
                dispatcher.generate(
                    prompt="say hello",
                    model_name="openrouter/free",
                    is_tier1=True,
                )


class TestProviderSelectionConsistency(unittest.TestCase):
    """Provider selection must be consistent and must not substitute paid models."""

    def test_openrouter_gateway_does_not_substitute_paid_openai_model(self):
        """Non-OpenAI labels must not silently become a paid openai/gpt-4o."""
        for label in (
            "Qwen 3.5 / 3.8 Coder",
            "Llama 3.3 / Llama 4 (70B)",
            "Frontier Standard (Spec Default)",
        ):
            with self.subTest(label=label):
                with self.assertRaises(ModelConfigurationError):
                    ModelDispatcher._resolve_remote_model_id(
                        label, "openai", "https://openrouter.ai/api/v1"
                    )

    def test_openai_family_labels_are_namespaced_on_openrouter_gateway(self):
        self.assertEqual(
            ModelDispatcher._resolve_remote_model_id(
                "GPT-4o-mini", "openai", "https://openrouter.ai/api/v1"
            ),
            "openai/gpt-4o-mini",
        )
        self.assertEqual(
            ModelDispatcher._resolve_remote_model_id(
                "GPT-5.6 / ChatGPT Astra", "openai", "https://openrouter.ai/api/v1"
            ),
            "openai/gpt-4o",
        )

    def test_legacy_openai_mapping_untouched_off_openrouter(self):
        self.assertEqual(
            ModelDispatcher._resolve_remote_model_id(
                "Qwen 3.5 / 3.8 Coder", "openai", "https://api.openai.com/v1"
            ),
            "gpt-4o",
        )

    def test_openrouter_key_not_sent_to_custom_gateway(self):
        """An OpenRouter key must never be sent to a non-OpenRouter endpoint."""
        with _EnvGuard(
            OPENAI_BASE_URL="http://localhost:8000/v1",
            OPENROUTER_BASE_URL=None,
            OPENAI_API_KEY="sk-openai-test-key",
            OPENROUTER_API_KEY="or-openrouter-test-key",
        ):
            self.assertEqual(AppConfig.resolve_api_key("openrouter"), "sk-openai-test-key")

    def test_every_catalog_model_resolves_to_a_usable_id(self):
        """No catalog label may map to an empty remote model ID."""
        from telemetry import TIER_1_BUDGET_PROFILES, TIER_2_FRONTIER_PROFILES
        labels = list(TIER_1_BUDGET_PROFILES) + list(TIER_2_FRONTIER_PROFILES)
        for label in labels:
            with self.subTest(label=label):
                provider = ModelDispatcher._resolve_provider(label)
                base = "https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.openai.com/v1"
                remote_id = ModelDispatcher._resolve_remote_model_id(label, provider, base)
                self.assertTrue(remote_id)

    def test_openrouter_free_catalog_entry_is_never_remapped(self):
        """The openrouter/free catalog entry must never become a paid model."""
        from telemetry import TIER_1_BUDGET_PROFILES, TIER_2_FRONTIER_PROFILES
        for catalog in (TIER_1_BUDGET_PROFILES, TIER_2_FRONTIER_PROFILES):
            with self.subTest(catalog=id(catalog)):
                remote_id = ModelDispatcher._resolve_remote_model_id(
                    "openrouter/free", "openrouter", "https://openrouter.ai/api/v1"
                )
                self.assertEqual(remote_id, "openrouter/free")
                self.assertNotIn("gpt-4o", remote_id)
                self.assertIn("openrouter/free", catalog)


class TestBenchmarkStrictLive(unittest.TestCase):
    """Benchmark runs must not report mock results as live measurements."""

    def _capture_dispatcher(self, **suite_kwargs):
        """Runs the benchmark's dispatcher construction and captures its flags."""
        import benchmark_harness
        captured = {}

        class _CaptureDispatcher(ModelDispatcher):
            def __init__(self, force_mock=False, strict_live=None):
                captured["force_mock"] = force_mock
                captured["strict_live"] = strict_live

        class _StubOrchestrator:
            def __init__(self, dispatcher=None, **kwargs):
                captured["dispatcher"] = dispatcher

            def run_cascade(self, *args, **kwargs):
                raise _StopRun()

        with mock.patch.object(benchmark_harness, "ModelDispatcher", _CaptureDispatcher), \
             mock.patch.object(benchmark_harness, "RelayOrchestrator", _StubOrchestrator):
            with self.assertRaises(_StopRun):
                benchmark_harness.MacroBenchmarkSuite.run_suite(**suite_kwargs)
        return captured

    def test_benchmark_is_strict_when_a_live_key_is_configured(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ):
            captured = self._capture_dispatcher(
                tier1_model="openrouter/free",
                tier2_model="openrouter/free",
            )
        self.assertTrue(captured["strict_live"])

    def test_benchmark_is_not_strict_without_any_live_key(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY=None,
            OPENAI_API_KEY=None,
        ):
            captured = self._capture_dispatcher(
                tier1_model="openrouter/free",
                tier2_model="openrouter/free",
            )
        self.assertFalse(captured["strict_live"])

    def test_force_mock_forces_non_strict(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ):
            captured = self._capture_dispatcher(
                tier1_model="openrouter/free",
                tier2_model="openrouter/free",
                force_mock=True,
            )
        self.assertTrue(captured["force_mock"])
        self.assertFalse(captured["strict_live"])

    def test_explicit_strict_live_argument_is_respected(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY=None,
            OPENAI_API_KEY=None,
        ):
            captured = self._capture_dispatcher(
                tier1_model="openrouter/free",
                tier2_model="openrouter/free",
                strict_live=True,
            )
        self.assertTrue(captured["strict_live"])

    def test_has_live_provider_key(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-openrouter-test-key",
            OPENAI_API_KEY=None,
        ):
            self.assertTrue(
                ModelDispatcher.has_live_provider_key(["openrouter/free"])
            )
            self.assertFalse(
                ModelDispatcher.has_live_provider_key(["Gemini 3.5 Flash"])
            )

    def test_strict_live_env_flag_is_honoured(self):
        with _EnvGuard(STRICT_LIVE="1"):
            dispatcher = ModelDispatcher()
            self.assertTrue(dispatcher.strict_live)

    def test_strict_live_env_flag_does_not_override_force_mock(self):
        with _EnvGuard(STRICT_LIVE="1"):
            dispatcher = ModelDispatcher(force_mock=True)
            self.assertFalse(dispatcher.strict_live)

    def test_explicit_strict_live_argument_wins_over_env(self):
        with _EnvGuard(STRICT_LIVE="0"):
            dispatcher = ModelDispatcher(strict_live=True)
            self.assertTrue(dispatcher.strict_live)


class TestSecretHygiene(unittest.TestCase):
    """Secrets stay in .env, .env stays git-ignored, keys are never printed."""

    def test_env_file_is_git_ignored(self):
        self.assertTrue(os.path.exists(".gitignore"), ".gitignore must exist")
        with open(".gitignore", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(".env", content)

    def test_env_example_documents_openrouter(self):
        with open("env.example", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("OPENROUTER_API_KEY", content)
        self.assertIn("OPENAI_BASE_URL", content)

    def test_masking_never_reveals_full_key(self):
        key = "or-live-abcdef1234567890"
        masked = AppConfig.mask_key(key)
        self.assertNotIn("abcdef1234567890", masked)
        self.assertIn("...", masked)

    def test_openrouter_headers_contain_no_secret(self):
        with _EnvGuard(
            OPENROUTER_HTTP_REFERER="https://example.test",
            OPENROUTER_APP_TITLE="ITMS",
            OPENROUTER_API_KEY="or-openrouter-test-key",
        ):
            headers = AppConfig.get_openrouter_headers()
        self.assertNotIn("or-openrouter-test-key", str(headers))
        self.assertEqual(headers["HTTP-Referer"], "https://example.test")

    def test_tier_model_defaults_can_be_overridden(self):
        options = ["Gemini 3.5 Flash", "openrouter/free"]
        with _EnvGuard(DEFAULT_TIER1_MODEL="openrouter/free", DEFAULT_TIER2_MODEL="openrouter/free"):
            self.assertEqual(AppConfig.get_default_tier1_model(options), "openrouter/free")
            self.assertEqual(AppConfig.get_default_tier2_model(options), "openrouter/free")

    def test_invalid_tier_model_default_is_ignored(self):
        options = ["Gemini 3.5 Flash", "openrouter/free"]
        with _EnvGuard(DEFAULT_TIER1_MODEL="not-a-real-model"):
            self.assertIsNone(AppConfig.get_default_tier1_model(options))

    def test_arbitrary_openrouter_id_is_accepted_as_default(self):
        """An explicit OpenRouter model ID must be selectable, not silently ignored."""
        from telemetry import TIER_1_BUDGET_PROFILES, resolve_tier_pricing
        options = list(TIER_1_BUDGET_PROFILES.keys())
        model_id = "meta-llama/llama-3.3-70b-instruct:free"
        with _EnvGuard(DEFAULT_TIER1_MODEL=model_id):
            self.assertEqual(AppConfig.get_default_tier1_model(options), model_id)
            # Must not raise, and must not be priced at another vendor's rate.
            profile = resolve_tier_pricing(model_id, TIER_1_BUDGET_PROFILES)
            self.assertEqual(profile.price_input_uncached, 0.0)

    def test_known_catalog_key_still_resolves(self):
        from telemetry import TIER_1_BUDGET_PROFILES
        options = list(TIER_1_BUDGET_PROFILES.keys())
        with _EnvGuard(DEFAULT_TIER1_MODEL="openrouter/free"):
            self.assertEqual(AppConfig.get_default_tier1_model(options), "openrouter/free")

    def test_openrouter_free_is_offered_as_a_tier_option(self):
        from telemetry import TIER_1_BUDGET_PROFILES, TIER_2_FRONTIER_PROFILES
        self.assertIn("openrouter/free", TIER_1_BUDGET_PROFILES)
        self.assertIn("openrouter/free", TIER_2_FRONTIER_PROFILES)
        self.assertEqual(TIER_1_BUDGET_PROFILES["openrouter/free"].price_output, 0.0)
        self.assertEqual(TIER_2_FRONTIER_PROFILES["openrouter/free"].price_output, 0.0)


class TestRequestedModelIsSentVerbatim(unittest.TestCase):
    """
    The dispatcher must send the model the caller asked for.

    A hard-coded model id would silently run a different model than the
    experiment selected, which would make every measurement of that run a
    fiction. These tests pin the wire payload.
    """

    def test_anthropic_uses_requested_model(self):
        """Anthropic must receive the caller's model id, not a fixed one."""
        _StubClient.calls = []
        _StubClient.status_code = 200
        _StubClient.raise_on_post = False
        _StubClient.response_payload = {
            "content": [{"type": "text", "text": "hello"}],
            "model": "claude-sonnet-custom",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 2},
        }

        with _EnvGuard(
            ANTHROPIC_API_KEY="anthropic-test-key",
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            resp = dispatcher.generate(
                prompt="say hello",
                model_name="claude-sonnet-custom",
                is_tier1=True,
            )

        self.assertFalse(resp.is_mock)
        self.assertEqual(resp.provider, "Anthropic")
        posts = [c for c in _StubClient.calls if c["json"] is not None]
        self.assertTrue(posts, "the stub client received no request")
        self.assertEqual(posts[0]["json"]["model"], "claude-sonnet-custom")
        self.assertNotEqual(posts[0]["json"]["model"], "claude-3-5-sonnet-20241022")
        self.assertIn("claude-sonnet-custom", posts[0]["url"] + posts[0]["json"]["model"])

    def test_gemini_uses_requested_model(self):
        """A gemini-* model id must be sent verbatim on the wire."""
        _StubClient.calls = []
        _StubClient.status_code = 200
        _StubClient.raise_on_post = False
        _StubClient.response_payload = {
            "candidates": [
                {"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}
            ],
            "modelVersion": "gemini-2.5-pro-preview",
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 2,
                "totalTokenCount": 9,
            },
        }

        with _EnvGuard(
            GEMINI_API_KEY="gemini-test-key",
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            resp = dispatcher.generate(
                prompt="say hello",
                model_name="gemini-2.5-pro-preview",
                is_tier1=True,
            )

        self.assertFalse(resp.is_mock)
        self.assertEqual(resp.provider, "Google DeepMind")
        posts = [c for c in _StubClient.calls if c["json"] is not None]
        self.assertTrue(posts, "the stub client received no request")
        self.assertIn("/models/gemini-2.5-pro-preview:generateContent", posts[0]["url"])
        self.assertNotIn("gemini-2.0-flash", posts[0]["url"])
        self.assertNotIn("gemini-1.5-pro", posts[0]["url"])

    def test_gemini_display_label_falls_back_to_a_concrete_route(self):
        """
        A product display label is not a wire model id, so it is mapped.

        The fallback is documented behaviour and must stay stable.
        """
        _StubClient.calls = []
        _StubClient.status_code = 200
        _StubClient.raise_on_post = False
        _StubClient.response_payload = {
            "candidates": [
                {"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }

        with _EnvGuard(
            GEMINI_API_KEY="gemini-test-key",
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            dispatcher = ModelDispatcher(strict_live=True)
            dispatcher.generate(
                prompt="say hello",
                model_name="Gemini 3.5 Flash",
                is_tier1=True,
            )

        posts = [c for c in _StubClient.calls if c["json"] is not None]
        self.assertTrue(posts)
        self.assertIn("/models/gemini-2.0-flash:generateContent", posts[0]["url"])


if __name__ == "__main__":
    unittest.main()
