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
        # The raw aggregate still exists, but the guard blocks its publication.
        self.assertGreater(report.pass_rate_pct, 0.0)
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

        self.assertEqual(len(report.run_records), 5)
        self.assertEqual(report.live_request_failures, 5)
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
        self.assertEqual(len(report.run_records), 15)
        self.assertEqual(len({r.rep for r in report.run_records}), 3)
        self.assertEqual(len({r.task_id for r in report.run_records}), 5)

    def test_system_label_is_recorded(self):
        report = MacroBenchmarkSuite.run_suite(force_mock=True, system="Aider")
        for r in report.run_records:
            self.assertEqual(r.system, "Aider")


class TestTaskInventory(unittest.TestCase):
    """The current canonical task inventory must be explicit, not assumed."""

    def test_task_count_is_five_not_twenty(self):
        """Documents the current reality: 5 tasks exist, 20 are required."""
        tasks = MacroBenchmarkSuite.get_canonical_tasks()
        self.assertEqual(len(tasks), 5)
        ids = [t.task_id for t in tasks]
        self.assertEqual(len(set(ids)), len(ids), "task IDs must be unique")
        for t in tasks:
            self.assertTrue(t.test_suite_code.strip(), f"{t.task_id} has no test suite")
            self.assertTrue(t.context_code.strip(), f"{t.task_id} has no context code")

    def test_no_humaneval_or_aider_cline_task_systems_exist(self):
        """Aider and Cline are not integrated; assert that explicitly."""
        import benchmark_harness
        src = inspect_source(benchmark_harness)
        for token in ("aider", "cline"):
            self.assertNotIn(token, src.lower(),
                             f"'{token}' unexpectedly present in benchmark_harness")


class TestKnownSplicerLimitation(unittest.TestCase):
    """
    Characterizes the KNOWN nested-function splicer limitation.

    This test pins the CURRENT behaviour so the bug is measurable in the
    baseline and is not silently "fixed" mid-benchmark. The defect itself is
    NOT fixed here (per the audit constraint).
    """

    def test_body_only_nested_def_replacement_is_misindented(self):
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

        # KNOWN BUG: the splice reports success but corrupts the file.
        self.assertTrue(ok, "documented current behaviour: splice reports success")
        self.assertIsNone(err)

        # The nested def is hoisted to module level (the corruption).
        self.assertTrue(
            spliced.startswith("def helper(v):"),
            "current behaviour: nested def hoisted to module level"
        )
        # The original method body was replaced by the helper def.
        self.assertIn("def helper(v):\n            return v * 2", spliced)

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
