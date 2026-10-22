"""
Offline safety tests for the Hermes/ITMS benchmark harness.

These tests guarantee that a benchmark run can never silently present mock
output as a live measurement, that live-mode failures are recorded as
failures, that unmeasured telemetry stays None, and that results are
durably persisted.

Every test is fully offline: no network calls, no API credits, no secrets.
"""

import csv
import json
import os
import tempfile
import unittest
from unittest import mock

import httpx

from benchmark_harness import (
    BenchmarkRunRecord,
    MacroBenchmarkSuite,
    MockContaminationError,
    assert_live_measurement,
    export_run_records,
)
from model_dispatcher import (
    LiveModeError,
    ModelDispatcher,
    QuotaExceededError,
)


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


class _StubClient:
    """Stub httpx.Client that never touches the network."""

    calls = []
    status_code = 200
    response_payload = {}
    response_headers = {}
    raise_on_post = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, url, json=None, headers=None, **kwargs):
        type(self).calls.append({"url": url, "json": json, "headers": headers or {}})
        if type(self).raise_on_post is not None:
            raise type(self).raise_on_post
        return _StubResponse(
            type(self).response_payload,
            type(self).status_code,
            dict(type(self).response_headers),
        )

    def get(self, url, headers=None, **kwargs):
        return _StubResponse(
            {
                "data": [
                    {
                        "id": "openrouter/free",
                        "supported_parameters": ["max_tokens", "temperature"],
                        "endpoints": None,
                    }
                ]
            },
            200,
            {},
        )


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


class TestMockContaminationGuard(unittest.TestCase):
    """A mock-derived report must never be publishable as a live measurement."""

    def test_mock_results_are_flagged(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY=None,
            OPENAI_API_KEY=None,
        ):
            report = MacroBenchmarkSuite.run_suite()
        self.assertTrue(report.contains_mock_results)
        self.assertTrue(any(r.is_mock for r in report.run_records))

    def test_assert_live_measurement_rejects_mock_report(self):
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY=None,
            OPENAI_API_KEY=None,
        ):
            report = MacroBenchmarkSuite.run_suite()
        with self.assertRaises(MockContaminationError) as ctx:
            assert_live_measurement(report)
        self.assertIn("NOT a live measurement", str(ctx.exception))

    def test_forced_mock_run_is_rejected_as_measurement(self):
        report = MacroBenchmarkSuite.run_suite(force_mock=True)
        self.assertTrue(report.contains_mock_results)
        with self.assertRaises(MockContaminationError):
            assert_live_measurement(report)

    def test_mock_run_does_not_report_a_passing_pass_rate(self):
        """The pass rate of a mock run must not be silently presentable."""
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY=None,
            OPENAI_API_KEY=None,
        ):
            report = MacroBenchmarkSuite.run_suite()
        # The raw aggregate still exists and is a real number, but the guard
        # blocks its publication. The mock engine does not repair the
        # approved Phase 0 tasks, so the rate may legitimately be 0 %.
        self.assertIsInstance(report.pass_rate_pct, float)
        self.assertGreaterEqual(report.pass_rate_pct, 0.0)
        self.assertLessEqual(report.pass_rate_pct, 100.0)
        self.assertTrue(report.contains_mock_results)
        with self.assertRaises(MockContaminationError):
            assert_live_measurement(report)


