"""
Offline tests for the Phase 2 readiness gates (B1-B6) and the configuration
validation.

Every test in this module is offline: the session-wide socket guard in
``conftest.py`` makes any outbound connection raise.  No provider is
contacted and no credential is read from the real environment: the
configuration is monkeypatched to a hermetic, fake value.

The tests cover:
  B1  request-level seed handling and the reproducibility metadata
  B2  the mandatory spend / attempt gate
  B3  exact prompt persistence
  B4  the offline analysis + figures pipeline
  B5  the justified, configurable output-token limit
  B6  the pre-registered, outcome-independent task-selection rule
  +   provider configuration validation that never discloses a secret
"""

from __future__ import annotations

import json
import os

import pytest

from benchmarks.benchmark.adapters import get_adapter
from benchmarks.benchmark.budget import BudgetError, BudgetGate
from benchmarks.benchmark.experiment import ExperimentRunner
from benchmarks.benchmark.output_budget import (
    justified_output_token_limit,
    resolve_output_token_limit,
)
from benchmarks.benchmark.pipeline import AttemptPipeline
from benchmarks.benchmark.provider import LiveProvider, MockProvider, ProviderError
from benchmarks.benchmark.records import AttemptRecord
from benchmarks.benchmark.report import build_analysis, write_analysis
from benchmarks.phase2.task_selection import (
    DEFAULT_CRITERIA,
    SelectionCriteria,
    compute_task_properties,
    select_tasks,
)

FAKE_KEY = "sk-or-v1-test0000000000000000000000000000000000000000000000000000000000"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _hermetic_config(monkeypatch, shared: bool = False, cross_vendor: bool = False):
    """Replaces the credential lookup with a hermetic, fake configuration."""
    from config import AppConfig

    monkeypatch.setattr(
        AppConfig, "resolve_api_key", classmethod(lambda cls, provider: FAKE_KEY)
    )
    groups = [["openai", "openrouter"]] if shared else []
    cross = (
        [{"providers": ["openai", "openrouter"], "gateways": ["api.openai.com", "openrouter.ai"]}]
        if cross_vendor
        else []
    )
    monkeypatch.setattr(
        AppConfig,
        "credential_binding_report",
        classmethod(
            lambda cls: {
                "providers_with_credential": ["openai", "openrouter"],
                "shared_credential_groups": groups,
                "shared_credential_detected": bool(groups),
                "cross_vendor_credential_groups": cross,
                "cross_vendor_credential_detected": bool(cross),
            }
        ),
    )
    return AppConfig


def _run_mock_benchmark(tmp_path, tasks=("HE-EDIT-006",), reps=1, seed_base=770000):
    """Runs the three strategies offline with the deterministic mock provider."""
    from benchmarks.benchmark.tasks import TaskRepository

    repository = TaskRepository()
    out_dir = str(tmp_path / "run")
    runner = ExperimentRunner(
        repository=repository,
        provider=MockProvider(policy="wrong_answer"),
        mode="mock",
        output_dir=out_dir,
        task_ids=list(tasks),
        reps=reps,
        seed=20260101,
        seed_base=seed_base,
        budget=BudgetGate(max_attempts=1000),
    )
    summary = runner.run(progress=False)
    return out_dir, summary


# ----------------------------------------------------------------------
# B1 - request-level seed and reproducibility metadata
# ----------------------------------------------------------------------
def test_b1_seed_is_derived_deterministically(tmp_path):
    """The same configuration produces the same seed for the same cell."""
    from benchmarks.benchmark.experiment import build_schedule

    schedule = build_schedule(["HE-EDIT-006"], ["itms_ast_splice"], 2, seed=7)
    runner = ExperimentRunner(
        repository=__import__(
            "benchmarks.benchmark.tasks", fromlist=["TaskRepository"]
        ).TaskRepository(),
        provider=MockProvider(),
        mode="mock",
        output_dir=str(tmp_path / "run"),
        task_ids=["HE-EDIT-006"],
        reps=2,
        seed=7,
        seed_base=770000,
    )
    first = [runner.seed_for(plan, index) for index, plan in enumerate(schedule)]
    second = [runner.seed_for(plan, index) for index, plan in enumerate(schedule)]
    assert first == second
    assert first == [770000, 770001]
    assert len(set(first)) == len(first)


