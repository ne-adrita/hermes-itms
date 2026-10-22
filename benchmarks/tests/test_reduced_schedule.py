"""
Offline tests for the reduced 100-request schedule.

The tests are local only: they never contact a provider and never make a
network request.  They guard the properties the reduced schedule must have:

*   exactly 100 cells, all unique,
*   the per-method allocation is 34 / 33 / 33,
*   every task is covered by every method at least once,
*   every task receives exactly 5 attempts,
*   the schedule is deterministic,
*   the runner loads the schedule, plans exactly 100 attempts and refuses
    to plan more than the request cap,
*   the default cross-product behaviour is unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmarks.benchmark.tasks import TaskRepository  # noqa: E402
from benchmarks.phase2.make_reduced_schedule import (  # noqa: E402
    ALLOCATION_ORDER,
    ORDER_SEED,
    build_cells,
    build_schedule_document,
    order_cells,
)

MODULAR_DIR = os.path.join(REPO_ROOT, "benchmarks", "data", "modular")
SCHEDULE_PATH = os.path.join(
    REPO_ROOT, "benchmarks", "phase2", "schedules", "modular_reduced_100_v1.json"
)

EXPECTED_CELLS = 100
EXPECTED_PER_METHOD = {
    "full_file_regeneration": 34,
    "search_replace_patch": 33,
    "itms_ast_splice": 33,
}


@pytest.fixture(scope="module")
def task_ids() -> list:
    return TaskRepository(data_dir=MODULAR_DIR).task_ids


@pytest.fixture(scope="module")
def document() -> dict:
    with open(SCHEDULE_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# The schedule file
# --------------------------------------------------------------------------

def test_the_schedule_file_has_exactly_one_hundred_unique_cells(document):
    cells = document["cells"]
    assert len(cells) == EXPECTED_CELLS
    keys = {(c["task_id"], c["method"], c["rep"]) for c in cells}
    assert len(keys) == EXPECTED_CELLS
    assert document["cell_count"] == EXPECTED_CELLS


def test_the_per_method_allocation_is_thirty_four_thirty_three_thirty_three(document):
    counts: dict = {}
    for cell in document["cells"]:
        counts[cell["method"]] = counts.get(cell["method"], 0) + 1
    assert counts == EXPECTED_PER_METHOD
    assert document["cells_per_method"] == EXPECTED_PER_METHOD


def test_every_task_is_covered_by_every_method_at_least_once(document, task_ids):
    covered: dict = {}
    for cell in document["cells"]:
        covered.setdefault(cell["task_id"], set()).add(cell["method"])
    assert len(covered) == len(task_ids)
    for task_id in task_ids:
        assert covered[task_id] == set(ALLOCATION_ORDER), task_id


def test_every_task_receives_exactly_five_attempts(document, task_ids):
    counts: dict = {}
    for cell in document["cells"]:
        counts[cell["task_id"]] = counts.get(cell["task_id"], 0) + 1
    assert len(counts) == len(task_ids)
    assert set(counts.values()) == {5}


def test_no_task_method_cell_is_attempted_more_than_twice(document):
    counts: dict = {}
    for cell in document["cells"]:
        key = (cell["task_id"], cell["method"])
        counts[key] = counts.get(key, 0) + 1
    assert set(counts.values()) == {1, 2}
    assert len(counts) == 60


def test_the_repetition_distribution_is_disclosed(document):
    policy = document["repetition_policy"]
    assert policy["cells_with_rep_1"] == 60
    assert policy["cells_with_rep_2"] == 40
    assert policy["repetitions_used"] == [1, 2]


def test_the_schedule_does_not_claim_the_full_design(document):
    """The document must not be mistakable for 20 x 5 x 3."""
    text = json.dumps(document)
    assert "NOT 20 tasks x 5 repetitions x 3 methods" in text
    assert document["cell_count"] == 100
    assert document["cell_count"] != 300


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_the_schedule_is_deterministic(task_ids):
    first = build_schedule_document(task_ids, seed=ORDER_SEED)
    second = build_schedule_document(task_ids, seed=ORDER_SEED)
    assert first["cells"] == second["cells"]


def test_the_committed_schedule_matches_the_generator(task_ids, document):
    regenerated = build_schedule_document(task_ids, seed=ORDER_SEED)
    assert regenerated["cells"] == document["cells"]
    assert regenerated["cells_per_method"] == document["cells_per_method"]


def test_the_execution_order_is_a_seeded_shuffle(task_ids):
    cells = build_cells(task_ids)
    ordered = order_cells(cells, seed=ORDER_SEED)
    assert [c["task_id"] for c in ordered] != [c["task_id"] for c in cells]
    # The three methods are interleaved: no method occupies a contiguous
    # block, so a provider outage cannot fall on one method only.
    methods = [c["method"] for c in ordered]
    for method in ALLOCATION_ORDER:
        positions = [i for i, m in enumerate(methods) if m == method]
        assert positions[-1] - positions[0] > len(positions)


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------

def test_the_runner_loads_the_schedule_and_plans_exactly_one_hundred(task_ids):
    from benchmarks.benchmark_runner import _load_schedule

    plans, provenance = _load_schedule(SCHEDULE_PATH)
    assert len(plans) == EXPECTED_CELLS
    assert provenance["cell_count"] == EXPECTED_CELLS
    assert len(provenance["sha256"]) == 64


def test_the_runner_rejects_a_malformed_schedule_file(tmp_path):
    from benchmarks.benchmark_runner import _load_schedule

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"cells": []}))
    with pytest.raises(ValueError):
        _load_schedule(str(empty))

    missing_field = tmp_path / "missing.json"
    missing_field.write_text(json.dumps({"cells": [{"task_id": "MOD-EDIT-001"}]}))
    with pytest.raises(ValueError):
        _load_schedule(str(missing_field))

    not_json = tmp_path / "notjson.json"
    not_json.write_text("this is not json")
    with pytest.raises(ValueError):
        _load_schedule(str(not_json))

    absent = tmp_path / "absent.json"
    with pytest.raises(ValueError):
        _load_schedule(str(absent))


def test_the_runner_rejects_a_schedule_with_an_unknown_task_or_method(tmp_path):
    """The plan is validated before the provider is ever contacted."""
    from benchmarks.benchmark_runner import _validate_schedule_cells
    from benchmarks.benchmark.experiment import AttemptPlan

    repository = TaskRepository(data_dir=MODULAR_DIR)
    good = AttemptPlan(task_id=repository.task_ids[0], method="itms_ast_splice", rep=1)
    _validate_schedule_cells([good], repository)

    with pytest.raises(ValueError):
        _validate_schedule_cells(
            [AttemptPlan(task_id="NOT-A-TASK", method="itms_ast_splice", rep=1)],
            repository,
        )
    with pytest.raises(ValueError):
        _validate_schedule_cells(
            [AttemptPlan(task_id=repository.task_ids[0], method="not-a-method", rep=1)],
            repository,
        )
    with pytest.raises(ValueError):
        _validate_schedule_cells([good, good], repository)


def test_the_experiment_runner_validates_the_schedule(task_ids):
    from benchmarks.benchmark.experiment import (
        AttemptPlan,
        ExperimentError,
        ExperimentRunner,
    )

    class _Stub:
        """A minimal stand-in for the task repository."""

        def __init__(self):
            self.task_ids = sorted(task_ids)

        def get(self, task_id):
            return task_id

        def manifest_digest(self):
            return "stub"

    def _make(schedule):
        return ExperimentRunner(
            repository=_Stub(),
            provider=None,
            mode="mock",
            output_dir="/tmp/never-written",
            schedule=schedule,
        )

    # A duplicate cell must be refused at construction time.
    with pytest.raises(ExperimentError):
        _make(
            [
                AttemptPlan(task_id=task_ids[0], method="itms_ast_splice", rep=1),
                AttemptPlan(task_id=task_ids[0], method="itms_ast_splice", rep=1),
            ]
        )
    # An unknown task must be refused.
    with pytest.raises(ExperimentError):
        _make([AttemptPlan(task_id="NOT-A-TASK", method="itms_ast_splice", rep=1)])
    # An unknown method must be refused.
    with pytest.raises(ExperimentError):
        _make([AttemptPlan(task_id=task_ids[0], method="not-a-method", rep=1)])

    runner = _make(
        [AttemptPlan(task_id=task_ids[0], method="itms_ast_splice", rep=1)]
    )
    assert runner.planned_call_count() == 1
    assert runner.scheduled_cells_by_method() == {"itms_ast_splice": 1}


def test_the_dry_run_reports_one_hundred_attempts_and_the_cap(tmp_path):
    """A live dry run validates the configuration without any request."""
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "benchmarks", "benchmark_runner.py"),
            "--dry-run",
            "--mode",
            "live",
            "--provider",
            "openrouter",
            "--model",
            "minimax/minimax-m3.1-flash:free",
            "--data-dir",
            "benchmarks/data/modular",
            "--schedule-file",
            os.path.relpath(SCHEDULE_PATH, REPO_ROOT),
            "--max-attempts",
            "100",
            "--max-retries",
            "0",
            "--max-spend-usd",
            "1.00",
            "--require-free-model",
            "--max-output-tokens",
            "4096",
            "--temperature",
            "0.2",
            "--seed",
            "20261024",
            "--seed-base",
            "991000",
            "--evaluation-timeout",
            "10",
            "--request-timeout",
            "60",
            "--output-dir",
            str(tmp_path / "preflight"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "planned attempts    : 100" in result.stdout
    assert "max attempts        : 100 (hard request cap)" in result.stdout
    assert "dry run: 100 attempts scheduled" in result.stdout
    assert "live preflight: OK" in result.stdout
    assert "cells per method: {'full_file_regeneration': 34, 'search_replace_patch': 33, 'itms_ast_splice': 33}" in result.stdout
    assert "every task is covered by every method: True" in result.stdout


def test_the_default_cross_product_behaviour_is_unchanged():
    """Without a schedule file the runner still plans the full product."""
    from benchmarks.benchmark.adapters import METHODS
    from benchmarks.benchmark.experiment import build_schedule

    task_ids = TaskRepository(data_dir=MODULAR_DIR).task_ids
    schedule = build_schedule(task_ids, list(METHODS), 5, seed=1)
    assert len(schedule) == 300
    assert len({plan.key for plan in schedule}) == 300