class TestRunRecordFields(unittest.TestCase):
    """Every run must record the required audit fields."""

    def _record(self):
        return BenchmarkRunRecord(
            system="ITMS", task_id="T1", task_name="n", category="c", rep=1,
            model="openrouter/free", passed=True, syntax_valid=True, timed_out=False,
            error_type=None, api_error=None, api_error_category=None, http_status=None,
            duration_ms=1.0, input_tokens=10, output_tokens=5, total_tokens=15,
            cost_usd=0.0, is_mock=False, escalated_to_tier2=False,
            tier1_surgical_repairs=0,
        )

    def test_required_fields_present(self):
        d = self._record().to_dict()
        for field in (
            "system", "task_id", "rep", "model", "passed", "syntax_valid",
            "timed_out", "error_type", "api_error", "api_error_category",
            "http_status", "duration_ms", "input_tokens", "output_tokens",
            "total_tokens", "cost_usd", "is_mock",
        ):
            self.assertIn(field, d, f"missing required field: {field}")

    def test_unknown_values_are_none_not_zero(self):
        """Unmeasured token counts and costs must be None, never 0."""
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY=None,
            OPENAI_API_KEY=None,
        ):
            report = MacroBenchmarkSuite.run_suite()
        for r in report.run_records:
            if r.is_mock:
                self.assertIsNone(r.total_tokens)
                self.assertIsNone(r.cost_usd)
                self.assertIsNone(r.input_tokens)
                self.assertIsNone(r.output_tokens)

    def test_live_failure_is_recorded_as_failure_not_mock(self):
        """
        A live-mode failure must be recorded as a FAILED run carrying the error,
        never as a mock pass.
        """
        _StubClient.calls = []
        _StubClient.raise_on_post = None
        _StubClient.status_code = 429
        _StubClient.response_payload = {
            "error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429}
        }
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            report = MacroBenchmarkSuite.run_suite(
                tier1_model="openrouter/free",
                tier2_model="openrouter/free",
                strict_live=True,
            )

        self.assertEqual(len(report.run_records), 20)
        self.assertEqual(report.live_request_failures, 20)
        self.assertEqual(report.passed_tasks, 0)
        self.assertFalse(report.contains_mock_results)
        for r in report.run_records:
            self.assertFalse(r.passed, "a live failure must not be recorded as a pass")
            self.assertFalse(r.is_mock, "a live failure must not be recorded as a mock")
            self.assertIsNotNone(r.api_error)
            self.assertEqual(r.api_error_category, "quota_exceeded")
            self.assertEqual(r.http_status, 429)
            self.assertIsNone(r.total_tokens)
            self.assertIsNone(r.cost_usd)

    def test_deliberate_mock_run_is_flagged_as_mock(self):
        """A force_mock run is a deliberate mock and must be flagged as such."""
        report = MacroBenchmarkSuite.run_suite(force_mock=True)
        self.assertTrue(report.contains_mock_results)
        for r in report.run_records:
            self.assertTrue(r.is_mock)
            self.assertIn("not a live measurement", r.notes)


class TestLiveFailureClassification(unittest.TestCase):
    """Auth, timeout, quota and provider failures must be distinguishable."""

    def _generate_with(self, status_code=None, exc=None):
        _StubClient.calls = []
        _StubClient.raise_on_post = exc
        _StubClient.status_code = status_code or 200
        _StubClient.response_headers = {}
        if status_code == 429:
            _StubClient.response_payload = {
                "error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429}
            }
        elif status_code == 401:
            _StubClient.response_payload = {
                "error": {"message": "Invalid credentials", "code": 401}
            }
        else:
            _StubClient.response_payload = {}
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            with self.assertRaises(Exception) as ctx:
                ModelDispatcher(strict_live=True).generate(
                    prompt="x", model_name="openrouter/free", is_tier1=True
                )
        return ctx.exception

    def test_auth_error_is_categorised(self):
        err = self._generate_with(status_code=401)
        self.assertIsInstance(err, LiveModeError)
        self.assertEqual(err.category, "auth_error")
        self.assertEqual(err.status_code, 401)

    def test_quota_error_is_categorised(self):
        err = self._generate_with(status_code=429)
        self.assertIsInstance(err, QuotaExceededError)
        self.assertEqual(err.category, "quota_exceeded")
        self.assertEqual(err.status_code, 429)

    def test_provider_error_is_categorised(self):
        err = self._generate_with(status_code=500)
        self.assertIsInstance(err, LiveModeError)
        self.assertEqual(err.category, "provider_error")
        self.assertEqual(err.status_code, 500)

    def test_timeout_is_categorised(self):
        err = self._generate_with(exc=httpx.ConnectTimeout("timed out"))
        self.assertIsInstance(err, LiveModeError)
        self.assertEqual(err.category, "timeout")

    def test_network_error_is_categorised(self):
        err = self._generate_with(exc=httpx.ConnectError("refused"))
        self.assertIsInstance(err, LiveModeError)
        self.assertEqual(err.category, "network_error")

    def test_non_strict_app_mode_keeps_mock_fallback_but_flags_it(self):
        """
        Non-strict mode preserves the app's documented mock fallback, but the
        response is flagged is_mock=True so it can never be mistaken for a live
        result. The benchmark never uses this path (see strict-live tests).
        """
        _StubClient.calls = []
        _StubClient.raise_on_post = None
        _StubClient.status_code = 401
        _StubClient.response_payload = {"error": {"message": "Invalid credentials", "code": 401}}
        with _EnvGuard(
            OPENAI_BASE_URL=None,
            OPENROUTER_BASE_URL=None,
            OPENROUTER_API_KEY="or-test-key",
            OPENAI_API_KEY=None,
        ), mock.patch("model_dispatcher.httpx.Client", _StubClient):
            resp = ModelDispatcher(strict_live=False).generate(
                prompt="x", model_name="openrouter/free", is_tier1=True
            )
        self.assertTrue(resp.is_mock, "a non-live response must be flagged is_mock=True")