def test_b1_seed_is_sent_on_the_request(monkeypatch, tmp_path):
    """The seed reaches the provider request, and the record says so."""
    _hermetic_config(monkeypatch)

    captured = {}

    class _FakeDispatcher:
        def generate_live(self, prompt, model_name, **kwargs):
            captured.update(kwargs)
            from model_dispatcher import ModelResponse

            return ModelResponse(
                content="```python\ndef below_zero(xs):\n    return True\n```",
                raw_tokens_in=10,
                cached_tokens_in=0,
                output_tokens=5,
                reasoning_tokens=0,
                model_name=model_name,
                provider="openrouter",
                latency_ms=1.0,
                is_mock=False,
            )

    provider = LiveProvider(
        provider="openrouter",
        model_id="vendor/model:free",
        dispatcher=_FakeDispatcher(),
    )
    provider._dispatcher = _FakeDispatcher()
    provider._dispatcher_owned = False
    result = provider.complete("prompt", None, seed=4242)
    assert captured["seed"] == 4242
    assert result.seed_requested == 4242
    assert result.seed_applied is True


def test_b1_seed_metadata_is_recorded_without_claiming_honour(tmp_path):
    """`seed_honour_verified` is never claimed from a single request."""
    out_dir, summary = _run_mock_benchmark(tmp_path)
    records = [
        json.loads(line)
        for line in open(os.path.join(out_dir, "attempts.jsonl"), encoding="utf-8")
    ]
    assert records
    for record in records:
        assert record["seed_requested"] is not None
        assert record["seed_honour_verified"] is False
    assert summary["seed_honour_verified"] is False


def test_b1_mock_provider_records_no_request_level_seed():
    """The mock engine performs no inference, so no seed is applied."""
    result = MockProvider().complete("prompt", None, seed=99)
    assert result.seed_requested == 99
    assert result.seed_supported is False
    assert result.seed_applied is False
    assert result.seed_honour_verified is False


# ----------------------------------------------------------------------
# B2 - the mandatory spend / attempt gate
# ----------------------------------------------------------------------
def test_b2_attempt_cap_aborts_before_the_next_request():
    """The gate refuses the request that would exceed the cap."""
    gate = BudgetGate(max_attempts=2)
    gate.preflight()
    gate.before_request("cell-1")
    gate.after_request(None)
    gate.before_request("cell-2")
    gate.after_request(None)
    with pytest.raises(BudgetError):
        gate.before_request("cell-3")
    state = gate.state()
    assert state["requests_issued"] == 2
    assert state["limit_reached_reason"]
    assert "cell-3" in state["not_executed_cells"]


def test_b2_spend_cap_aborts_on_reported_cost():
    """The spend cap is applied to the cost the provider reported."""
    gate = BudgetGate(max_attempts=10, max_spend_usd=0.01)
    gate.preflight()
    gate.after_request(0.004)
    assert not gate.spend_cap_reached()
    gate.after_request(0.007)
    assert gate.spend_cap_reached()
    assert "reported spend" in (gate.state()["limit_reached_reason"] or "")


def test_b2_free_model_guard_refuses_a_paid_model():
    """`require_free_model` refuses a model id that is not a :free route."""
    gate = BudgetGate(max_attempts=1, require_free_model=True, model_id="vendor/paid")
    with pytest.raises(BudgetError):
        gate.preflight()
    ok = BudgetGate(max_attempts=1, require_free_model=True, model_id="vendor/model:free")
    assert ok.preflight()["max_attempts"] == 1


