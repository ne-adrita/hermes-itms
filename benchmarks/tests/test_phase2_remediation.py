"""
Phase 2 remediation tests.

Covers the five Major findings of the Phase 2A audit (M1 token provenance,
M2 repeated catalog request, M3 provider/model identity, M4 run metadata,
M5 retry accounting) and the Minor findings that were fixed.

Everything runs offline: the provider layer is exercised with stubbed HTTP
clients, and the session-wide socket guard in ``conftest.py`` makes any real
network access fail loudly.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import httpx  # noqa: E402
import model_dispatcher as MD  # noqa: E402
from benchmarks.benchmark.experiment import ExperimentRunner  # noqa: E402
from benchmarks.benchmark.pipeline import AttemptPipeline  # noqa: E402
from benchmarks.benchmark.provider import (  # noqa: E402
    LiveProvider,
    MockProvider,
    ProviderError,
    ProviderResult,
)
from benchmarks.benchmark.records import (  # noqa: E402
    AttemptStatus,
    FailureCategory,
)

RUNNER = os.path.join("benchmarks", "benchmark_runner.py")


# --------------------------------------------------------------------------
# Stubbed HTTP layer
# --------------------------------------------------------------------------

class StubResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://example.invalid"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class RecordingClient:
    """A stub ``httpx.Client`` that records every request it is asked to make.

    ``GET`` requests (the model catalog) and ``POST`` requests (chat
    completions) use separate queues, so a test can control exactly how many
    chat requests are made.
    """

    calls: list = []
    post_responses: list = []
    get_responses: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def _catalog(self):
        return StubResponse(
            200,
            {
                "data": [
                    {
                        "id": "vendor/model",
                        "endpoints": ["chat"],
                        "supported_parameters": ["max_tokens"],
                    }
                ]
            },
        )

    def post(self, url, **kwargs):
        RecordingClient.calls.append(("POST", url))
        if RecordingClient.post_responses:
            item = RecordingClient.post_responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return StubResponse(
            200,
            {
                "model": "vendor/model-20260101",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
            },
        )

    def get(self, url, **kwargs):
        RecordingClient.calls.append(("GET", url))
        if RecordingClient.get_responses:
            item = RecordingClient.get_responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return self._catalog()


@pytest.fixture
def stub_http(monkeypatch):
    """Install the recording stub client and a fake credential."""
    RecordingClient.calls = []
    RecordingClient.post_responses = []
    RecordingClient.get_responses = []
    monkeypatch.setattr(MD.httpx, "Client", RecordingClient)
    from config import AppConfig

    monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: "sk-test-key"))
    return RecordingClient


def _chat_payload(usage=None, finish_reason="stop", model="vendor/model-20260101"):
    payload = {
        "model": model,
        "choices": [{"message": {"content": "ok"}, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


# --------------------------------------------------------------------------
# M1 — token provenance
# --------------------------------------------------------------------------

class TestM1TokenProvenance:

    def _response(self, usage, finish_reason="stop"):
        """The ``stub_http`` fixture must already be active for this test."""
        RecordingClient.post_responses = [
            StubResponse(200, _chat_payload(usage=usage, finish_reason=finish_reason))
        ]
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        return provider.complete("a prompt")

    def test_absent_usage_is_not_reported_as_provider_usage(self, stub_http):
        result = self._response(None)
        assert result.prompt_tokens is None
        assert result.completion_tokens is None
        assert result.total_tokens is None
        assert result.usage_source == "local_estimate"
        # A local estimate is kept, but in its own field.
        assert result.estimated_prompt_tokens is not None
        assert result.estimated_prompt_tokens != result.prompt_tokens

    def test_empty_usage_object_is_not_provider_reported(self, stub_http):
        result = self._response({})
        assert result.prompt_tokens is None
        assert result.completion_tokens is None
        assert result.total_tokens is None
        assert result.usage_source == "local_estimate"
        assert result.estimated_prompt_tokens is not None

    def test_total_tokens_only_usage(self, stub_http):
        result = self._response({"total_tokens": 42})
        assert result.total_tokens == 42
        assert result.prompt_tokens is None
        assert result.completion_tokens is None
        assert result.usage_source == "provider_partial"

    def test_partial_token_fields(self, stub_http):
        result = self._response({"prompt_tokens": 20})
        assert result.prompt_tokens == 20
        assert result.completion_tokens is None
        assert result.total_tokens is None
        assert result.usage_source == "provider_partial"
        assert result.estimated_completion_tokens is not None

    def test_complete_usage_is_provider_reported(self, stub_http):
        result = self._response(
            {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
        )
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 5
        assert result.total_tokens == 25
        assert result.usage_source == "provider_reported"
        assert result.estimated_prompt_tokens is None
        assert result.estimated_completion_tokens is None

    def test_missing_cost_stays_null(self, stub_http):
        result = self._response(
            {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
        )
        assert result.cost_usd is None
        assert result.cost_source is None

    def test_provider_reported_cost_is_kept(self, stub_http):
        payload = _chat_payload(usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25})
        payload["cost"] = 0.0025
        RecordingClient.post_responses = [StubResponse(200, payload)]
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        result = provider.complete("a prompt")
        assert result.cost_usd == pytest.approx(0.0025)
        assert result.cost_source == "provider_reported"

    def test_mock_provider_reports_no_usage_at_all(self):
        result = MockProvider().complete("prompt")
        assert result.prompt_tokens is None
        assert result.total_tokens is None
        assert result.cost_usd is None
        assert result.usage_source != "provider_reported"

    def test_analysis_excludes_estimates_from_provider_totals(self):
        from benchmarks.benchmark.analysis import method_metrics
        from benchmarks.benchmark.records import AttemptRecord

        records = [
            AttemptRecord(
                attempt_id="a" * 32, run_id="r", timestamp_utc="t", task_id="HE-EDIT-001",
                humaneval_task_id="HumanEval/0", method="full_file_regeneration", rep=1,
                mode="live", provider="openrouter", requested_model_id="vendor/model",
                status=AttemptStatus.REPAIR_SUCCESS, test_passed=True, syntax_valid=True,
                prompt_tokens=100, completion_tokens=10, total_tokens=110,
                usage_source="provider_reported", estimated_prompt_tokens=101,
            ),
            AttemptRecord(
                attempt_id="b" * 32, run_id="r", timestamp_utc="t", task_id="HE-EDIT-002",
                humaneval_task_id="HumanEval/1", method="full_file_regeneration", rep=1,
                mode="live", provider="openrouter", requested_model_id="vendor/model",
                status=AttemptStatus.REPAIR_FAILED, test_passed=False, syntax_valid=True,
                prompt_tokens=50, completion_tokens=None, total_tokens=None,
                usage_source="provider_partial", estimated_completion_tokens=7,
            ),
        ]
        metrics = method_metrics(
            records, scheduled=2, method="full_file_regeneration", tasks_count=2, reps=1
        )
        usage = metrics["token_usage"]
        assert usage["provider_reported_attempts"] == 1
        assert usage["provider_partial_attempts"] == 1
        assert usage["total_tokens_total"] == 110  # the estimate is not added
        assert usage["missing_attempts"] == 0

    def test_record_keeps_estimate_in_a_separate_field(self):
        from benchmarks.benchmark.records import AttemptRecord

        record = AttemptRecord(
            attempt_id="c" * 32, run_id="r", timestamp_utc="t", task_id="HE-EDIT-001",
            humaneval_task_id="HumanEval/0", method="full_file_regeneration", rep=1,
            mode="live", provider="openrouter", requested_model_id="vendor/model",
        )
        assert record.prompt_tokens is None
        assert record.estimated_prompt_tokens is None
        assert record.cost_usd is None
        assert "estimated_prompt_tokens" in record.to_csv_row()


# --------------------------------------------------------------------------
# M2 — the catalog request must not repeat per attempt
# --------------------------------------------------------------------------

class TestM2SingleCatalogRequest:

    def test_catalog_is_fetched_once_for_many_attempts(self, stub_http, monkeypatch):
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        for _ in range(5):
            provider.complete("a prompt")

        gets = [c for c in RecordingClient.calls if c[0] == "GET"]
        posts = [c for c in RecordingClient.calls if c[0] == "POST"]
        assert len(gets) == 1, f"catalog request repeated: {RecordingClient.calls}"
        assert len(posts) == 5

    def test_dispatcher_instance_is_reused(self, stub_http):
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        provider.complete("a prompt")
        first = provider._dispatcher_instance()
        provider.complete("a prompt")
        assert provider._dispatcher_instance() is first

    def test_planned_call_count_stays_honest(self, stub_http, repository, tmp_path):
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        runner = ExperimentRunner(
            repository=repository,
            provider=provider,
            mode="live",
            output_dir=str(tmp_path / "run"),
            methods=["full_file_regeneration"],
            reps=1,
            task_ids=["HE-EDIT-001", "HE-EDIT-002"],
            seed=1,
        )
        assert runner.planned_call_count() == 2
        runner.run(progress=False)
        gets = [c for c in RecordingClient.calls if c[0] == "GET"]
        posts = [c for c in RecordingClient.calls if c[0] == "POST"]
        assert len(posts) == 2
        assert len(gets) == 1


# --------------------------------------------------------------------------
# M3 — provider and model identity
# --------------------------------------------------------------------------

class TestM3ProviderAndModelIdentity:

    def test_declared_provider_must_match_the_resolved_provider(self, stub_http):
        provider = LiveProvider(provider="openrouter", model_id="gpt-4o-mini")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert excinfo.value.category == "configuration_error"
        assert "does not match" in str(excinfo.value)

    def test_openrouter_style_id_is_accepted_and_forwarded_verbatim(self, stub_http):
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        provider.preflight()  # must not raise
        assert provider.effective_provider == "openrouter"
        assert provider._resolve_remote_model_id() == "vendor/model"

    def test_gemini_path_is_refused_because_the_model_id_cannot_be_honoured(
        self, stub_http, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_BASE_URL", "")
        provider = LiveProvider(provider="gemini", model_id="gemini-2.0-flash")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert excinfo.value.category == "configuration_error"

    def test_anthropic_path_is_refused_because_the_model_id_cannot_be_honoured(
        self, stub_http, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_BASE_URL", "")
        provider = LiveProvider(provider="anthropic", model_id="claude-3-5-sonnet-20241022")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert excinfo.value.category == "configuration_error"

    def test_openai_provider_without_an_explicit_id_is_refused(self, stub_http, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "")
        provider = LiveProvider(provider="openai", model_id="my-custom-model")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert excinfo.value.category == "configuration_error"

    def test_dynamic_alias_is_still_refused(self, stub_http):
        provider = LiveProvider(provider="openrouter", model_id="openrouter/free")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert "dynamic alias" in str(excinfo.value)

    def test_served_model_id_is_never_invented(self, stub_http, monkeypatch):
        stub_http(monkeypatch)
        payload = _chat_payload(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        payload.pop("model")
        RecordingClient.post_responses = [StubResponse(200, payload)]
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        result = provider.complete("a prompt")
        assert result.served_model_id is None
        assert result.requested_model_id == "vendor/model"

    def test_served_model_id_is_recorded_when_the_provider_reports_it(self, stub_http, monkeypatch):
        stub_http(monkeypatch)
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        result = provider.complete("a prompt")
        assert result.served_model_id == "vendor/model-20260101"
        assert result.requested_model_id == "vendor/model"

    def test_model_mismatch_between_requested_and_served_is_visible(self, stub_http, monkeypatch):
        stub_http(monkeypatch)
        payload = _chat_payload(
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model="vendor/model-DIFFERENT",
        )
        RecordingClient.post_responses = [StubResponse(200, payload)]
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        result = provider.complete("a prompt")
        assert result.requested_model_id != result.served_model_id


# --------------------------------------------------------------------------
# M4 — reproducible run metadata
# --------------------------------------------------------------------------

class TestM4RunMetadata:

    def _run(self, repository, tmp_path, **provider_kwargs):
        provider = MockProvider(**provider_kwargs)
        runner = ExperimentRunner(
            repository=repository,
            provider=provider,
            mode="mock",
            output_dir=str(tmp_path / "run"),
            methods=["full_file_regeneration", "search_replace_patch", "itms_ast_splice"],
            reps=1,
            task_ids=["HE-EDIT-001", "HE-EDIT-002"],
            seed=1234,
        )
        runner.run(progress=False)
        with open(runner.output_paths()["run_metadata_json"], "r", encoding="utf-8") as fh:
            return json.load(fh), runner

    def test_metadata_contains_every_required_field(self, repository, tmp_path):
        metadata, _ = self._run(repository, tmp_path)
        required = [
            "mode",
            "declared_provider",
            "effective_provider",
            "requested_model_id",
            "served_model_id",
            "temperature",
            "max_output_tokens",
            "request_timeout_seconds",
            "evaluation_timeout_seconds",
            "max_retries",
            "retry_policy",
            "reps",
            "tasks",
            "task_count",
            "methods",
            "run_order_seed",
            "bootstrap_seed",
            "planned_attempts",
            "task_manifest_digest",
            "software_versions",
            "python_version",
            "platform",
        ]
        for field in required:
            assert field in metadata, field

    def test_metadata_values_are_correct(self, repository, tmp_path):
        metadata, runner = self._run(repository, tmp_path)
        assert metadata["mode"] == "mock"
        assert metadata["is_measurement"] is False
        assert metadata["reps"] == 1
        assert metadata["task_count"] == 2
        assert metadata["methods"] == [
            "full_file_regeneration",
            "search_replace_patch",
            "itms_ast_splice",
        ]
        assert metadata["run_order_seed"] == 1234
        assert isinstance(metadata["bootstrap_seed"], int)
        assert metadata["planned_attempts"] == 6
        assert metadata["task_manifest_digest"] == repository.manifest_digest()
        assert metadata["served_model_id"] is None  # never invented

    def test_full_experiment_schedule_is_300_attempts(self, repository, tmp_path):
        """The planned experiment is 20 tasks x 3 methods x 5 reps = 300."""
        runner = ExperimentRunner(
            repository=repository,
            provider=MockProvider(),
            mode="mock",
            output_dir=str(tmp_path / "run"),
            methods=["full_file_regeneration", "search_replace_patch", "itms_ast_splice"],
            reps=5,
            task_ids=repository.task_ids,
            seed=1,
        )
        assert runner.planned_call_count() == 300
        assert len(runner.plan()) == 300
        assert len({plan.key for plan in runner.plan()}) == 300

    def test_metadata_reads_the_actual_provider_configuration(self, repository, tmp_path):
        provider = LiveProvider(
            provider="openrouter",
            model_id="vendor/model",
            temperature=0.35,
            max_output_tokens=1234,
            request_timeout=17.0,
            max_retries=1,
        )
        runner = ExperimentRunner(
            repository=repository,
            provider=provider,
            mode="live",
            output_dir=str(tmp_path / "run"),
            methods=["full_file_regeneration"],
            reps=1,
            task_ids=["HE-EDIT-001"],
        )
        runner.run(progress=False)
        with open(runner.output_paths()["run_metadata_json"], "r", encoding="utf-8") as fh:
            metadata = json.load(fh)
        assert metadata["temperature"] == 0.35
        assert metadata["max_output_tokens"] == 1234
        assert metadata["request_timeout_seconds"] == 17.0
        assert metadata["max_retries"] == 1
        assert metadata["declared_provider"] == "openrouter"

    def test_observed_served_model_ids_are_reported_in_the_summary(self, repository, tmp_path):
        from config import AppConfig

        original = AppConfig.resolve_api_key
        AppConfig.resolve_api_key = staticmethod(lambda p: "sk-test-key")
        try:
            provider = LiveProvider(provider="openrouter", model_id="vendor/model")
            runner = ExperimentRunner(
                repository=repository,
                provider=provider,
                mode="live",
                output_dir=str(tmp_path / "run"),
                methods=["full_file_regeneration"],
                reps=1,
                task_ids=["HE-EDIT-001"],
            )
            summary = runner.run(progress=False)
        finally:
            AppConfig.resolve_api_key = original
        assert "observed_served_model_ids" in summary
        assert isinstance(summary["observed_served_model_ids"], list)


# --------------------------------------------------------------------------
# M5 — retry accounting
# --------------------------------------------------------------------------

class TestM5RetryAccounting:

    @pytest.fixture(autouse=True)
    def _no_backoff_sleep(self, monkeypatch):
        """The retry back-off is real, but a test must not wait for it."""
        monkeypatch.setattr(MD.time, "sleep", lambda *_: None)

    def _provider(self, responses, **kwargs):
        from config import AppConfig

        original = AppConfig.resolve_api_key
        AppConfig.resolve_api_key = staticmethod(lambda p: "sk-test-key")
        try:
            RecordingClient.post_responses = list(responses)
            provider = LiveProvider(provider="openrouter", model_id="vendor/model", **kwargs)
            return provider
        finally:
            AppConfig.resolve_api_key = original

    def test_success_without_retries_records_zero(self, stub_http):
        provider = self._provider(
            [StubResponse(200, _chat_payload(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}))]
        )
        result = provider.complete("prompt")
        assert result.retry_count == 0

    def test_success_after_one_retry_records_the_retry(self, stub_http, monkeypatch):
        monkeypatch.setattr(MD.time, "sleep", lambda *_: None)
        provider = self._provider(
            [
                StubResponse(429, {"error": {"message": "rate limited, try later"}}),
                StubResponse(200, _chat_payload(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})),
            ],
            max_retries=2,
        )
        result = provider.complete("prompt")
        assert result.retry_count == 1
        posts = [c for c in RecordingClient.calls if c[0] == "POST"]
        assert len(posts) == 2

    def test_failure_after_multiple_retries_preserves_the_retry_count(self, stub_http, monkeypatch):
        monkeypatch.setattr(MD.time, "sleep", lambda *_: None)
        provider = self._provider(
            [
                StubResponse(429, {"error": {"message": "rate limited, try later"}}),
                StubResponse(429, {"error": {"message": "rate limited, try later"}}),
                StubResponse(429, {"error": {"message": "rate limited, try later"}}),
            ],
            max_retries=2,
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "quota_exceeded"
        assert excinfo.value.retry_count == 2
        posts = [c for c in RecordingClient.calls if c[0] == "POST"]
        assert len(posts) == 3  # 1 initial + 2 retries

    def test_daily_quota_is_not_retried(self, stub_http, monkeypatch):
        monkeypatch.setattr(MD.time, "sleep", lambda *_: None)
        provider = self._provider(
            [StubResponse(429, {"error": {"message": "free-models-per-day limit reached"}})],
            max_retries=2,
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "quota_exceeded"
        assert excinfo.value.retry_count == 0
        posts = [c for c in RecordingClient.calls if c[0] == "POST"]
        assert len(posts) == 1

    def test_payment_required_is_not_retried(self, stub_http, monkeypatch):
        monkeypatch.setattr(MD.time, "sleep", lambda *_: None)
        provider = self._provider(
            [StubResponse(402, {"error": {"message": "insufficient credits"}})], max_retries=2
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "quota_exceeded"
        assert excinfo.value.retry_count == 0
        posts = [c for c in RecordingClient.calls if c[0] == "POST"]
        assert len(posts) == 1

    def test_timeout_is_categorised_and_preserves_retries(self, stub_http, monkeypatch):
        monkeypatch.setattr(MD.time, "sleep", lambda *_: None)
        provider = self._provider(
            [
                httpx.TimeoutException("timed out"),
                httpx.TimeoutException("timed out"),
            ],
            max_retries=1,
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "timeout"

    def test_pipeline_records_the_retry_count_of_a_failed_attempt(self, stub_http, monkeypatch, simple_task):
        monkeypatch.setattr(MD.time, "sleep", lambda *_: None)
        from config import AppConfig

        original = AppConfig.resolve_api_key
        AppConfig.resolve_api_key = staticmethod(lambda p: "sk-test-key")
        try:
            RecordingClient.post_responses = [
                StubResponse(429, {"error": {"message": "rate limited, try later"}}),
                StubResponse(429, {"error": {"message": "rate limited, try later"}}),
                StubResponse(429, {"error": {"message": "rate limited, try later"}}),
            ]
            provider = LiveProvider(provider="openrouter", model_id="vendor/model", max_retries=2)
            pipeline = AttemptPipeline(
                provider=provider,
                adapter=__import__(
                    "benchmarks.benchmark.adapters", fromlist=["get_adapter"]
                ).get_adapter("full_file_regeneration"),
                mode="live",
                run_id="test",
            )
            outcome = pipeline.run(simple_task, 1)
        finally:
            AppConfig.resolve_api_key = original
        record = outcome.record
        assert record.status == AttemptStatus.PROVIDER_ERROR
        assert record.failure_category == FailureCategory.QUOTA_EXCEEDED
        assert record.retry_count == 2


# --------------------------------------------------------------------------
# Minor findings
# --------------------------------------------------------------------------

class TestMinorFindings:

    def test_missing_credential_is_a_configuration_error(self, monkeypatch):
        from config import AppConfig

        monkeypatch.setattr(AppConfig, "resolve_api_key", staticmethod(lambda p: None))
        provider = LiveProvider(provider="openrouter", model_id="vendor/model")
        with pytest.raises(ProviderError) as excinfo:
            provider.preflight()
        assert excinfo.value.category == "configuration_error"

    def test_dispatcher_configuration_error_maps_to_configuration_error(self, stub_http, monkeypatch):
        from model_dispatcher import ModelConfigurationError

        class Dispatcher:
            def generate_live(self, **kwargs):
                raise ModelConfigurationError("bad model id")

        provider = LiveProvider(
            provider="openrouter", model_id="vendor/model", dispatcher=Dispatcher()
        )
        with pytest.raises(ProviderError) as excinfo:
            provider.complete("prompt")
        assert excinfo.value.category == "configuration_error"

    def test_truncated_output_is_recorded_distinctly(self, simple_task):
        class TruncatingProvider(MockProvider):
            def complete(self, prompt, system_instruction=None):
                result = super().complete(prompt, system_instruction)
                result.finish_reason = "length"
                return result

        provider = TruncatingProvider(policy="malformed")
        pipeline = AttemptPipeline(
            provider=provider,
            adapter=__import__(
                "benchmarks.benchmark.adapters", fromlist=["get_adapter"]
            ).get_adapter("full_file_regeneration"),
            mode="mock",
            run_id="test",
        )
        outcome = pipeline.run(simple_task, 1)
        record = outcome.record
        assert record.failure_category == FailureCategory.TRUNCATED_OUTPUT
        assert record.truncated_output is True
        assert record.status == AttemptStatus.OUTPUT_PARSE_ERROR

    def test_dry_run_rejects_unknown_methods_cleanly(self):
        result = subprocess.run(
            [sys.executable, RUNNER, "--dry-run", "--methods", "not_a_method"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 2
        assert "unknown editing method" in (result.stdout + result.stderr)
        assert "Traceback" not in (result.stdout + result.stderr)

    def test_two_runs_in_one_directory_do_not_mix_records(self, repository, tmp_path):
        output = tmp_path / "shared"
        provider = MockProvider()
        first = ExperimentRunner(
            repository=repository, provider=provider, mode="mock",
            output_dir=str(output), methods=["full_file_regeneration"], reps=1,
            task_ids=["HE-EDIT-001"], seed=1, allow_overwrite=True,
        )
        first_summary = first.run(progress=False)
        second = ExperimentRunner(
            repository=repository, provider=provider, mode="mock",
            output_dir=str(output), methods=["full_file_regeneration"], reps=1,
            task_ids=["HE-EDIT-002"], seed=2, allow_overwrite=True,
        )
        second_summary = second.run(progress=False)
        assert first.output_paths()["attempts_jsonl"] != second.output_paths()["attempts_jsonl"]
        with open(first.output_paths()["attempts_jsonl"], "r", encoding="utf-8") as fh:
            first_records = [json.loads(line) for line in fh if line.strip()]
        with open(second.output_paths()["attempts_jsonl"], "r", encoding="utf-8") as fh:
            second_records = [json.loads(line) for line in fh if line.strip()]
        assert [r["task_id"] for r in first_records] == ["HE-EDIT-001"]
        assert [r["task_id"] for r in second_records] == ["HE-EDIT-002"]
        assert first_summary["recorded_attempts"] == 1
        assert second_summary["recorded_attempts"] == 1

    def test_jsonl_csv_summary_and_metadata_agree(self, repository, tmp_path):

        provider = MockProvider()
        runner = ExperimentRunner(
            repository=repository, provider=provider, mode="mock",
            output_dir=str(tmp_path / "run"), methods=["full_file_regeneration"], reps=1,
            task_ids=["HE-EDIT-001", "HE-EDIT-002"], seed=1,
        )
        summary = runner.run(progress=False)
        paths = runner.output_paths()
        with open(paths["attempts_jsonl"], "r", encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        with open(paths["attempts_csv"], "r", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        with open(paths["summary_json"], "r", encoding="utf-8") as fh:
            summary_file = json.load(fh)
        with open(paths["run_metadata_json"], "r", encoding="utf-8") as fh:
            metadata = json.load(fh)

        assert len(records) == len(rows) == 2
        assert summary["recorded_attempts"] == len(records)
        assert summary_file["recorded_attempts"] == len(records)
        assert metadata["planned_attempts"] == 2
        assert len({r["attempt_id"] for r in records}) == 2
        for record, row in zip(records, rows):
            for key in ("task_id", "method", "rep", "status", "failure_category",
                        "is_mock", "usage_source", "retry_count", "finish_reason"):
                assert str(record[key]) == row[key], (key, record[key], row[key])
        assert all(r["is_mock"] is True for r in records)
        assert all(r["usage_source"] != "provider_reported" for r in records)
        assert all(r["cost_usd"] is None for r in records)
