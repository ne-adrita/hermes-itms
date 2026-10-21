"""Pipeline, runner and scheduling tests (fully offline)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.adapters import get_adapter  # noqa: E402
from benchmarks.benchmark.experiment import (  # noqa: E402
    ExperimentError,
    ExperimentRunner,
    build_schedule,
)
from benchmarks.benchmark.pipeline import AttemptPipeline  # noqa: E402
from benchmarks.benchmark.provider import (  # noqa: E402
    BenchmarkProvider,
    ProviderError,
    ProviderResult,
)
from benchmarks.benchmark.records import AttemptStatus, FailureCategory  # noqa: E402
from benchmarks.benchmark.tasks import TaskRepository, TaskRepositoryError  # noqa: E402

CORRECT_BELOW_ZERO = """from typing import List


def below_zero(operations: List[int]) -> bool:
    \"\"\" You're given a list of deposit and withdrawal operations on a bank account.
    >>> below_zero([1, 2, 3])
    False
    >>> below_zero([1, -2, 3])
    True
    \"\"\"
    balance = 0
    for op in operations:
        balance += op
        if balance < 0:
            return True

    return False
"""


class FakeProvider(BenchmarkProvider):
    """A scripted provider: returns canned content or raises a scripted error."""

    name = "fake-provider"

    def __init__(self, content=None, error=None, is_mock=False, model_id="fake/model"):
        self.content = content
        self.error = error
        self.is_mock = is_mock
        self.model_id = model_id
        self.calls = 0

    def complete(self, prompt, system_instruction=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ProviderResult(
            content=self.content or "",
            provider=self.name,
            requested_model_id=self.model_id,
            is_mock=self.is_mock,
            finish_reason="stop",
            served_model_id=self.model_id,
            prompt_tokens=10 if not self.is_mock else None,
            completion_tokens=20 if not self.is_mock else None,
            total_tokens=30 if not self.is_mock else None,
            usage_source="provider_reported" if not self.is_mock else "local_estimate_not_reported",
            latency_ms=1.0,
        )


def _pipeline(provider, method="full_file_regeneration", timeout=10.0):
    return AttemptPipeline(
        provider=provider,
        adapter=get_adapter(method),
        mode="mock" if provider.is_mock else "live",
        run_id="test-run",
        evaluation_timeout=timeout,
    )


class TestSingleAttemptPipeline:

    def test_successful_repair_is_recorded(self, simple_task):
        provider = FakeProvider(content=f"```python\n{CORRECT_BELOW_ZERO}```")
        outcome = _pipeline(provider).run(simple_task, 1)
        record = outcome.record
        assert record.status == AttemptStatus.REPAIR_SUCCESS
        assert record.test_passed is True
        assert record.syntax_valid is True
        assert record.failure_category == FailureCategory.NONE
        assert record.latency_ms is not None and record.latency_ms > 0

    def test_wrong_answer_is_recorded_as_a_failed_repair(self, simple_task):
        wrong = CORRECT_BELOW_ZERO.replace("balance < 0", "balance <= 0")
        provider = FakeProvider(content=f"```python\n{wrong}```")
        outcome = _pipeline(provider).run(simple_task, 1)
        record = outcome.record
        assert record.status == AttemptStatus.REPAIR_FAILED
        assert record.test_passed is False
        assert record.failure_category == FailureCategory.TEST_FAILED

    def test_syntax_error_is_recorded(self, simple_task):
        provider = FakeProvider(content="```python\ndef below_zero(ops)\n    return False\n```")
        outcome = _pipeline(provider).run(simple_task, 1)
        record = outcome.record
        assert record.status == AttemptStatus.REPAIR_FAILED
        assert record.syntax_valid is False
        assert record.failure_category == FailureCategory.SYNTAX_ERROR

    def test_malformed_output_is_recorded(self, simple_task):
        provider = FakeProvider(content="I would fix the comparison operator.")
        outcome = _pipeline(provider).run(simple_task, 1)
        record = outcome.record
        assert record.status == AttemptStatus.OUTPUT_PARSE_ERROR
        assert record.failure_category == FailureCategory.OUTPUT_PARSE_ERROR
        assert record.test_passed is None

    def test_provider_error_is_recorded_and_not_replaced_by_a_mock(self, simple_task):
        provider = FakeProvider(error=ProviderError("quota gone", category="quota_exceeded", status_code=429))
        outcome = _pipeline(provider).run(simple_task, 1)
        record = outcome.record
        assert record.status == AttemptStatus.PROVIDER_ERROR
        assert record.failure_category == FailureCategory.QUOTA_EXCEEDED
        assert record.is_mock is False
        assert record.test_passed is None

    def test_timeout_is_recorded(self, simple_task):
        looping = (
            "```python\ndef below_zero(operations):\n"
            "    while True:\n        pass\n```"
        )
        provider = FakeProvider(content=looping)
        outcome = _pipeline(provider, timeout=1.0).run(simple_task, 1)
        record = outcome.record
        assert record.timed_out is True
        assert record.status == AttemptStatus.REPAIR_FAILED
        assert record.failure_category == FailureCategory.TIMEOUT

    def test_no_convenience_imports_are_injected(self, simple_task):
        """
        The candidate is evaluated as-is: a name that only exists because of an
        injected convenience import must fail.
        """
        needs_typing = (
            "```python\ndef below_zero(operations: List[int]) -> bool:\n"
            "    return False\n```"
        )
        provider = FakeProvider(content=needs_typing)
        outcome = _pipeline(provider).run(simple_task, 1)
        record = outcome.record
        assert record.test_passed is False
        assert record.test_error_type is not None

    def test_temporary_workspace_is_cleaned_up(self, simple_task, tmp_path, monkeypatch):
        created = []
        import tempfile as tempfile_module

        original_mkdtemp = tempfile_module.mkdtemp

        def spy(*args, **kwargs):
            path = original_mkdtemp(*args, **kwargs)
            created.append(path)
            return path

        monkeypatch.setattr(tempfile_module, "mkdtemp", spy)
        provider = FakeProvider(content=f"```python\n{CORRECT_BELOW_ZERO}```")
        _pipeline(provider).run(simple_task, 1)
        assert created
        for path in created:
            assert not os.path.exists(path), path

    def test_every_attempt_produces_exactly_one_record(self, simple_task):
        provider = FakeProvider(content="not code")
        outcome = _pipeline(provider).run(simple_task, 3)
        assert outcome.record.attempt_id
        assert outcome.record.rep == 3
        assert outcome.record.task_id == simple_task.task_id
        assert outcome.record.method == "full_file_regeneration"


class TestTaskMapping:

    def test_unknown_task_id_is_refused(self, repository):
        with pytest.raises(TaskRepositoryError):
            repository.get("HE-EDIT-999")

    def test_task_source_matches_the_approved_file(self, repository):
        task = repository.get("HE-EDIT-001")
        with open(task.mutated_source_path, "r", encoding="utf-8") as fh:
            assert fh.read() == task.mutated_source

    def test_repository_exposes_no_gold_solution(self, repository):
        task = repository.get("HE-EDIT-001")
        assert not hasattr(task, "gold_solution")
        assert "original_source" not in task.to_dict()

    def test_manifest_digest_is_stable(self, repository):
        assert repository.manifest_digest() == repository.manifest_digest()
        assert len(repository.manifest_digest()) == 64


class TestScheduling:

    def test_full_experiment_is_300_attempts(self, repository):
        schedule = build_schedule(repository.task_ids, list(("full_file_regeneration",
                                                             "search_replace_patch",
                                                             "itms_ast_splice")), 5)
        assert len(schedule) == 300
        assert len({plan.key for plan in schedule}) == 300

    def test_every_combination_appears_exactly_once(self, repository):
        schedule = build_schedule(repository.task_ids[:3], ["full_file_regeneration"], 2)
        keys = {plan.key for plan in schedule}
        assert len(keys) == 6
        assert {plan.rep for plan in schedule} == {1, 2}

    def test_schedule_is_deterministic_for_a_seed(self, repository):
        a = build_schedule(repository.task_ids, ["full_file_regeneration"], 1, seed=7)
        b = build_schedule(repository.task_ids, ["full_file_regeneration"], 1, seed=7)
        assert [p.key for p in a] == [p.key for p in b]

    def test_different_seeds_change_the_order(self, repository):
        a = build_schedule(repository.task_ids, ["full_file_regeneration"], 1, seed=1)
        b = build_schedule(repository.task_ids, ["full_file_regeneration"], 1, seed=2)
        assert [p.key for p in a] != [p.key for p in b]


class TestExperimentRunner:

    def _runner(self, repository, provider, output_dir, **kwargs):
        return ExperimentRunner(
            repository=repository,
            provider=provider,
            mode="mock" if provider.is_mock else "live",
            output_dir=str(output_dir),
            methods=["full_file_regeneration"],
            reps=1,
            task_ids=["HE-EDIT-001", "HE-EDIT-006"],
            seed=1,
            **kwargs,
        )

    def test_run_writes_all_artifacts(self, repository, tmp_path):
        provider = FakeProvider(content="not code", is_mock=True)
        runner = self._runner(repository, provider, tmp_path / "run")
        summary = runner.run(progress=False)
        paths = runner.output_paths()
        assert os.path.exists(paths["attempts_jsonl"])
        assert os.path.exists(paths["attempts_csv"])
        assert os.path.exists(paths["summary_json"])
        assert os.path.exists(paths["run_metadata_json"])
        assert summary["recorded_attempts"] == 2
        assert summary["is_measurement"] is False
        assert summary["mock_warning"]

    def test_failed_attempts_are_kept(self, repository, tmp_path):
        provider = FakeProvider(content="not code", is_mock=True)
        runner = self._runner(repository, provider, tmp_path / "run")
        runner.run(progress=False)
        with open(runner.output_paths()["attempts_jsonl"], "r", encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        assert len(lines) == 2
        assert all(line["status"] == AttemptStatus.OUTPUT_PARSE_ERROR for line in lines)

    def test_results_are_written_incrementally(self, repository, tmp_path):
        """
        A failure in the middle of a run must not destroy the attempts that
        already completed.
        """

        class ExplodingProvider(FakeProvider):
            def complete(self, prompt, system_instruction=None):
                self.calls += 1
                if self.calls > 1:
                    raise ProviderError("provider exploded", category="provider_error")
                return ProviderResult(
                    content="not code",
                    provider=self.name,
                    requested_model_id=self.model_id,
                    is_mock=True,
                )

        provider = ExplodingProvider(is_mock=True)
        runner = self._runner(repository, provider, tmp_path / "run")
        runner.run(progress=False)
        with open(runner.output_paths()["attempts_jsonl"], "r", encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        assert len(lines) == 2
        assert lines[0]["status"] == AttemptStatus.OUTPUT_PARSE_ERROR
        assert lines[1]["status"] == AttemptStatus.PROVIDER_ERROR

    def test_existing_output_directory_is_not_overwritten(self, repository, tmp_path):
        provider = FakeProvider(content="not code", is_mock=True)
        output = tmp_path / "run"
        runner = self._runner(repository, provider, output)
        runner.run(progress=False)
        with pytest.raises(ExperimentError):
            self._runner(repository, provider, output).run(progress=False)

    def test_run_ids_are_unique_per_run(self, repository, tmp_path):
        provider = FakeProvider(content="not code", is_mock=True)
        first = self._runner(repository, provider, tmp_path / "a")
        second = self._runner(repository, provider, tmp_path / "b")
        assert first.run_id != second.run_id

    def test_metadata_records_the_manifest_digest(self, repository, tmp_path):
        provider = FakeProvider(content="not code", is_mock=True)
        runner = self._runner(repository, provider, tmp_path / "run")
        runner.run(progress=False)
        with open(runner.output_paths()["run_metadata_json"], "r", encoding="utf-8") as fh:
            metadata = json.load(fh)
        assert metadata["task_manifest_digest"] == repository.manifest_digest()
        assert metadata["mode"] == "mock"
        assert metadata["planned_attempts"] == 2


class TestPhase0TestsStillPass:

    def test_phase0_suite_is_unchanged_and_passing(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                os.path.join("benchmarks", "phase0", "test_phase0_tasks.py"),
                "-q",
            ],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "passed" in result.stdout