def test_b2_gate_reports_unavailable_cost():
    """When the provider reports no cost, the gate says so."""
    gate = BudgetGate(max_attempts=2, max_spend_usd=1.0)
    gate.preflight()
    gate.after_request(None)
    state = gate.state()
    assert state["unreported_cost_attempts"] == 1
    assert state["cost_reporting_available"] is False
    assert state["spend_cap_effective"] is False


def test_b2_gate_requires_an_explicit_attempt_cap():
    """A run without a request cap is refused."""
    with pytest.raises(BudgetError):
        BudgetGate(max_attempts=0).preflight()


def test_b2_live_mode_requires_an_attempt_cap(monkeypatch, capsys):
    """The CLI refuses to start a live run without --max-attempts."""
    from benchmarks import benchmark_runner

    monkeypatch.setattr(
        benchmark_runner, "LiveProvider", _NeverCalledLiveProvider
    )
    code = benchmark_runner.main(
        [
            "--mode",
            "live",
            "--provider",
            "openrouter",
            "--model",
            "vendor/model:free",
            "--tasks",
            "HE-EDIT-006",
            "--reps",
            "1",
            "--output-dir",
            "/tmp/should-not-be-created-by-this-test",
        ]
    )
    assert code == 2
    assert "max-attempts" in capsys.readouterr().err


