"""Tests for the ITMS vs Aider vs Cline experiment preparation (Phase 1).

These tests are strictly offline: no provider is contacted, no ``aider`` or
``cline`` process is launched.  The CLI contracts of the two external tools
are asserted by inspecting the argv that the adapters build, with
``subprocess.run`` replaced by a stub.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from experiments.itms_aider_cline_v1.config import ExperimentConfig, load_manifest
from experiments.itms_aider_cline_v1.dry_run import (
    DryRunResult,
    install_network_guard,
    run_mock_pipeline,
    validate_config,
    validate_schedule,
)
from experiments.itms_aider_cline_v1.runner import (
    RECORD_COLUMNS,
    AttemptPlan,
    ExperimentRunner,
    attempt_id_for,
    build_adapters,
    build_schedule,
)
from experiments.itms_aider_cline_v1.systems import (
    AiderSystemAdapter,
    ClineSystemAdapter,
    ItmsSystemAdapter,
    MockSystemAdapter,
    TaskBundle,
)
from experiments.itms_aider_cline_v1.systems.aider import _parse_aider_telemetry
from experiments.itms_aider_cline_v1.systems.cline import _parse_cline_jsonl


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def config() -> ExperimentConfig:
    return ExperimentConfig.load()


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def mock_run(tmp_path_factory, config, manifest):
    """Run the full mock pipeline once for the whole module."""
    output_dir = str(tmp_path_factory.mktemp("mockrun"))
    result = DryRunResult()
    summary = run_mock_pipeline(config, manifest, output_dir, result)
    return {"output_dir": output_dir, "summary": summary, "result": result}


@pytest.fixture()
def bundle(manifest):
    entry = manifest["tasks"][0]
    return TaskBundle(
        task_id=entry["task_id"],
        humaneval_task_id=entry["source"]["humaneval_task_id"],
        target_function=entry["source"]["entry_point"],
        problem_prompt=entry["task_description"]["problem_prompt"],
        repair_instruction=entry["task_description"]["repair_instruction"],
        mutated_source=open(
            os.path.join(REPO_ROOT, entry["task_description"]["mutated_source_path"]),
            encoding="utf-8",
        ).read(),
        mutated_source_path=os.path.join(
            REPO_ROOT, entry["task_description"]["mutated_source_path"]
        ),
        official_test_path=os.path.join(REPO_ROOT, entry["official_test"]["path"]),
    )


# ---------------------------------------------------------------------------
# manifest / selection protocol
# ---------------------------------------------------------------------------


def test_manifest_has_twenty_tasks(manifest):
    assert manifest["task_count"] == 20
    assert len({t["task_id"] for t in manifest["tasks"]}) == 20


def test_every_task_maps_to_a_distinct_humaneval_problem(manifest):
    humaneval_ids = [t["source"]["humaneval_task_id"] for t in manifest["tasks"]]
    assert len(set(humaneval_ids)) == 20


def test_manifest_records_provenance_test_and_rationale(manifest):
    for task in manifest["tasks"]:
        assert task["source"]["dataset_revision"]
        assert task["source"]["dataset_license"] == "MIT"
        assert task["official_test"]["path"].endswith("official_test.py")
        assert task["official_test"]["hidden_from_model"] is True
        assert task["expected_difficulty_mechanism"]
        assert task["selection_rationale"]
        assert task["observed_outcome_evidence"]["used_for_selection"] is False


def test_manifest_declares_held_out_pool(manifest):
    pool = manifest["selection_protocol"]["held_out_pool"]
    assert pool["total_available"] > 0
    assert "not from these 20" in " ".join(
        manifest["selection_protocol"]["bias_controls"]
    )


def test_manifest_check_mode_is_stable():
    from experiments.itms_aider_cline_v1.select_tasks import main

    assert main(["--check"]) == 0


# ---------------------------------------------------------------------------
# schedule / identity
# ---------------------------------------------------------------------------


def test_schedule_is_the_full_cross_product(config):
    plans = build_schedule(config.task_ids, config.systems, config.repetitions, 1)
    assert len(plans) == 300
    assert len({p.key for p in plans}) == 300


def test_attempt_ids_are_unique_and_deterministic(config):
    plans = build_schedule(config.task_ids, config.systems, config.repetitions, 1)
    ids = [attempt_id_for(p, "run") for p in plans]
    assert len(set(ids)) == 300
    again = [attempt_id_for(p, "run") for p in plans]
    assert ids == again


def test_different_run_ids_give_different_attempt_ids():
    plan = AttemptPlan(task_id="HE-EDIT-001", system="itms", repetition=1)
    assert attempt_id_for(plan, "runA") != attempt_id_for(plan, "runB")


def test_repetition_id_is_explicit():
    plan = AttemptPlan(task_id="HE-EDIT-001", system="aider", repetition=3)
    assert plan.repetition_id == "HE-EDIT-001::aider::rep03"


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_config_has_no_credential_material(config):
    serialised = json.dumps(config.to_dict()).lower()
    for needle in ("api_key", "sk-", "or-", "token\":", "secret"):
        assert needle not in serialised


def test_config_planned_attempts(config):
    assert config.planned_attempts == 300


def test_seed_policy_records_request_not_application(config):
    seed = config.seed_for("HE-EDIT-001", "cline", 1)
    assert isinstance(seed, int)
    assert config.seed_for("HE-EDIT-001", "cline", 2) != seed


# ---------------------------------------------------------------------------
# system adapters: CLI contract, without launching anything
# ---------------------------------------------------------------------------


def test_aider_builds_a_non_interactive_one_shot_command(tmp_path, monkeypatch, bundle):
    captured = {}

    class _Result:
        returncode = 0
        stdout = "Tokens: 100 sent, 20 received. Cost: $0.01 message, $0.02 session."
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")

    adapter = AiderSystemAdapter(model_id="vendor/model:free", provider="openrouter")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "candidate.py").write_text(bundle.mutated_source, encoding="utf-8")

    outcome = adapter.run(bundle, str(workspace), "rep01", seed=4242)

    cmd = captured["cmd"]
    assert "--message" in cmd
    assert bundle.repair_instruction in cmd
    assert "--yes-always" in cmd
    assert "--exit" in cmd
    assert "--no-git" in cmd
    assert "--no-stream" in cmd
    assert "--no-analytics" in cmd
    assert "--no-check-update" in cmd
    assert cmd[-1] == "candidate.py"
    assert "openrouter/vendor/model:free" in cmd
    assert captured["cwd"] == str(workspace)
    # the credential must travel in the environment, never on the command line
    assert "test-key-not-real" not in " ".join(cmd)
    assert captured["env"]["OPENROUTER_API_KEY"] == "test-key-not-real"
    # the seed is written into the model settings file
    settings = (workspace / ".aider.model.settings.yml").read_text(encoding="utf-8")
    assert "seed: 4242" in settings
    assert outcome.seed_applied == 4242
    assert outcome.usage_source == "tool_reported"
    assert outcome.prompt_tokens == 100
    assert outcome.completion_tokens == 20
    assert outcome.cost_usd == 0.02


def test_aider_reports_missing_binary_as_execution_failure(tmp_path, monkeypatch, bundle):
    monkeypatch.setattr("shutil.which", lambda name: None)
    adapter = AiderSystemAdapter(model_id="vendor/model:free")
    outcome = adapter.run(bundle, str(tmp_path), "rep01")
    assert outcome.execution_completed is False
    assert outcome.failure_category == "tool_not_available"


def test_aider_telemetry_parser_handles_k_suffixes():
    tokens, cost = _parse_aider_telemetry(
        "Tokens: 12.3k sent, 1.5k cache write, 900 received. "
        "Cost: $0.1234 message, $0.5678 session."
    )
    assert tokens["sent"] == 12300
    assert tokens["cache_write"] == 1500
    assert tokens["received"] == 900
    assert cost["session"] == 0.5678


def test_cline_builds_a_non_interactive_json_command(tmp_path, monkeypatch, bundle):
    captured = {}
    run_result = {
        "type": "run_result",
        "finishReason": "stop",
        "durationMs": 1234,
        "usage": {
            "inputTokens": 321,
            "outputTokens": 45,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
            "totalCost": 0.0042,
        },
        "model": {"id": "vendor/model:free", "provider": "openrouter"},
    }

    class _Result:
        returncode = 0
        stdout = json.dumps({"type": "agent_event", "event": {"type": "iteration_start"}}) \
            + "\n" + json.dumps(run_result) + "\n"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")

    adapter = ClineSystemAdapter(model_id="vendor/model:free", provider="openrouter")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "candidate.py").write_text(bundle.mutated_source, encoding="utf-8")

    outcome = adapter.run(bundle, str(workspace), "rep01", seed=99)

    cmd = captured["cmd"]
    assert "--json" in cmd
    assert "--yolo" in cmd
    assert bundle.repair_instruction == cmd[-1]
    assert "openrouter" in cmd
    assert "vendor/model:free" in cmd
    assert captured["cwd"] == str(workspace)
    assert "test-key-not-real" not in " ".join(cmd)
    # cline has no request-level seed
    assert adapter.supports_seed is False
    assert outcome.seed_supported is False
    assert outcome.seed_applied is None
    assert outcome.prompt_tokens == 321
    assert outcome.completion_tokens == 45
    assert outcome.cost_usd == 0.0042
    assert outcome.usage_source == "tool_reported"


def test_cline_jsonl_parser_ignores_non_json_lines():
    events, run_result, parse_error = _parse_cline_jsonl(
        "not json\n" + json.dumps({"type": "run_result", "finishReason": "stop"}) + "\n"
    )
    assert len(events) == 1
    assert run_result["finishReason"] == "stop"
    assert parse_error is None


def test_itms_adapter_reports_missing_credential_as_configuration_error(
    tmp_path, monkeypatch, bundle
):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    adapter = ItmsSystemAdapter(model_id="vendor/model:free")
    monkeypatch.setattr(adapter, "_resolve_api_key", lambda: None)
    outcome = adapter.run(bundle, str(tmp_path), "rep01")
    assert outcome.execution_completed is False
    assert outcome.failure_category == "configuration_error"


def test_itms_adapter_declares_seed_support():
    assert ItmsSystemAdapter(model_id="vendor/model:free").supports_seed is True


# ---------------------------------------------------------------------------
# runner: fairness, isolation, failure accounting
# ---------------------------------------------------------------------------


def test_every_system_receives_the_same_starting_file(tmp_path, config, manifest):
    """The candidate file handed to a system must be byte-identical across systems."""
    seen = {}
    for system in config.systems:
        adapter = MockSystemAdapter(behaviour="correct")
        runner = ExperimentRunner(
            config=config,
            manifest=manifest,
            adapters={system: adapter},
            output_dir=str(tmp_path / system),
            mode="mock",
        )
        bundle = runner._task_bundle(config.task_ids[0])
        workspace = runner._prepare_workspace(bundle)
        with open(os.path.join(workspace, bundle.workspace_filename), encoding="utf-8") as fh:
            seen[system] = fh.read()
    assert len(set(seen.values())) == 1


def test_official_test_is_not_present_in_the_workspace(tmp_path, config, manifest):
    runner = ExperimentRunner(
        config=config,
        manifest=manifest,
        adapters={"itms": MockSystemAdapter()},
        output_dir=str(tmp_path / "out"),
        mode="mock",
    )
    bundle = runner._task_bundle(config.task_ids[0])
    workspace = runner._prepare_workspace(bundle)
    names = os.listdir(workspace)
    assert names == ["candidate.py"]
    assert not any("test" in name for name in names)


def test_workspace_is_outside_the_repository(tmp_path, config, manifest):
    runner = ExperimentRunner(
        config=config,
        manifest=manifest,
        adapters={"itms": MockSystemAdapter()},
        output_dir=str(tmp_path / "out"),
        mode="mock",
    )
    bundle = runner._task_bundle(config.task_ids[0])
    workspace = runner._prepare_workspace(bundle)
    assert not os.path.abspath(workspace).startswith(os.path.abspath(REPO_ROOT) + os.sep)


def test_mock_pipeline_produces_three_hundred_records(mock_run):
    assert mock_run["summary"]["recorded_attempts"] == 300
    assert mock_run["result"].all_passed


def test_failed_execution_keeps_its_artifacts(tmp_path, config, manifest):
    class _BrokenAdapter(MockSystemAdapter):
        def run(self, task, workspace, repetition_id, seed=None):
            from experiments.itms_aider_cline_v1.systems.base import (
                FailureCategory,
                SystemOutcome,
            )

            return SystemOutcome(
                system=self.name,
                task_id=task.task_id,
                repetition_id=repetition_id,
                execution_completed=False,
                failure_category=FailureCategory.TIMEOUT,
                failure_reason="simulated timeout",
            )

    adapters = {name: _BrokenAdapter() for name in config.systems}
    runner = ExperimentRunner(
        config=config,
        manifest=manifest,
        adapters=adapters,
        output_dir=str(tmp_path / "run"),
        mode="mock",
        run_id="broken",
    )
    summary = runner.run(progress=False)
    assert summary["recorded_attempts"] == 300
    for system in config.systems:
        assert summary["systems"][system]["execution_failures"] == 100
        assert summary["systems"][system]["evaluated"] == 0
    # every attempt still has an artifact directory
    artifacts = os.path.join(str(tmp_path / "run"), "artifacts")
    assert len(os.listdir(artifacts)) == 300


def test_records_carry_the_required_measurement_fields(mock_run):
    output_dir = mock_run["output_dir"]
    with open(os.path.join(output_dir, "attempts.jsonl"), encoding="utf-8") as fh:
        record = json.loads(fh.readline())
    for column in RECORD_COLUMNS:
        assert column in record, column
    assert record["seed_requested"] is not None
    assert record["seed_supported"] in (True, False)
    assert record["telemetry_source"]
    assert record["provider"]
    assert record["requested_model_id"]


def test_csv_mirror_matches_the_jsonl(mock_run):
    import csv

    output_dir = mock_run["output_dir"]
    with open(os.path.join(output_dir, "attempts.jsonl"), encoding="utf-8") as fh:
        jsonl_ids = [json.loads(line)["attempt_id"] for line in fh if line.strip()]
    with open(os.path.join(output_dir, "attempts.csv"), encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(jsonl_ids) == 300
    assert [r["attempt_id"] for r in rows] == jsonl_ids
    assert list(rows[0].keys()) == RECORD_COLUMNS


def test_run_metadata_records_configuration_and_never_a_secret(mock_run):
    output_dir = mock_run["output_dir"]
    with open(os.path.join(output_dir, "run_metadata.json"), encoding="utf-8") as fh:
        metadata = json.load(fh)
    assert metadata["planned_attempts"] == 300
    assert metadata["seed_policy"] == "per_system_support"
    assert set(metadata["systems"]) == {"itms", "aider", "cline"}
    assert "sk-" not in json.dumps(metadata)


def test_mock_build_adapters_never_touch_the_network(config):
    adapters = build_adapters(config, mode="mock")
    assert set(adapters) == set(config.systems)
    for adapter in adapters.values():
        assert adapter.requires_credential is False
        assert adapter.preflight()["available"] is True


# ---------------------------------------------------------------------------
# offline guarantee
# ---------------------------------------------------------------------------


def test_network_guard_blocks_connections(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "socket", socket.socket)
    install_network_guard()
    with pytest.raises(Exception):
        socket.socket()