class TestFreeRouteGuards(unittest.TestCase):
    """
    The free-route guards: a paid model must be refused before any attempt,
    and a spend cap must stop the run.
    """

    def _run_cli(self, *args, env=None):
        import subprocess
        import sys as _sys

        root = os.path.dirname(os.path.abspath(__file__))
        environment = dict(os.environ)
        if env:
            environment.update(env)
        return subprocess.run(
            [_sys.executable, "-m", "benchmark_harness", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
            env=environment,
        )

    def test_require_free_model_refuses_a_paid_model(self):
        """A model that is not on the free route must stop the run."""
        import benchmark_harness

        calls = []

        def _never(*args, **kwargs):
            calls.append(kwargs)
            raise AssertionError("the pipeline must not be started")

        with mock.patch.object(
            benchmark_harness.RelayOrchestrator, "run_cascade", _never
        ):
            with self.assertRaises(ValueError) as ctx:
                MacroBenchmarkSuite.run_suite(
                    force_mock=True,
                    require_free_model=True,
                    tier1_model="minimax/minimax-m3.1",
                    tier2_model="minimax/minimax-m3.1",
                )
        self.assertIn(":free", str(ctx.exception))
        self.assertEqual(calls, [], "no attempt may be made")

    def test_require_free_model_accepts_a_free_model(self):
        """A free model is accepted and the run produces its records."""
        report = MacroBenchmarkSuite.run_suite(
            force_mock=True,
            require_free_model=True,
            tier1_model="minimax/minimax-m3.1-flash:free",
            tier2_model="minimax/minimax-m3.1-flash:free",
        )
        self.assertEqual(len(report.run_records), 20)

    def test_spend_cap_stops_the_run(self):
        """The spend cap must stop the run and mark the rest as not executed."""
        import benchmark_harness

        class _Resp:
            is_mock = False
            raw_tokens_in = 10
            output_tokens = 10

        class _Res:
            tier1_response = _Resp()
            tier2_response = None
            tier1_result = None
            tier2_result = None
            verified_module = ""
            final_success = False
            escalated_to_tier2 = False
            tier1_surgical_repairs = 0
            total_pipeline_cost_usd = 0.10
            direct_frontier_cost_usd = 0.0
            naive_agentic_cost_usd = 0.0
            cost_saved_vs_frontier_pct = 0.0
            total_tokens_consumed = 20
            total_duration_ms = 1.0

        with mock.patch.object(
            benchmark_harness.RelayOrchestrator, "run_cascade",
            return_value=_Res(),
        ):
            report = MacroBenchmarkSuite.run_suite(
                force_mock=True,
                max_spend_usd=0.30,
            )

        executed = [r for r in report.run_records if r.executed]
        skipped = [r for r in report.run_records if not r.executed]
        self.assertLessEqual(len(executed), 3)
        self.assertEqual(len(executed) + len(skipped), 20)
        for record in skipped:
            self.assertEqual(record.notes, "spend cap reached")
            self.assertFalse(record.passed)

    def test_cli_rejects_paid_model_when_free_required(self):
        """The CLI must exit 1 and make no provider request."""
        without_keys = {
            "OPENAI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "GEMINI_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "OPENAI_BASE_URL": "",
            "OPENROUTER_BASE_URL": "",
        }
        result = self._run_cli(
            "--require-free-model",
            "--live",
            "--tier1-model", "minimax/minimax-m3.1",
            env=without_keys,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("free route", result.stderr)
        self.assertIn("No request was made", result.stderr)


class TestProgressDisplay(unittest.TestCase):
    """A long run must show that it is alive."""

    def test_progress_lines_are_printed(self):
        """The progress summary must be printed while the run happens."""
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            MacroBenchmarkSuite.run_suite(force_mock=True)
        output = buffer.getvalue()

        self.assertIn("[HE-EDIT-001] rep 1/1", output)
        summaries = [
            line for line in output.splitlines()
            if line.startswith("--- progress:")
        ]
        self.assertTrue(summaries, "no progress summary was printed")
        summary = summaries[0]
        self.assertIn("pass", summary)
        self.assertIn("fail", summary)
        # The progress display must never leak a token count or a cost.
        self.assertNotIn("$", summary)

    def test_the_progress_summary_is_printed_every_ten_records(self):
        """The summary cadence is one line per ten records."""
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            MacroBenchmarkSuite.run_suite(force_mock=True)
        summaries = [
            line for line in buffer.getvalue().splitlines()
            if line.startswith("--- progress:")
        ]
        self.assertEqual(len(summaries), 2)
        self.assertIn("10/20", summaries[0])
        self.assertIn("20/20", summaries[1])


class TestBoundedRetryPolicy(unittest.TestCase):
    """The free route must not spend its quota on self-repair retries."""

    def test_free_route_default_disables_self_repair(self):
        """
        Without an explicit retry budget a run makes exactly one tier-1
        attempt (no self-repair) and at most one tier-2 escalation, i.e. 2
        provider calls instead of the 4 of the old worst case.
        """
        report = MacroBenchmarkSuite.run_suite(force_mock=True)
        self.assertEqual(len(report.run_records), 20)
        for record in report.run_records:
            self.assertEqual(
                record.tier1_surgical_repairs, 0,
                "the default must not spend self-repair attempts",
            )
            tier2_calls = 1 if record.escalated_to_tier2 else 0
            tier1_calls = record.provider_attempts - tier2_calls
            self.assertEqual(
                tier1_calls, 1,
                "a run must make exactly one tier-1 attempt by default",
            )
            self.assertLessEqual(record.provider_attempts, 2)

    def test_cli_max_tier1_retries_is_honoured(self):
        """The CLI's --max-tier1-retries must reach the pipeline."""
        import benchmark_harness

        captured = {}

        class _Resp:
            is_mock = True
            raw_tokens_in = 1
            output_tokens = 1

        class _Res:
            tier1_response = _Resp()
            tier2_response = None
            tier1_result = None
            tier2_result = None
            verified_module = ""
            final_success = False
            escalated_to_tier2 = False
            tier1_surgical_repairs = 0
            total_pipeline_cost_usd = 0.0
            direct_frontier_cost_usd = 0.0
            naive_agentic_cost_usd = 0.0
            cost_saved_vs_frontier_pct = 0.0
            total_tokens_consumed = 2
            total_duration_ms = 1.0

        def _fake_run_cascade(self, *args, **kwargs):
            captured.update(kwargs)
            return _Res()

        with mock.patch.object(
            benchmark_harness.RelayOrchestrator, "run_cascade", _fake_run_cascade
        ):
            exit_code = benchmark_harness.main([
                "--tasks", "HE-EDIT-001",
                "--reps", "1",
                "--max-tier1-retries", "3",
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(captured.get("max_tier1_retries"), 3)
        self.assertFalse(captured.get("enable_tier1_self_repair"))


class TestBoundedOutput(unittest.TestCase):
    """Every provider response must have a bounded size."""

    def test_max_output_tokens_is_forwarded(self):
        """The harness must forward max_output_tokens to the dispatcher."""
        original = ModelDispatcher.generate
        seen = []

        def _spy(self, *args, **kwargs):
            seen.append(kwargs)
            return original(self, *args, **kwargs)

        with mock.patch.object(ModelDispatcher, "generate", _spy):
            MacroBenchmarkSuite.run_suite(
                force_mock=True,
                max_output_tokens=4096,
            )

        self.assertTrue(seen, "the dispatcher was never called")
        for kwargs in seen:
            self.assertEqual(kwargs.get("max_output_tokens"), 4096)


class TestStopOnQuota(unittest.TestCase):
    """
    A quota-exceeded failure must stop the run instead of burning the rest
    of the daily quota on failures that cannot succeed.
    """

    def test_stop_on_quota_halts_the_run(self):
        """The first quota failure stops the run; the rest is not executed."""
        import benchmark_harness

        calls = []

        def _quota(*args, **kwargs):
            calls.append(kwargs)
            raise QuotaExceededError("free-models-per-day", status_code=429)

        with mock.patch.object(
            benchmark_harness.RelayOrchestrator, "run_cascade", _quota
        ):
            report = MacroBenchmarkSuite.run_suite(
                force_mock=False,
                strict_live=True,
                stop_on_quota=True,
                tier1_model="minimax/minimax-m3.1-flash:free",
                tier2_model="minimax/minimax-m3.1-flash:free",
            )

        executed = [r for r in report.run_records if r.executed]
        skipped = [r for r in report.run_records if not r.executed]
        self.assertEqual(len(executed), 1)
        self.assertEqual(len(executed) + len(skipped), 20)
        self.assertEqual(len(calls), 1, "no further request may be made")
        # The quota failure itself is recorded as a failure, never a mock.
        self.assertFalse(executed[0].passed)
        self.assertFalse(executed[0].is_mock)
        self.assertEqual(executed[0].api_error_category, "quota_exceeded")
        self.assertEqual(report.live_request_failures, 1)
        for record in skipped:
            self.assertEqual(record.notes, "quota exhausted")
            self.assertFalse(record.passed)

    def test_the_cli_accepts_the_stop_on_quota_flag(self):
        """--stop-on-quota must be a valid CLI flag."""
        import benchmark_harness

        with mock.patch.object(
            benchmark_harness, "_print_dry_run", mock.Mock(return_value=0)
        ) as _dry:
            exit_code = benchmark_harness.main([
                "--dry-run", "--stop-on-quota", "--reps", "1",
            ])
        self.assertEqual(exit_code, 0)
        self.assertTrue(_dry.called)


class TestResultExport(unittest.TestCase):
    """Results must be durably persisted to JSON and CSV."""

    def test_export_writes_json_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = MacroBenchmarkSuite.run_suite(force_mock=True, output_dir=tmp)
            self.assertIn("json", report.output_files)
            self.assertIn("csv", report.output_files)

            with open(report.output_files["json"], encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(len(payload), len(report.run_records))
            self.assertIn("is_mock", payload[0])
            self.assertIn("rep", payload[0])

            with open(report.output_files["csv"], encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), len(report.run_records))
            for field in ("system", "task_id", "rep", "is_mock", "cost_usd", "total_tokens"):
                self.assertIn(field, rows[0])

    def test_export_of_empty_record_set_still_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = export_run_records([], output_dir=tmp, basename="empty")
            self.assertTrue(os.path.exists(paths["json"]))
            self.assertTrue(os.path.exists(paths["csv"]))


class TestRepetitionsAndSystems(unittest.TestCase):
    """Repetitions and system labelling must be supported."""

    def test_reps_produce_one_record_per_task_per_rep(self):
        report = MacroBenchmarkSuite.run_suite(force_mock=True, reps=3)
        self.assertEqual(len(report.run_records), 60)
        self.assertEqual(len({r.rep for r in report.run_records}), 3)
        self.assertEqual(len({r.task_id for r in report.run_records}), 20)

    def test_system_label_is_recorded(self):
        report = MacroBenchmarkSuite.run_suite(force_mock=True, system="Aider")
        for r in report.run_records:
            self.assertEqual(r.system, "Aider")


class TestOfficialEvaluatorIsUsed(unittest.TestCase):
    """
    The benchmark's verdict must come from the official Phase 0 evaluator.

    The legacy ``ExecutionHarness`` gate only isolates a process boundary:
    it does not block sockets and does not confine the file system. The
    official evaluator does, and it is the same evaluator the Phase 0 task
    set was validated with.
    """

    def test_official_test_is_used_as_the_evaluator(self):
        """The official evaluator must run once per attempt."""
        import benchmark_harness

        calls = []

        class _Outcome:
            passed = True
            error_type = None
            error_message = None
            duration_ms = 12.5
            timed_out = False

        def _fake_run_official_test(source_code, test_code, entry_point, timeout=None):
            calls.append({
                "source_code": source_code,
                "test_code": test_code,
                "entry_point": entry_point,
                "timeout": timeout,
            })
            return _Outcome()

        with mock.patch.object(
            benchmark_harness, "run_official_test", _fake_run_official_test
        ):
            report = MacroBenchmarkSuite.run_suite(force_mock=True)

        # One call per recorded attempt.
        self.assertEqual(len(calls), len(report.run_records))
        self.assertEqual(len(calls), 20)
        # The official test and the entry point must be the task's own.
        by_test_source = {
            t.official_test_source: t for t in MacroBenchmarkSuite.get_canonical_tasks()
        }
        for call in calls:
            task = by_test_source.get(call["test_code"])
            self.assertIsNotNone(task, "the official test is not a task's own")
            self.assertEqual(call["entry_point"], task.entry_point)
            self.assertIn("def check(", call["test_code"])
            # The check call is added by the evaluator, never by the task.
            self.assertNotIn(f"check({call['entry_point']})", call["test_code"])

    def test_the_record_carries_the_official_evaluators_verdict(self):
        """passed / error_type / error_message / duration_ms come from the
        official evaluator."""
        import benchmark_harness

        class _Outcome:
            passed = False
            error_type = "AssertionError"
            error_message = "assert 1 == 2"
            duration_ms = 42.0
            timed_out = False

        with mock.patch.object(
            benchmark_harness, "run_official_test", lambda *a, **k: _Outcome()
        ):
            report = MacroBenchmarkSuite.run_suite(force_mock=True)

        self.assertEqual(len(report.run_records), 20)
        for record in report.run_records:
            self.assertFalse(record.passed)
            self.assertEqual(record.error_type, "AssertionError")
            self.assertEqual(record.error_message, "assert 1 == 2")
            self.assertEqual(record.duration_ms, 42.0)
            self.assertEqual(report.passed_tasks, 0)

    def test_a_candidate_without_an_official_verdict_is_not_a_pass(self):
        """No evaluable module must never be recorded as a successful repair."""
        import benchmark_harness

        with mock.patch.object(
            benchmark_harness, "run_official_test", lambda *a, **k: None
        ):
            report = MacroBenchmarkSuite.run_suite(force_mock=True)

        self.assertEqual(report.passed_tasks, 0)
        self.assertEqual(report.pass_rate_pct, 0.0)
        for record in report.run_records:
            self.assertFalse(record.passed)
            self.assertEqual(record.error_type, "NoEvaluableCandidate")
            self.assertIsNotNone(record.error_message)


class TestBenchmarkCli(unittest.TestCase):
    """
    The command line entry point of the macro benchmark.

    The CLI is the operator's handle on a *measurement*, so it must fail
    closed: a run that was not a live measurement has to leave with a
    non-zero exit code, and a dry run must contact nobody.
    """

    def _run_cli(self, *args, env=None):
        import subprocess
        import sys as _sys

        root = os.path.dirname(os.path.abspath(__file__))
        environment = dict(os.environ)
        if env:
            environment.update(env)
        return subprocess.run(
            [_sys.executable, "-m", "benchmark_harness", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
            env=environment,
        )

    def test_the_dry_run_prints_the_schedule_and_contacts_no_provider(self):
        """--dry-run must show the whole schedule without a single request."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "must_not_be_created")
            result = self._run_cli(
                "--dry-run", "--reps", "5", "--output-dir", out_dir
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("planned runs        : 100", result.stdout)
            self.assertIn("No provider was contacted", result.stdout)
            self.assertIn("HE-EDIT-001", result.stdout)
            self.assertIn("HE-EDIT-020", result.stdout)
            # 20 tasks x 5 repetitions are listed.
            self.assertEqual(result.stdout.count("  rep "), 100)
            # Nothing was written.
            self.assertFalse(os.path.exists(out_dir))

    def test_the_live_cli_exits_2_when_the_run_is_not_a_measurement(self):
        """
        A live run that could not produce live measurements must exit 2.

        Every provider key is removed, so the dispatcher fails closed before
        any request: the run records failures, the guard refuses the
        report, and the CLI exits 2. No request is made.
        """
        without_keys = {
            "OPENAI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "GEMINI_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_cli(
                "--live",
                "--tasks", "HE-EDIT-001",
                "--reps", "1",
                "--output-dir", tmp,
                env=without_keys,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("NOT a live measurement", result.stderr)
            self.assertIn("live_request_failures : 1", result.stdout)

    def test_the_cli_rejects_an_unknown_task(self):
        """An unknown task id must stop the run before any request."""
        result = self._run_cli("--dry-run", "--tasks", "HE-EDIT-999")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown task id", result.stderr)

    def test_the_cli_rejects_dry_run_together_with_live(self):
        """The two modes are mutually exclusive."""
        result = self._run_cli("--dry-run", "--live")
        self.assertEqual(result.returncode, 1)
        self.assertIn("mutually exclusive", result.stderr)


class TestIncrementalPersistence(unittest.TestCase):
    """A partial run must never lose the records it already produced."""

    def test_every_record_is_written_as_it_is_produced(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = MacroBenchmarkSuite.run_suite(force_mock=True, output_dir=tmp)
            jsonl_path = report.output_files.get("jsonl")
            self.assertTrue(jsonl_path and os.path.isfile(jsonl_path))
            with open(jsonl_path, encoding="utf-8") as fh:
                lines = [line for line in fh if line.strip()]
            self.assertEqual(len(lines), len(report.run_records))
            # The JSONL and the final JSON export agree.
            with open(report.output_files["json"], encoding="utf-8") as fh:
                exported = json.load(fh)
            self.assertEqual(len(exported), len(lines))
            self.assertEqual(
                [record["task_id"] for record in exported],
                [json.loads(line)["task_id"] for line in lines],
            )

    def test_a_run_that_stops_early_keeps_what_it_produced(self):
        """A crash after the first record must not lose that record."""
        import benchmark_harness as bh

        class _Stop(Exception):
            pass

        class _Outcome:
            passed = True
            error_type = None
            error_message = None
            duration_ms = 1.0
            timed_out = False

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                bh, "_evaluate_official", side_effect=[_Outcome(), _Stop()]
            ):
                with self.assertRaises(_Stop):
                    bh.MacroBenchmarkSuite.run_suite(force_mock=True, output_dir=tmp)
            jsonl_path = os.path.join(tmp, "ITMS_runs.jsonl")
            self.assertTrue(
                os.path.isfile(jsonl_path), "the partial run wrote no records"
            )
            with open(jsonl_path, encoding="utf-8") as fh:
                lines = [line for line in fh if line.strip()]
            self.assertGreaterEqual(
                len(lines), 1, "the record produced before the stop was lost"
            )


class TestAppModuleImports(unittest.TestCase):
    """The dashboard module must import and its annotations must resolve."""

    def test_app_imports_and_its_annotations_resolve(self):
        """
        ``app.py`` annotates ``_missing_cells(...) -> List[str]``.

        Without ``from typing import List`` the annotation is evaluated when
        the function is defined, so the whole module fails to import and the
        dashboard cannot start at all.
        """
        import importlib
        import typing

        module = importlib.import_module("app")
        hints = typing.get_type_hints(module._missing_cells)
        self.assertIn("List", str(hints.get("return")))


class TestTaskInventory(unittest.TestCase):
    """The current canonical task inventory must be explicit, not assumed."""

    def test_task_count_is_twenty(self):
        """The 20 approved Phase 0 tasks are the benchmark's task set."""
        tasks = MacroBenchmarkSuite.get_canonical_tasks()
        self.assertEqual(len(tasks), 20)
        ids = [t.task_id for t in tasks]
        self.assertEqual(len(set(ids)), len(ids), "task IDs must be unique")
        for t in tasks:
            self.assertTrue(t.test_suite_code.strip(), f"{t.task_id} has no test suite")
            self.assertTrue(t.context_code.strip(), f"{t.task_id} has no context code")

    def test_tasks_come_from_the_approved_phase0_set(self):
        """
        The harness must load the approved HE-EDIT-* tasks, not an invented
        set. The task id, the entry point and the test suite have to agree
        with the Phase 0 artefacts on disk.
        """
        tasks = MacroBenchmarkSuite.get_canonical_tasks()
        expected_ids = [f"HE-EDIT-{index:03d}" for index in range(1, 21)]
        self.assertEqual([t.task_id for t in tasks], expected_ids)
        for task in tasks:
            task_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "benchmarks", "data", "tasks", task.task_id,
            )
            with open(os.path.join(task_dir, "mutated_source.py"), encoding="utf-8") as fh:
                context = fh.read()
            with open(os.path.join(task_dir, "official_test.py"), encoding="utf-8") as fh:
                official_test = fh.read()
            self.assertEqual(task.context_code, context, task.task_id)
            self.assertEqual(task.official_test_source, official_test, task.task_id)
            self.assertEqual(task.target_function_name, task.entry_point)
            # The script form carries the check call; the bare form does not.
            self.assertTrue(task.test_suite_code.startswith(official_test))
            self.assertIn(f"check({task.entry_point})", task.test_suite_code)
            self.assertNotIn(f"check({task.entry_point})", task.official_test_source)

    def test_the_harness_cannot_reach_the_reference_solutions(self):
        """
        The reference solutions must stay unreachable from the harness, so a
        benchmark can never see the answer.

        The check looks at the *code* (string literals and name references),
        not at the prose: a docstring may explain that the reference
        solutions are deliberately not read.
        """
        import ast as _ast
        import benchmark_harness

        tree = _ast.parse(inspect_source(benchmark_harness))
        docstrings = set()
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                                 _ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], _ast.Expr) and isinstance(
                    getattr(body[0], "value", None), _ast.Constant
                ) and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))

        references = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                references.append(node.value)
            elif isinstance(node, _ast.Name):
                references.append(node.id)
            elif isinstance(node, _ast.Attribute):
                references.append(node.attr)
        blob = " ".join(references)
        self.assertNotIn("gold_solutions", blob)
        self.assertNotIn("original_source", blob)

    def test_a_missing_task_set_is_a_hard_error(self):
        """A missing approved task set must stop the run, not shrink it."""
        import benchmark_harness
        with mock.patch.object(
            benchmark_harness, "_MODEL_FACING_TASKS", "/nonexistent/model_facing_tasks.json"
        ):
            with self.assertRaises(RuntimeError) as ctx:
                MacroBenchmarkSuite.get_canonical_tasks()
        self.assertIn("missing", str(ctx.exception).lower())

    def test_a_short_task_set_is_a_hard_error(self):
        """Fewer than 20 approved tasks must stop the run, not shrink it."""
        import benchmark_harness
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as tmp:
            payload_path = os.path.join(tmp, "model_facing_tasks.json")
            with open(payload_path, "w", encoding="utf-8") as fh:
                json.dump({"tasks": [{"task_id": f"HE-EDIT-{i:03d}"} for i in range(1, 20)]}, fh)
            with mock.patch.object(benchmark_harness, "_MODEL_FACING_TASKS", payload_path):
                with self.assertRaises(RuntimeError) as ctx:
                    MacroBenchmarkSuite.get_canonical_tasks()
        self.assertIn("20", str(ctx.exception))

    def test_no_humaneval_or_aider_cline_task_systems_exist(self):
        """Aider and Cline are not integrated; assert that explicitly."""
        import benchmark_harness
        src = inspect_source(benchmark_harness)
        for token in ("aider", "cline"):
            self.assertNotIn(token, src.lower(),
                             f"'{token}' unexpectedly present in benchmark_harness")