class _NeverCalledLiveProvider:
    """Stands in for LiveProvider so the CLI fails before any request."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("the live provider must not be constructed")


def test_b2_not_executed_cells_are_reported(tmp_path):
    """Cells that were planned but never requested are recorded."""
    from benchmarks.benchmark.tasks import TaskRepository

    runner = ExperimentRunner(
        repository=TaskRepository(),
        provider=MockProvider(),
        mode="mock",
        output_dir=str(tmp_path / "run"),
        task_ids=["HE-EDIT-006", "HE-EDIT-009"],
        reps=1,
        budget=BudgetGate(max_attempts=2),
    )
    summary = runner.run(progress=False)
    assert summary["recorded_attempts"] == 2
    assert summary["budget"]["not_executed_count"] == 4
    assert summary["budget"]["limit_reached_reason"]


# ----------------------------------------------------------------------
# B3 - exact prompt persistence
# ----------------------------------------------------------------------
def test_b3_prompt_is_persisted_with_identity(tmp_path):
    """Every attempt stores the exact prompt next to its response."""
    out_dir, _ = _run_mock_benchmark(tmp_path)
    records = [
        json.loads(line)
        for line in open(os.path.join(out_dir, "attempts.jsonl"), encoding="utf-8")
    ]
    for record in records:
        assert record["prompt_path"]
        prompt_file = os.path.join(out_dir, record["prompt_path"])
        assert os.path.isfile(prompt_file)
        meta_file = os.path.join(
            os.path.dirname(prompt_file), "prompt.json"
        )
        meta = json.load(open(meta_file, encoding="utf-8"))
        assert meta["task_id"] == record["task_id"]
        assert meta["method"] == record["method"]
        assert meta["attempt_id"] == record["attempt_id"]


def test_b3_prompt_hash_matches_the_sent_prompt(tmp_path):
    """The stored digest identifies the prompt that was sent."""
    from benchmarks.benchmark.artifacts import sha256_text
    from benchmarks.benchmark.tasks import TaskRepository

    class _CapturingProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def complete(self, prompt, system_instruction=None, seed=None):
            self.prompts.append(prompt)
            return super().complete(prompt, system_instruction, seed)

    provider = _CapturingProvider()
    runner = ExperimentRunner(
        repository=TaskRepository(),
        provider=provider,
        mode="mock",
        output_dir=str(tmp_path / "run"),
        task_ids=["HE-EDIT-006"],
        reps=1,
    )
    runner.run(progress=False)
    records = [
        json.loads(line)
        for line in open(
            os.path.join(str(tmp_path / "run"), "attempts.jsonl"), encoding="utf-8"
        )
    ]
    sent = {sha256_text(prompt) for prompt in provider.prompts}
    for record in records:
        assert record["prompt_sha256"] in sent


def test_b3_prompt_is_redacted(tmp_path):
    """A credential-shaped prompt is stored redacted, with the original hash."""
    from benchmarks.benchmark.artifacts import ArtifactStore
    from benchmarks.benchmark.provider import ProviderResult

    class _Outcome:
        pass

    outcome = _Outcome()
    outcome.record = AttemptRecord(
        attempt_id="a" * 32,
        run_id="r" * 12,
        timestamp_utc="2026-01-01T00:00:00Z",
        task_id="HE-EDIT-001",
        humaneval_task_id="HumanEval/1",
        method="itms_ast_splice",
        rep=1,
        mode="live",
        provider="openrouter",
        requested_model_id="vendor/model:free",
    )
    outcome.raw_response = "ok"
    outcome.candidate_source = "x = 1\n"
    outcome.evaluation = None
    outcome.provider_error = None
    outcome.prompt = "use key sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789 to authenticate"

    store = ArtifactStore(str(tmp_path / "run"), "runid")
    info = store.persist(outcome)
    stored = open(
        os.path.join(str(tmp_path / "run"), info["prompt_path"]), encoding="utf-8"
    ).read()
    assert "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789" not in stored
    assert "[REDACTED]" in stored
    assert info["prompt_sha256"] != info["prompt_file_sha256"]


# ----------------------------------------------------------------------
# B4 - the offline analysis + figures pipeline
# ----------------------------------------------------------------------
def test_b4_analysis_runs_offline_on_a_mock_run(tmp_path):
    """The analysis reads the saved artefacts and writes its own outputs."""
    out_dir, _ = _run_mock_benchmark(tmp_path)
    analysis = write_analysis(out_dir, str(tmp_path / "analysis"), write_figures=True)
    assert os.path.isfile(analysis["analysis_path"])
    assert os.path.isfile(analysis["summary_path"])
    assert len(analysis["figures"]) >= 6
    assert analysis["denominators"]["recorded_attempts"] == 3


def test_b4_analysis_reports_unavailable_cost(tmp_path):
    """Cost that the provider did not report is 'unavailable', never zero."""
    out_dir, _ = _run_mock_benchmark(tmp_path)
    analysis = build_analysis(out_dir)
    assert analysis["cost"]["available"] is False
    assert analysis["cost"]["total_cost_usd"] is None
    text = open(
        os.path.join(str(tmp_path), "none"), encoding="utf-8"
    ) if False else None
    from benchmarks.benchmark.report import render_markdown

    markdown = render_markdown(analysis)
    assert "Unavailable" in markdown
    assert "$0.000000" not in markdown


def test_b4_analysis_flags_a_mock_run_as_not_a_measurement(tmp_path):
    """A mock run is never presented as a measurement."""
    out_dir, _ = _run_mock_benchmark(tmp_path)
    analysis = build_analysis(out_dir)
    assert analysis["is_measurement"] is False
    assert any("MOCK run" in limitation for limitation in analysis["limitations"])


def test_b4_analysis_detects_a_tampered_candidate(tmp_path):
    """A candidate file that no longer matches its digest is detected."""
    out_dir, _ = _run_mock_benchmark(tmp_path)
    records = [
        json.loads(line)
        for line in open(os.path.join(out_dir, "attempts.jsonl"), encoding="utf-8")
    ]
    target = next(r for r in records if r["candidate_path"])
    with open(os.path.join(out_dir, target["candidate_path"]), "a", encoding="utf-8") as fh:
        fh.write("\n# tampered\n")
    analysis = build_analysis(out_dir)
    assert analysis["integrity"]["all_passed"] is False
    failed = [c["check"] for c in analysis["integrity"]["checks"] if not c["passed"]]
    assert "candidate_hashes_match_artifacts" in failed


def test_b4_figures_are_written(tmp_path):
    """The figure set is produced from the analysis bundle."""
    out_dir, _ = _run_mock_benchmark(tmp_path)
    analysis = write_analysis(out_dir, str(tmp_path / "analysis"), write_figures=True)
    names = {os.path.basename(path) for path in analysis["figures"]}
    assert "fig1_pass_rate_by_method.png" in names
    assert "fig10_cost_availability.png" in names
    for path in analysis["figures"]:
        assert os.path.getsize(path) > 0


# ----------------------------------------------------------------------
# B5 - the justified, configurable output-token limit
# ----------------------------------------------------------------------
def test_b5_justified_limit_covers_the_largest_file(repository):
    """The derived limit always covers the biggest file the baseline must emit."""
    tasks = repository.select(None)
    limit = justified_output_token_limit(tasks)
    largest = max(
        len(t.mutated_source) for t in tasks
    )
    assert limit >= 2048
    # The limit is at least the largest file's token count plus head-room.
    from benchmarks.benchmark.output_budget import _file_token_counts

    assert limit >= 2 * max(_file_token_counts(tasks))
    assert largest > 0


def test_b5_limit_is_configurable_and_recorded(repository):
    """An explicit limit wins and is recorded with its justification."""
    tasks = repository.select(None)
    info = resolve_output_token_limit(tasks, explicit=4096)
    assert info["resolved_limit"] == 4096
    assert info["limit_source"] == "operator_explicit"
    auto = resolve_output_token_limit(tasks, explicit=0)
    assert auto["limit_source"] == "justified_from_task_set"
    assert auto["resolved_limit"] >= 2048


def test_b5_same_cap_is_used_for_every_strategy(tmp_path):
    """All three strategies of a run share one output cap."""
    from benchmarks.benchmark.tasks import TaskRepository

    limit = resolve_output_token_limit(TaskRepository().select(None), explicit=4096)
    provider = MockProvider()
    provider.max_output_tokens = limit["resolved_limit"]
    runner = ExperimentRunner(
        repository=TaskRepository(),
        provider=provider,
        mode="mock",
        output_dir=str(tmp_path / "run"),
        task_ids=["HE-EDIT-006"],
        reps=1,
        output_token_limit=limit["resolved_limit"],
        output_limit_justification=limit,
    )
    summary = runner.run(progress=False)
    assert summary["output_cap_consistent_across_strategies"] is True
    assert summary["max_output_tokens_values"] == [4096]


def test_b5_truncation_is_recorded_explicitly(tmp_path):
    """A truncated attempt is a first-class, counted outcome."""
    out_dir, summary = _run_mock_benchmark(tmp_path)
    assert "truncated_outputs" in summary
    assert "truncated_outputs_by_method" in summary
    records = [
        json.loads(line)
        for line in open(os.path.join(out_dir, "attempts.jsonl"), encoding="utf-8")
    ]
    for record in records:
        assert "truncated_output" in record


# ----------------------------------------------------------------------
# B6 - the pre-registered task-selection rule
# ----------------------------------------------------------------------
def test_b6_properties_are_computed_from_the_source(repository):
    """The properties are measured from the task's own source file."""
    task = repository.get("HE-EDIT-006")
    properties = compute_task_properties(task)
    assert properties.task_id == "HE-EDIT-006"
    assert properties.file_lines == len(task.mutated_source.splitlines())
    assert properties.function_count >= 1
    assert 0.0 < properties.target_ratio <= 1.0


