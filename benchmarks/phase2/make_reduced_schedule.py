#!/usr/bin/env python3
"""
Generator for the *reduced* 100-request schedule.

Why a reduced schedule exists
-----------------------------
The full experiment is ``20 tasks x 3 methods x 5 repetitions = 300``
attempts.  A reduced budget of **100** requests cannot be expressed by the
default cross-product schedule: the runner builds
``reps x tasks x methods``, so the only reachable sizes are 60 (1
repetition) and 120 (2 repetitions) for the 20-task, 3-method matrix.

This module therefore builds the schedule explicitly and writes it to a
JSON file that the runner consumes through ``--schedule-file``.  The
schedule is a *plan*, not a result: it is produced before any model call
and is hashed into the run metadata.

Design of the schedule
----------------------
*   **Full coverage.**  Every one of the 20 tasks is attempted with every
    one of the 3 methods at least once: 60 cells.
*   **Per-task balance.**  The remaining 40 cells are distributed so that
    every task receives exactly 5 attempts in total (3 + 2 extra), and no
    (task, method) cell is attempted more than twice.
*   **Per-method allocation.**  The 100 cells are split
    ``34 / 33 / 33`` across the three methods.  A method is doubled on a
    task exactly when the task's *single* (not doubled) method is one of
    the other two.
*   **Determinism.**  The assignment follows from the sorted task ids and
    a fixed rule; the execution order is a seeded shuffle, exactly as the
    default schedule is shuffled, so the methods are interleaved and a
    provider outage cannot favour one method.

The repetition imbalance is intentional and is reported: 40 of the 60
(task, method) cells are attempted twice, 20 are attempted once.  This is
**not** 20 tasks x 5 repetitions x 3 methods, and the run must never be
described that way.

Usage::

    python benchmarks/phase2/make_reduced_schedule.py --out <file>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.adapters import METHODS  # noqa: E402

#: The method order used for the allocation.  The first method receives the
#: one extra cell, so the split is 34 / 33 / 33.
ALLOCATION_ORDER: Tuple[str, ...] = (
    "full_file_regeneration",
    "search_replace_patch",
    "itms_ast_splice",
)

#: How many tasks are *not* doubled for each method.  A task has exactly
#: two doubled cells and one single cell, so the number of tasks in which a
#: method is the single one is ``20 - (doubles of that method)``.
#:
#: full_file_regeneration: 20 - 14 = 6 tasks
#: search_replace_patch   : 20 - 13 = 7 tasks
#: itms_ast_splice        : 20 - 13 = 7 tasks
SINGLE_TASK_COUNTS: Dict[str, int] = {
    "full_file_regeneration": 6,
    "search_replace_patch": 7,
    "itms_ast_splice": 7,
}

#: The seed of the execution-order shuffle.  It is recorded in the schedule
#: file and in the run metadata.
ORDER_SEED = 20261024


def build_cells(task_ids: Sequence[str], methods: Sequence[str] = METHODS) -> List[Dict[str, Any]]:
    """
    Build the cells of the reduced schedule.

    The rule is fixed and outcome-independent: it depends only on the sorted
    task ids and on the method order, never on a model result.
    """
    if sorted(methods) != sorted(ALLOCATION_ORDER):
        raise ValueError(
            f"the reduced schedule is defined for the three methods "
            f"{list(ALLOCATION_ORDER)}, got {list(methods)}"
        )
    tasks = sorted(task_ids)
    total_single = sum(SINGLE_TASK_COUNTS.values())
    if total_single != len(tasks):
        raise ValueError(
            f"the single-cell counts {SINGLE_TASK_COUNTS} cover {total_single} "
            f"tasks, but the schedule has {len(tasks)}"
        )

    # -- which method is the *single* (not doubled) one for each task -----
    single_method: Dict[str, str] = {}
    cursor = 0
    for method in ALLOCATION_ORDER:
        for _ in range(SINGLE_TASK_COUNTS[method]):
            single_method[tasks[cursor]] = method
            cursor += 1

    cells: List[Dict[str, Any]] = []
    for task_id in tasks:
        excluded = single_method[task_id]
        # (1) the coverage pass: every method once.
        for method in ALLOCATION_ORDER:
            cells.append({"task_id": task_id, "method": method, "rep": 1})
        # (2) the extra pass: the two doubled methods get a second attempt.
        for method in ALLOCATION_ORDER:
            if method == excluded:
                continue
            cells.append({"task_id": task_id, "method": method, "rep": 2})
    return cells


def order_cells(cells: List[Dict[str, Any]], seed: int = ORDER_SEED) -> List[Dict[str, Any]]:
    """
    Return the execution order of the cells.

    The order is a seeded shuffle, exactly like the default schedule, so the
    three methods are interleaved across the run and a provider outage hits
    the methods as evenly as the plan allows.
    """
    ordered = list(cells)
    random.Random(seed).shuffle(ordered)
    return ordered


def build_schedule_document(
    task_ids: Sequence[str],
    methods: Sequence[str] = METHODS,
    seed: int = ORDER_SEED,
) -> Dict[str, Any]:
    """Build the full schedule document that is written to disk."""
    cells = build_cells(task_ids, methods)
    ordered = order_cells(cells, seed=seed)
    by_method: Dict[str, int] = {}
    by_task: Dict[str, int] = {}
    for cell in ordered:
        by_method[cell["method"]] = by_method.get(cell["method"], 0) + 1
        by_task[cell["task_id"]] = by_task.get(cell["task_id"], 0) + 1
    return {
        "schema_version": "1.0",
        "schedule_id": "modular_reduced_100_v1",
        "description": (
            "Reduced 100-request schedule for the modular task family. Every "
            "task is attempted with every method at least once (60 cells); "
            "the remaining 40 cells give each task exactly 5 attempts in "
            "total and split the budget 34/33/33 across the three methods. "
            "This is NOT 20 tasks x 5 repetitions x 3 methods: that design "
            "needs 300 requests."
        ),
        "task_set": "modular_edit",
        "data_dir": "benchmarks/data/modular",
        "task_count": len(task_ids),
        "methods": list(ALLOCATION_ORDER),
        "cell_count": len(ordered),
        "cells_per_method": by_method,
        "attempts_per_task": dict(sorted(by_task.items())),
        "order_seed": seed,
        "repetition_policy": {
            "repetitions_used": [1, 2],
            "cells_with_rep_1": sum(1 for c in ordered if c["rep"] == 1),
            "cells_with_rep_2": sum(1 for c in ordered if c["rep"] == 2),
            "note": (
                "Each task has 5 attempts: one per method (the coverage pass) "
                "plus a second attempt for two of its three methods.  Across "
                "the family, 60 of the 100 cells are a first attempt and 40 "
                "are a second attempt.  The imbalance is a consequence of "
                "the 100-request budget and is reported, not hidden."
            ),
        },
        "coverage": {
            "every_task_covered_by_every_method": True,
            "every_task_attempts": 5,
        },
        "cells": ordered,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="where to write the schedule JSON")
    parser.add_argument(
        "--data-dir",
        default=os.path.join("benchmarks", "data", "modular"),
        help="the task dataset the schedule is built for",
    )
    parser.add_argument("--seed", type=int, default=ORDER_SEED)
    args = parser.parse_args(argv)

    from benchmarks.benchmark.tasks import TaskRepository

    repository = TaskRepository(data_dir=args.data_dir)
    document = build_schedule_document(repository.task_ids, seed=args.seed)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"schedule written   : {out_path}")
    print(f"cells              : {document['cell_count']}")
    print(f"cells per method   : {document['cells_per_method']}")
    print(
        "attempts per task  : "
        f"min={min(document['attempts_per_task'].values())} "
        f"max={max(document['attempts_per_task'].values())} "
        f"over {document['task_count']} tasks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
