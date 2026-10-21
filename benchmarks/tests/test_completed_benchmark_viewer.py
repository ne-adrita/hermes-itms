"""
Offline tests for the read-only completed-benchmark viewer.

These tests only READ the artefacts of the completed run and the figures of
the offline analysis. They never run a benchmark, never contact a model
provider and never write a file. The package-level ``conftest.py`` blocks
outbound sockets for the whole session, so no network access is possible.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

from completed_benchmark_viewer import (
    DEFAULT_ANALYSIS_DIR,
    DEFAULT_RUN_DIR,
    GRAPH_FILENAMES,
    compute_method_metrics,
    compute_overall,
    load_run,
    read_graph_bytes,
)


def _require_run():
    if not os.path.isfile(os.path.join(DEFAULT_RUN_DIR, "attempts.jsonl")):
        pytest.skip(f"completed run not present at {DEFAULT_RUN_DIR}")
    return load_run(DEFAULT_RUN_DIR, DEFAULT_ANALYSIS_DIR)


class TestRunLoading:
    def test_expected_data_files_exist(self):
        for name in ("attempts.jsonl", "attempts.csv", "summary.json", "run_metadata.json"):
            path = os.path.join(DEFAULT_RUN_DIR, name)
            assert os.path.isfile(path), f"missing expected result file: {path}"

    def test_run_loads_without_problems(self):
        run = _require_run()
        assert run["problems"] == []
        assert len(run["records"]) == 120

    def test_jsonl_and_csv_agree_on_attempt_ids(self):
        run = _require_run()
        jsonl_ids = [r["attempt_id"] for r in run["records"]]
        csv_ids = [row["attempt_id"] for row in run["csv_rows"]]
        assert jsonl_ids == csv_ids
        assert len(set(jsonl_ids)) == 120

    def test_no_duplicate_attempt_ids(self):
        run = _require_run()
        ids = [r["attempt_id"] for r in run["records"]]
        assert len(set(ids)) == len(ids)

    def test_missing_directory_is_handled_gracefully(self):
        run = load_run(os.path.join(DEFAULT_RUN_DIR, "__does_not_exist__"), DEFAULT_ANALYSIS_DIR)
        assert run["records"] == []
        assert run["problems"], "a missing run directory must be reported"
        overall = compute_overall(run["records"])
        assert overall["total_scheduled"] == 0
        assert overall["completion_rate"] is None

    def test_loader_does_not_raise_on_unreadable_data(self):
        # A directory that exists but holds no result files must not raise.
        run = load_run(DEFAULT_ANALYSIS_DIR, DEFAULT_ANALYSIS_DIR)
        assert isinstance(run["problems"], list)
        assert isinstance(run["records"], list)


class TestOverallStatistics:
    def test_counts_are_derived_from_the_records(self):
        run = _require_run()
        overall = compute_overall(run["records"])
        assert overall["total_scheduled"] == len(run["records"]) == 120
        assert overall["successful"] + overall["output_failures"] == overall["total_scheduled"]
        assert overall["successful"] == sum(
            1 for r in run["records"] if r["status"] == "repair_success"
        )

    def test_output_failures_never_reached_the_evaluator(self):
        run = _require_run()
        overall = compute_overall(run["records"])
        not_evaluated = [
            r for r in run["records"] if r.get("evaluation_attempted") is not True
        ]
        assert overall["output_failures"] == len(not_evaluated)
        assert overall["reached_evaluation"] == len(run["records"]) - len(not_evaluated)

    def test_three_methods_and_task_count(self):
        run = _require_run()
        overall = compute_overall(run["records"])
        assert overall["method_count"] == 3
        assert overall["task_count"] == 20

    def test_run_contains_no_mock_attempts(self):
        run = _require_run()
        overall = compute_overall(run["records"])
        assert overall["mock_attempts"] == 0
        assert {r["mode"] for r in run["records"]} == {"live"}

    def test_a_single_served_model_is_recorded(self):
        run = _require_run()
        overall = compute_overall(run["records"])
        assert len(overall["served_model_ids"]) == 1
        assert overall["served_model_ids"] == overall["requested_model_ids"]


class TestMethodMetrics:
    def test_every_method_has_forty_scheduled_attempts(self):
        run = _require_run()
        metrics = compute_method_metrics(run["records"])
        assert len(metrics) == 3
        for method, m in metrics.items():
            assert m["scheduled"] == 40, method

    def test_rates_use_the_documented_denominators(self):
        run = _require_run()
        metrics = compute_method_metrics(run["records"])
        for method, m in metrics.items():
            assert m["completion_rate"] == pytest.approx(
                m["successful"] / m["scheduled"]
            )
            assert m["evaluator_pass_rate"] == pytest.approx(
                m["evaluator_passed"] / m["reached_evaluation"]
            )
            assert m["successful"] + m["output_failures"] == m["scheduled"]

    def test_latency_and_token_samples_are_not_imputed(self):
        run = _require_run()
        metrics = compute_method_metrics(run["records"])
        for method, m in metrics.items():
            assert m["latency_samples"] <= m["scheduled"]
            assert m["token_samples"] <= m["scheduled"]
            if m["mean_total_tokens"] is not None:
                assert m["token_samples"] > 0

    def test_metrics_agree_with_the_run_summary(self):
        run = _require_run()
        summary = run["summary"]
        if not summary:
            pytest.skip("summary.json unavailable")
        metrics = compute_method_metrics(run["records"])
        for method, m in metrics.items():
            recorded = summary["method_metrics"][method]
            assert recorded["repair_success"]["count"] == m["successful"], method
            assert recorded["denominators"]["scheduled_attempts"] == m["scheduled"], method


class TestFigures:
    def test_all_six_graph_files_exist_and_are_valid_png(self):
        missing = []
        for name in GRAPH_FILENAMES:
            path = os.path.join(DEFAULT_ANALYSIS_DIR, name)
            if not os.path.isfile(path):
                missing.append(name)
                continue
            with open(path, "rb") as fh:
                assert fh.read(8) == b"\x89PNG\r\n\x1a\n", name
        assert not missing, f"missing figure files: {missing}"

    def test_read_graph_bytes_returns_none_for_a_missing_file(self):
        assert read_graph_bytes(DEFAULT_ANALYSIS_DIR, "not_a_real_file.png") is None

    def test_read_graph_bytes_returns_content_for_a_real_file(self):
        name = GRAPH_FILENAMES[0]
        data = read_graph_bytes(DEFAULT_ANALYSIS_DIR, name)
        if data is None:
            pytest.skip("figure not present")
        assert data.startswith(b"\x89PNG\r\n\x1a\n")


class TestViewerIsReadOnly:
    def test_viewer_module_imports_no_network_client(self):
        import ast

        import completed_benchmark_viewer as viewer

        tree = ast.parse(inspect.getsource(viewer))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("httpx", "requests", "urllib", "socket", "subprocess", "aiohttp"):
            assert forbidden not in imported, f"viewer must not import {forbidden}"

    def test_viewer_module_defines_no_write_calls(self):
        import ast

        import completed_benchmark_viewer as viewer

        tree = ast.parse(inspect.getsource(viewer))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in ("write", "remove", "rmtree", "makedirs", "mkdir"):
            assert forbidden not in called, f"viewer must not call {forbidden}"

    def test_default_paths_are_absolute_and_inside_the_project(self):
        from completed_benchmark_viewer import PROJECT_ROOT

        assert os.path.isabs(DEFAULT_RUN_DIR)
        assert os.path.isabs(DEFAULT_ANALYSIS_DIR)
        assert os.path.commonpath([PROJECT_ROOT, DEFAULT_RUN_DIR]) == PROJECT_ROOT
        assert os.path.commonpath([PROJECT_ROOT, DEFAULT_ANALYSIS_DIR]) == PROJECT_ROOT