def test_b6_rule_rejects_every_current_task(repository):
    """The current task set does not satisfy the pre-registered rule."""
    result = select_tasks(repository.select(None), DEFAULT_CRITERIA)
    assert result.selected == []
    assert len(result.rejected) == 20


def test_b6_selection_is_deterministic_and_order_independent(repository):
    """The selection does not depend on the order of the pool."""
    tasks = repository.select(None)
    first = select_tasks(tasks, DEFAULT_CRITERIA)
    second = select_tasks(list(reversed(tasks)), DEFAULT_CRITERIA)
    assert first.selected == second.selected
    assert first.rejected == second.rejected


def test_b6_rejection_report_lists_every_excluded_task(repository):
    """Every excluded task is listed with the property that excluded it."""
    result = select_tasks(repository.select(None), DEFAULT_CRITERIA)
    assert set(result.rejected) == {t.task_id for t in repository.select(None)}
    for task_id, problems in result.rejected.items():
        assert problems
        assert any("target_ratio" in problem for problem in problems)


def test_b6_rule_selects_a_qualifying_task():
    """The rule does select a genuinely small-target-region task."""
    from benchmarks.benchmark.tasks import BenchmarkTask

    body = "\n".join(
        f"def helper_{index}(value):\n    total = value + {index}\n    return total\n"
        for index in range(60)
    )
    source = (
        body
        + "\nclass Widget:\n    def method(self, x):\n        return x\n"
    )
    assert len(source.splitlines()) >= 200
    task = BenchmarkTask(
        task_id="SYNTHETIC-001",
        humaneval_task_id="synthetic/1",
        target_function="method",
        problem_prompt="",
        repair_instruction="",
        mutated_source=source,
        mutated_source_path="",
        official_test="",
        official_test_path="",
        mutated_source_sha256="",
    )
    result = select_tasks([task], DEFAULT_CRITERIA)
    assert result.selected == ["SYNTHETIC-001"]


