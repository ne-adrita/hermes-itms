"""Metrics, CLI and mock end-to-end tests (fully offline)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.analysis import (  # noqa: E402
    bootstrap_ci,
    method_metrics,
    paired_task_comparison,
    summarise_attempts,
)
from benchmarks.benchmark.records import (  # noqa: E402
    AttemptRecord,
    AttemptStatus,
    FailureCategory,
)

RUNNER = os.path.join("benchmarks", "benchmark_runner.py")


def _record(**kwargs) -> AttemptRecord:
    defaults = dict(
        attempt_id=kwargs.pop("attempt_id", "a" * 32),
        run_id="run-1",
        timestamp_utc="2026-01-01T00:00:00Z",
        task_id="HE-EDIT-001",
        humaneval_task_id="HumanEval/0",
        method="full_file_regeneration",
        rep=1,
        mode="live",
        provider="openrouter",
        requested_model_id="vendor/model",
    )
    defaults.update(kwargs)
    return AttemptRecord(**defaults)


class TestMetricDenominators:

    def test_denominators_are_explicit(self):
        records = [
            _record(status=AttemptStatus.REPAIR_SUCCESS, test_passed=True, syntax_valid=True),
            _record(attempt_id="b" * 32, status=AttemptStatus.REPAIR_FAILED, test_passed=False, syntax_valid=True),
            _record(attempt_id="c" * 32, status=AttemptStatus.OUTPUT_PARSE_ERROR),
            _record(attempt_id="d" * 32, status=AttemptStatus.PROVIDER_ERROR),
        ]
        metrics = method_metrics(
            records, scheduled=10, method="full_file_regeneration", tasks_count=5, reps=2
        )
        denominators = metrics["denominators"]
        assert denominators["scheduled_attempts"] == 10
        assert denominators["recorded_attempts"] == 4
        assert denominators["candidate_produced_attempts"] == 2
        assert metrics["repair_success"]["count"] == 1
        assert metrics["repair_success"]["rate_over_scheduled"] == pytest.approx(0.1)
        assert metrics["repair_success"]["rate_over_attempted"] == pytest.approx(1 / 3)
        assert metrics["syntax_valid"]["rate_over_candidates"] == pytest.approx(1.0)
        assert metrics["test_pass"]["rate_over_candidates"] == pytest.approx(0.5)
        assert metrics["provider_failures"]["count"] == 1
        assert metrics["malformed_output"]["count"] == 1

    def test_missing_usage_is_reported_as_missing(self):
        records = [_record(status=AttemptStatus.REPAIR_SUCCESS, test_passed=True)]
        metrics = method_metrics(
            records, scheduled=1, method="full_file_regeneration", tasks_count=1, reps=1
        )
        assert metrics["token_usage"]["provider_reported_attempts"] == 0
        assert metrics["token_usage"]["total_tokens_total"] is None
        assert metrics["cost"]["total_cost_usd"] is None
        assert metrics["cost"]["missing_attempts"] == 1

    def test_provider_reported_usage_is_summarised(self):
        records = [
            _record(
                status=AttemptStatus.REPAIR_SUCCESS,
                test_passed=True,
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                usage_source="provider_reported",
                cost_usd=0.002,
                cost_source="provider_reported",
            )
        ]
        metrics = method_metrics(
            records, scheduled=1, method="full_file_regeneration", tasks_count=1, reps=1
        )
        assert metrics["token_usage"]["provider_reported_attempts"] == 1
        assert metrics["token_usage"]["total_tokens_total"] == 120
        assert metrics["cost"]["total_cost_usd"] == pytest.approx(0.002)

    def test_task_level_view_counts_a_task_once(self):
        records = [
            _record(rep=1, status=AttemptStatus.REPAIR_FAILED, test_passed=False),
            _record(rep=2, status=AttemptStatus.REPAIR_SUCCESS, test_passed=True),
            _record(rep=3, status=AttemptStatus.REPAIR_SUCCESS, test_passed=True),
        ]
        metrics = method_metrics(
            records, scheduled=3, method="full_file_regeneration", tasks_count=1, reps=3
        )
        assert metrics["task_level"]["tasks_with_at_least_one_success"] == 1
        assert metrics["task_level"]["mean_task_pass_rate"] == pytest.approx(2 / 3)


class TestMockContaminationDetection:

    def test_mock_results_are_flagged(self):
        records = [_record(is_mock=True, status=AttemptStatus.REPAIR_SUCCESS, test_passed=True)]
        summary = summarise_attempts(records)
        assert summary["contains_mock_results"] is True
        assert summary["is_measurement"] is False
        assert "MOCK" in summary["mock_warning"]

    def test_live_records_are_not_flagged_as_mock(self):
        records = [_record(is_mock=False, status=AttemptStatus.REPAIR_SUCCESS, test_passed=True)]
        summary = summarise_attempts(records)
        assert summary["contains_mock_results"] is False
        assert summary["is_measurement"] is True


class TestPairedComparison:

    def test_paired_difference_and_bootstrap(self):
        records = []
        for task_index in range(5):
            task_id = f"HE-EDIT-{task_index + 1:03d}"
            for rep in range(2):
                records.append(
                    _record(
                        attempt_id=f"{task_index}{rep}a",
                        task_id=task_id,
                        method="full_file_regeneration",
                        rep=rep,
                        status=AttemptStatus.REPAIR_SUCCESS,
                        test_passed=True,
                    )
                )
                records.append(
                    _record(
                        attempt_id=f"{task_index}{rep}b",
                        task_id=task_id,
                        method="search_replace_patch",
                        rep=rep,
                        status=AttemptStatus.REPAIR_FAILED,
                        test_passed=False,
                    )
                )
        comparison = paired_task_comparison(
            records,
            "full_file_regeneration",
            "search_replace_patch",
            [f"HE-EDIT-{i + 1:03d}" for i in range(5)],
        )
        assert comparison["paired_tasks"] == 5
        assert comparison["mean_difference_a_minus_b"] == pytest.approx(1.0)
        assert comparison["task_wins_a"] == 5
        assert comparison["task_losses_a"] == 0
        assert "not a HumanEval pass rate" in comparison["caveat"]

    def test_bootstrap_ci_is_deterministic(self):
        values = [0.0, 1.0, 1.0, 0.0, 1.0]
        first = bootstrap_ci(values, seed=42)
        second = bootstrap_ci(values, seed=42)
        assert first == second
        assert first["ci_low"] <= first["mean"] <= first["ci_high"]


class TestCommandLineInterface:

    def _run(self, *args, expect_success=True):
        result = subprocess.run(
            [sys.executable, RUNNER, *args],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            input="",
        )
        if expect_success:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def test_dry_run_plans_300_attempts_without_calling_a_provider(self):
        result = self._run("--dry-run", "--mode", "mock", "--reps", "5")
        assert "planned attempts    : 300" in result.stdout
        assert "no provider contacted" in result.stdout

    def test_default_mode_is_mock(self):
        result = self._run("--dry-run")
        assert "mode                : mock" in result.stdout

    def test_live_mode_without_credentials_fails_closed(self):
        env = dict(os.environ)
        env["OPENROUTER_API_KEY"] = ""
        env["OPENAI_API_KEY"] = ""
        result = subprocess.run(
            [sys.executable, RUNNER, "--mode", "live", "--provider", "openrouter",
             "--model", "vendor/model", "--max-attempts", "3", "--dry-run"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        assert result.returncode == 2
        assert "fail-closed" in result.stdout + result.stderr

    def test_live_mode_without_model_id_fails_closed(self):
        result = subprocess.run(
            [sys.executable, RUNNER, "--mode", "live", "--provider", "openrouter",
             "--max-attempts", "3", "--dry-run"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 2
        assert "model identifier" in (result.stdout + result.stderr)

    def test_mock_smoke_run_produces_artifacts(self, tmp_path):
        output = str(tmp_path / "mock_smoke")
        result = self._run(
            "--mode", "mock",
            "--tasks", "HE-EDIT-001,HE-EDIT-006",
            "--reps", "1",
            "--output-dir", output,
        )
        assert "MOCK RUN" in result.stdout
        assert os.path.exists(os.path.join(output, "attempts.jsonl"))
        assert os.path.exists(os.path.join(output, "attempts.csv"))
        assert os.path.exists(os.path.join(output, "summary.json"))
        assert os.path.exists(os.path.join(output, "run_metadata.json"))

        with open(os.path.join(output, "summary.json"), "r", encoding="utf-8") as fh:
            summary = json.load(fh)
        assert summary["mode"] == "mock"
        assert summary["is_measurement"] is False
        assert summary["mock_warning"]
        assert summary["recorded_attempts"] == 6

        with open(os.path.join(output, "attempts.jsonl"), "r", encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        assert len(records) == 6
        assert all(record["is_mock"] is True for record in records)
        assert all(record["usage_source"] != "provider_reported" for record in records)
        assert all(record["cost_usd"] is None for record in records)

    def test_existing_output_directory_is_refused(self, tmp_path):
        output = str(tmp_path / "existing")
        os.makedirs(output)
        with open(os.path.join(output, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("do not overwrite me")
        result = self._run(
            "--mode", "mock", "--tasks", "HE-EDIT-001", "--reps", "1",
            "--output-dir", output,
            expect_success=False,
        )
        assert result.returncode == 2
        with open(os.path.join(output, "keep.txt"), "r", encoding="utf-8") as fh:
            assert fh.read() == "do not overwrite me"

    def test_no_inference_call_happens_in_mock_mode(self, tmp_path):
        """
        The mock engine is the only provider reachable in mock mode, so a mock
        run cannot produce a live-looking record.
        """
        output = str(tmp_path / "mock_only")
        self._run(
            "--mode", "mock", "--tasks", "HE-EDIT-001", "--reps", "1",
            "--output-dir", output,
        )
        with open(os.path.join(output, "attempts.jsonl"), "r", encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        assert records
        for record in records:
            assert record["is_mock"] is True
            assert record["provider"] == "deterministic-mock"