class TestSplicerNestedFunctionRegression(unittest.TestCase):
    """
    Regression guard for the nested-function splicer defect.

    Historically a body-only replacement that declared a nested function was
    mis-classified as a full-node replacement: the nested ``def`` was hoisted
    to module level and the original method body was overwritten while
    ``splice()`` still reported success.

    The defect has been fixed.  The historical benchmark runs that were
    measured against the unfixed splicer are left untouched; this test now
    pins the *correct* behaviour so the defect cannot silently return.
    """

    def test_body_only_nested_def_replacement_stays_nested(self):
        from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer
        source = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def method(self, x):\n"
            "            def helper(v):\n"
            "                return v * 2\n"
            "            return helper(x) + 1\n"
        )
        target = ASTNodeLocator.locate_target(source, "Outer.Inner.method")
        self.assertIsNotNone(target)

        replacement = (
            "    def helper(v):\n"
            "        return v * 2\n"
            "    return helper(x) + 5\n"
        )
        ok, spliced, err = ASTSurgicalSplicer.splice(source, target, replacement)

        self.assertTrue(ok, f"splice should succeed: {err}")
        self.assertIsNone(err)

        # The target's own header must survive a body-only replacement.
        self.assertIn("def method(self, x):", spliced)

        # The nested def must stay inside the method body, not at module level.
        import ast
        tree = ast.parse(spliced)
        module_level_defs = [
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        ]
        self.assertNotIn(
            "helper",
            module_level_defs,
            "the nested def must not be hoisted to module scope",
        )
        inner_cls = tree.body[0].body[0]
        method = inner_cls.body[0]
        nested = [
            node.name
            for node in method.body
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn("helper", nested)
        self.assertIn("return helper(x) + 5", spliced)

    def test_full_node_replacement_is_unaffected(self):
        """Full-node replacements (with a def header) are not affected."""
        from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer
        source = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def method(self, x):\n"
            "            def helper(v):\n"
            "                return v * 2\n"
            "            return helper(x) + 1\n"
        )
        target = ASTNodeLocator.locate_target(source, "Outer.Inner.method")
        replacement = (
            "def method(self, x):\n"
            "    def helper(v):\n"
            "        return v * 2\n"
            "    return helper(x) + 5\n"
        )
        ok, spliced, err = ASTSurgicalSplicer.splice(source, target, replacement)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIn("return helper(x) + 5", spliced)
        self.assertNotIn("def helper(v):\n\n", spliced)


def inspect_source(module) -> str:
    import inspect
    return inspect.getsource(module)


if __name__ == "__main__":
    unittest.main()