# ----------------------------------------------------------------------
# Provider configuration validation (no secret is ever disclosed)
# ----------------------------------------------------------------------
def test_config_report_lists_providers_without_exposing_values(monkeypatch):
    """The report contains provider names only."""
    from config import AppConfig

    _hermetic_config(monkeypatch, shared=True)
    report = AppConfig.credential_binding_report()
    assert report["shared_credential_detected"] is True
    assert report["shared_credential_groups"] == [["openai", "openrouter"]]
    serialised = json.dumps(report)
    assert FAKE_KEY not in serialised
    assert str(len(FAKE_KEY)) not in serialised  # no length disclosure either
    import hashlib

    assert hashlib.sha256(FAKE_KEY.encode()).hexdigest()[:12] not in serialised


def test_sharing_one_key_across_one_gateway_is_not_a_risk(monkeypatch):
    """Providers that all point at the same gateway may share one key."""
    _hermetic_config(monkeypatch, shared=True)
    provider = LiveProvider(
        provider="openrouter",
        model_id="vendor/model:free",
        enforce_credential_binding=True,
    )
    provider.preflight()  # must not raise: same gateway, no cross-vendor risk


def test_preflight_refuses_a_cross_vendor_credential(monkeypatch):
    """A key shared across *different* gateways is refused."""
    _hermetic_config(monkeypatch, cross_vendor=True)
    provider = LiveProvider(
        provider="openrouter",
        model_id="vendor/model:free",
        enforce_credential_binding=True,
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.preflight()
    assert "different gateways" in str(excinfo.value)

    allowed = LiveProvider(
        provider="openrouter",
        model_id="vendor/model:free",
        allow_shared_credential=True,
        enforce_credential_binding=True,
    )
    allowed.preflight()  # must not raise


def test_credential_binding_is_recorded_without_refusing_by_default(monkeypatch):
    """A library caller can inspect the binding report without a refusal."""
    _hermetic_config(monkeypatch, cross_vendor=True)
    provider = LiveProvider(provider="openrouter", model_id="vendor/model:free")
    provider.preflight()
    assert provider.credential_binding["cross_vendor_credential_detected"] is True
    assert FAKE_KEY not in json.dumps(provider.credential_binding)


def test_env_hygiene_detects_a_bare_secret_line(tmp_path, monkeypatch):
    """A line without a KEY= assignment is reported without its content."""
    from config import AppConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789\n"
        "a-bare-secret-line-that-is-not-an-assignment\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        AppConfig, "_env_file_path", staticmethod(lambda: str(env_file))
    )
    report = AppConfig.env_hygiene_report()
    assert report["bare_secret_line_present"] is True
    assert report["defined_variable_names"] == ["OPENROUTER_API_KEY"]
    assert "a-bare-secret" not in json.dumps(report)
