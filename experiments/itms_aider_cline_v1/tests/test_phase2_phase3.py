"""Tests for the Phase 2 integrity validator, the Phase 3 analysis and the
read-only comparative viewer.

Everything here runs on **synthetic** fixtures. No provider is contacted and
no recorded benchmark attempt is modified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from experiments.itms_aider_cline_v1.analyze import (
    analyse,
    load_records,
    repetition_variation,
    system_metrics,
    task_matrix,
)
from experiments.itms_aider_cline_v1.integrity import IntegrityChecker
from experiments.itms_aider_cline_v1.runner import RECORD_COLUMNS

SYSTEMS = ("itms", "aider", "cline")
TASKS = ("HE-EDIT-001", "HE-EDIT-002")


# ---------------------------------------------------------------------------
# fixture: a small synthetic "live" run
# ---------------------------------------------------------------------------


def _make_run(tmp_path, records, planned=None, mode="live"):
    """Write a run directory that mirrors the real layout."""
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    for record in records:
        attempt_dir = run_dir / "artifacts" / record["attempt_id"]
        attempt_dir.mkdir(parents=True, exist_ok=True)
        if record.get("candidate_source") is not None:
            (attempt_dir / "candidate.py").write_text(
                record["candidate_source"], encoding="utf-8"
            )
            record["candidate_path"] = os.path.join(
                "artifacts", record["attempt_id"], "candidate.py"
            )
            record["candidate_sha256"] = hashlib.sha256(
                record["candidate_source"].encode("utf-8")
            ).hexdigest()

    with open(run_dir / "attempts.jsonl", "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")

    with open(run_dir / "attempts.csv", "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RECORD_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({k: record.get(k) for k in RECORD_COLUMNS})

    tasks = sorted({r["task_id"] for r in records})
    metadata = {
        "run_id": "synthetic",
        "experiment_id": "itms_vs_aider_vs_cline_v1",
        "mode": mode,
        "is_measurement": mode == "live",
        "declared_provider": "openrouter",
        "requested_model_id": "vendor/model:free",
        "temperature": 0.0,
        "repetitions": 1,
        "systems": list(SYSTEMS),
        "tasks": tasks,
        "planned_attempts": planned if planned is not None else len(records),
        "seed_policy": "per_system_support",
        "max_spend_usd": 1.0,
        "task_manifest_digest": "synthetic",
    }
    with open(run_dir / "run_metadata.json", "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    summary = {
        "recorded_attempts": len(records),
        "planned_attempts": metadata["planned_attempts"],
        "systems": {
            system: {
                "evaluated": sum(
                    1 for r in records
                    if r["system"] == system and r.get("evaluation_attempted")
                ),
            }
            for system in SYSTEMS
        },
    }
    with open(run_dir / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return run_dir, metadata


def _record(index, system, task, **overrides):
    base = {
        "attempt_id": f"a{index:032d}",
        "run_id": "synthetic",
        "timestamp_utc": "2026-01-01T00:00:00Z",
        "task_id": task,
        "humaneval_task_id": "HumanEval/0",
        "system": system,
        "method": system,
        "repetition": 1,
        "repetition_id": f"{task}::{system}::rep01",
        "mode": "live",
        "is_mock": False,
        "provider": "openrouter",
        "requested_model_id": "vendor/model:free",
        "served_model_id": "vendor/model:free",
        "seed_requested": 1234,
        "seed_supported": system in ("itms", "aider"),
        "seed_applied": 1234 if system in ("itms", "aider") else None,
        "execution_completed": True,
        "exit_code": 0,
        "timed_out": False,
        "latency_ms": 1000.0,
        "patch_applied": True,
        "syntax_valid": True,
        "evaluation_attempted": True,
        "test_passed": True,
        "test_error_type": None,
        "status": "repair_success",
        "failure_category": "none",
        "failure_reason": None,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "usage_source": "provider_reported",
        "cost_usd": None,
        "cost_source": "missing",
        "telemetry_source": "provider_reported",
        "candidate_source": "def f():\n    return 1\n",
        "artifacts_status": "ok",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def synthetic_run(tmp_path):
    records = []
    index = 0
    for task in TASKS:
        for system in SYSTEMS:
            index += 1
            records.append(_record(index, system, task))
    return _make_run(tmp_path, records)


# ---------------------------------------------------------------------------
# integrity validator
# ---------------------------------------------------------------------------


def test_integrity_passes_on_a_consistent_dataset(synthetic_run):
    run_dir, _ = synthetic_run
    result = IntegrityChecker(str(run_dir)).run()
    assert result["all_passed"], [c for c in result["checks"] if not c["passed"]]
    assert result["recorded_attempts"] == 6


def test_integrity_detects_a_missing_cell(tmp_path):
    records = []
    index = 0
    for task in TASKS:
        for system in SYSTEMS:
            index += 1
            if task == TASKS[1] and system == "cline":
                continue
            records.append(_record(index, system, task))
    run_dir, _ = _make_run(tmp_path, records, planned=6)
    result = IntegrityChecker(str(run_dir)).run()
    assert not result["all_passed"]
    assert result["missing_cells"]
    assert any(
        c["check"] == "attempt_matrix_complete" and not c["passed"]
        for c in result["checks"]
    )


def test_integrity_detects_a_tampered_candidate(tmp_path):
    records = [_record(1, "itms", TASKS[0])]
    run_dir, _ = _make_run(tmp_path, records)
    # tamper with the stored candidate
    candidate = run_dir / "artifacts" / records[0]["attempt_id"] / "candidate.py"
    candidate.write_text("def f():\n    return 999\n", encoding="utf-8")
    result = IntegrityChecker(str(run_dir)).run()
    assert not result["all_passed"]
    assert any(
        c["check"] == "candidate_hash_matches_stored_candidate" and not c["passed"]
        for c in result["checks"]
    )


def test_integrity_detects_a_mock_record(tmp_path):
    records = [_record(1, "itms", TASKS[0], is_mock=True)]
    run_dir, _ = _make_run(tmp_path, records)
    result = IntegrityChecker(str(run_dir)).run()
    assert not result["all_passed"]
    assert any(
        c["check"] == "no_mock_records_in_live_dataset" and not c["passed"]
        for c in result["checks"]
    )


def test_integrity_detects_a_duplicate_attempt_id(tmp_path):
    records = [_record(1, "itms", TASKS[0]), _record(1, "itms", TASKS[0])]
    run_dir, _ = _make_run(tmp_path, records)
    result = IntegrityChecker(str(run_dir)).run()
    assert not result["all_passed"]
    assert any(
        c["check"] == "attempt_ids_unique" and not c["passed"]
        for c in result["checks"]
    )


def test_integrity_flags_a_seed_claimed_without_support(tmp_path):
    records = [_record(1, "cline", TASKS[0], seed_supported=False, seed_applied=99)]
    run_dir, _ = _make_run(tmp_path, records)
    result = IntegrityChecker(str(run_dir)).run()
    assert not result["all_passed"]
    assert any(
        c["check"] == "no_seed_claimed_where_unsupported" and not c["passed"]
        for c in result["checks"]
    )


def test_integrity_flags_missing_telemetry_encoded_as_zero(tmp_path):
    records = [
        _record(
            1, "itms", TASKS[0], usage_source="missing",
            total_tokens=0, cost_usd=0,
        )
    ]
    run_dir, _ = _make_run(tmp_path, records)
    result = IntegrityChecker(str(run_dir)).run()
    assert not result["all_passed"]
    assert any(
        c["check"] == "missing_telemetry_is_null_not_zero" and not c["passed"]
        for c in result["checks"]
    )


def test_integrity_flags_an_unevaluated_attempt_counted_as_a_pass(tmp_path):
    records = [
        _record(
            1, "itms", TASKS[0], evaluation_attempted=False,
            test_passed=True, status="invalid_candidate",
        )
    ]
    run_dir, _ = _make_run(tmp_path, records)
    result = IntegrityChecker(str(run_dir)).run()
    assert not result["all_passed"]
    assert any(
        c["check"] == "unevaluated_attempts_are_never_passes" and not c["passed"]
        for c in result["checks"]
    )


def test_integrity_report_is_written(synthetic_run):
    from experiments.itms_aider_cline_v1.integrity import write_markdown

    run_dir, metadata = synthetic_run
    checker = IntegrityChecker(str(run_dir))
    result = checker.run()
    text = write_markdown(result, checker.load()["metadata"] or {})
    assert "Benchmark Integrity Report" in text
    assert "Matrix completeness" in text


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def test_system_metrics_use_planned_as_the_completion_denominator(tmp_path):
    records = [_record(i, "itms", TASKS[0]) for i in range(1, 4)]
    metrics = system_metrics(records, "itms", planned=10)
    assert metrics["recorded"] == 3
    assert metrics["planned"] == 10
    assert metrics["completion_rate"] == 0.3
    assert metrics["completion_rate_denominator"] == 10


def test_system_metrics_exclude_unevaluated_from_the_pass_rate(tmp_path):
    records = [
        _record(1, "itms", TASKS[0], evaluation_attempted=False,
                test_passed=None, status="invalid_candidate"),
        _record(2, "itms", TASKS[0], evaluation_attempted=True, test_passed=True),
    ]
    metrics = system_metrics(records, "itms", planned=2)
    assert metrics["evaluated"] == 1
    assert metrics["test_passed"] == 1
    assert metrics["evaluator_pass_rate"] == 1.0
    assert metrics["evaluator_pass_rate_denominator"] == 1


def test_missing_token_values_stay_none():
    records = [
        _record(1, "itms", TASKS[0], prompt_tokens=None, completion_tokens=None,
                total_tokens=None, usage_source="missing"),
    ]
    metrics = system_metrics(records, "itms", planned=1)
    assert metrics["tokens"]["total_mean"] is None
    assert metrics["tokens"]["total_available"] == 0


def test_task_matrix_covers_every_task_and_system():
    records = []
    index = 0
    for task in TASKS:
        for system in SYSTEMS:
            index += 1
            records.append(_record(index, system, task))
    matrix = task_matrix(records, list(TASKS))
    for task in TASKS:
        assert set(matrix[task]) == set(SYSTEMS)
        assert matrix[task]["itms"]["passed"] == 1


def test_repetition_variation_detects_variation():
    records = [
        _record(1, "itms", TASKS[0], repetition=1),
        _record(2, "itms", TASKS[0], repetition=2, test_passed=False,
                status="repair_failed", failure_category="test_failed"),
    ]
    variation = repetition_variation(records)
    assert variation["itms"]["varies"] is True


def test_analysis_produces_all_ten_figures(synthetic_run):
    run_dir, _ = synthetic_run
    analysis = analyse(str(run_dir))
    assert len(analysis["figure_paths"]) == 10
    for path in analysis["figure_paths"]:
        assert os.path.exists(path)
        with open(path, "rb") as fh:
            assert fh.read(4) == b"\x89PNG"
    assert os.path.exists(
        os.path.join(analysis["analysis_dir"], "COMPARATIVE_RESULTS_REPORT.md")
    )
    assert os.path.exists(
        os.path.join(analysis["analysis_dir"], "RUN_SUMMARY.md")
    )
    assert os.path.exists(os.path.join(analysis["analysis_dir"], "analysis.json"))


def test_analysis_report_states_denominators(synthetic_run):
    run_dir, _ = synthetic_run
    analysis = analyse(str(run_dir))
    report = os.path.join(analysis["analysis_dir"], "COMPARATIVE_RESULTS_REPORT.md")
    with open(report, encoding="utf-8") as fh:
        text = fh.read()
    assert "denominator" in text.lower()
    assert "not HumanEval" in text
    assert "Limitations" in text


# ---------------------------------------------------------------------------
# comparative viewer
# ---------------------------------------------------------------------------


def test_viewer_is_read_only_and_finds_the_latest_live_run(synthetic_run, monkeypatch):
    import comparative_viewer

    run_dir, _ = synthetic_run
    runs_root = str(run_dir.parent)
    monkeypatch.setattr(comparative_viewer, "RUNS_ROOT", runs_root)
    found = comparative_viewer.find_latest_live_run(runs_root)
    assert found == str(run_dir)

    loaded = comparative_viewer.load_comparative_run(str(run_dir))
    assert len(loaded["records"]) == 6
    # the module must not define any writer
    source = open(comparative_viewer.__file__, encoding="utf-8").read()
    for forbidden in ('"w"', "'w'", "write(", "open("):
        if forbidden == "open(":
            continue
        assert forbidden not in source, forbidden


def test_viewer_metrics_match_the_records(synthetic_run):
    import comparative_viewer

    run_dir, _ = synthetic_run
    loaded = comparative_viewer.load_comparative_run(str(run_dir))
    metrics = comparative_viewer.compute_comparative_metrics(loaded)
    for system in SYSTEMS:
        m = metrics["systems"][system]
        assert m["recorded"] == 2
        assert m["completed"] == 2
        assert m["evaluated"] == 2
        assert m["passed"] == 2
        assert m["completion_rate"] == 1.0


def test_viewer_reports_missing_files_instead_of_faking(tmp_path):
    import comparative_viewer

    empty = tmp_path / "empty"
    empty.mkdir()
    loaded = comparative_viewer.load_comparative_run(str(empty))
    assert loaded["records"] == []
    assert loaded["errors"]
    assert not loaded["integrity_report_exists"]


def test_viewer_present_cells_cover_the_recorded_attempts(synthetic_run):
    import comparative_viewer

    run_dir, _ = synthetic_run
    loaded = comparative_viewer.load_comparative_run(str(run_dir))
    metrics = comparative_viewer.compute_comparative_metrics(loaded)
    assert len(metrics["present_cells"]) == 6
    assert metrics["recorded_attempts"] == 6
