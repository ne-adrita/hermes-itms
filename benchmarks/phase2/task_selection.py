"""
Phase 2 task-set selection: a reproducible, outcome-independent rule.

Why a selection rule at all
---------------------------
The Phase 0 task set that the historical 120-attempt run used consists of
twenty *single-function* HumanEval-derived modules.  In those files the target
function occupies ~96 % of the file, so "surgical" editing has essentially
nothing to save, and a capable model solves all of them.  A benchmark built
on that set cannot establish the Phase 2 thesis: it measures the harness, not
the editing methods.

The remedy is **not** to cherry-pick tasks that happen to favour one method.
The remedy is a **pre-registered selection rule**, expressed only in
measurable properties of a task file, that is applied to a *pool* of
candidate tasks, together with a rejection report that lists every excluded
task and the property that excluded it.

Rules of this module
---------------------
*   The criteria are properties of the **source file** (size, structure,
    target ratio).  They never reference a model result, a pass rate or any
    other outcome.
*   The rule is fixed in version-controlled code **before** any model run.
*   ``select_tasks`` returns both the qualifying tasks and the rejection
    report, so the selection is auditable and reproducible.
*   The existing Phase 0 task set is **never modified**.  It is reported as
    not representative, and a new task family must be built and validated by
    the Phase 0 pipeline before it can be used in a run.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "TaskProperties",
    "SelectionCriteria",
    "SelectionResult",
    "compute_task_properties",
    "select_tasks",
    "write_report",
    "DEFAULT_CRITERIA",
]


@dataclass(frozen=True)
class TaskProperties:
    """The measurable properties of one candidate task."""

    task_id: str
    file_lines: int
    file_tokens: int
    target_tokens: int
    target_ratio: float
    function_count: int
    class_count: int
    has_module_docstring: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "file_lines": self.file_lines,
            "file_tokens": self.file_tokens,
            "target_tokens": self.target_tokens,
            "target_ratio": round(self.target_ratio, 4),
            "function_count": self.function_count,
            "class_count": self.class_count,
            "has_module_docstring": self.has_module_docstring,
        }


@dataclass(frozen=True)
class SelectionCriteria:
    """
    The pre-registered rule.

    The defaults describe a task that actually exercises a *small target
    region*: a module of at least ``min_file_lines`` lines whose target
    function is at most ``max_target_ratio`` of the file, with several
    functions and at least one class so that the surrounding context is
    genuinely larger than the edit.
    """

    min_file_lines: int = 200
    max_target_ratio: float = 0.20
    min_function_count: int = 5
    min_class_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_file_lines": self.min_file_lines,
            "max_target_ratio": self.max_target_ratio,
            "min_function_count": self.min_function_count,
            "min_class_count": self.min_class_count,
        }

    def failures(self, properties: TaskProperties) -> List[str]:
        """Returns the properties that disqualify a task (empty = qualifies)."""
        problems: List[str] = []
        if properties.file_lines < self.min_file_lines:
            problems.append(
                f"file_lines={properties.file_lines} < {self.min_file_lines}"
            )
        if properties.target_ratio > self.max_target_ratio:
            problems.append(
                f"target_ratio={properties.target_ratio:.3f} > {self.max_target_ratio}"
            )
        if properties.function_count < self.min_function_count:
            problems.append(
                f"function_count={properties.function_count} < {self.min_function_count}"
            )
        if properties.class_count < self.min_class_count:
            problems.append(
                f"class_count={properties.class_count} < {self.min_class_count}"
            )
        return problems


DEFAULT_CRITERIA = SelectionCriteria()


@dataclass
class SelectionResult:
    """The outcome of applying the rule to a pool."""

    criteria: SelectionCriteria
    selected: List[str] = field(default_factory=list)
    rejected: Dict[str, List[str]] = field(default_factory=dict)
    properties: Dict[str, TaskProperties] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criteria": self.criteria.to_dict(),
            "selected": list(self.selected),
            "rejected": {key: list(value) for key, value in self.rejected.items()},
            "properties": {
                task_id: props.to_dict() for task_id, props in self.properties.items()
            },
            "note": (
                "Selection is by measurable source properties only. No model "
                "result, pass rate or comparative outcome is consulted."
            ),
        }


def _token_count(text: str) -> int:
    try:
        from prompt_manager import get_token_count

        return int(get_token_count(text))
    except Exception:
        # A conservative fallback keeps the rule usable when the tokenizer is
        # unavailable.  It is an approximation of the same order of magnitude.
        return max(1, len(text) // 4)


def compute_task_properties(task: Any) -> TaskProperties:
    """Computes the selection properties of one task object."""
    source = getattr(task, "mutated_source", "") or ""
    target_name = getattr(task, "target_function", "") or ""
    lines = source.splitlines()
    file_tokens = _token_count(source)

    function_count = 0
    class_count = 0
    target_tokens = 0
    has_docstring = False
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_count += 1
            elif isinstance(node, ast.ClassDef):
                class_count += 1
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_name:
                segment = ast.get_source_segment(source, node) or ""
                target_tokens = _token_count(segment)
                break
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(
            tree.body[0].value, ast.Constant
        ) and isinstance(tree.body[0].value.value, str):
            has_docstring = True
    except SyntaxError:
        pass

    ratio = (target_tokens / file_tokens) if file_tokens else 1.0
    return TaskProperties(
        task_id=getattr(task, "task_id", ""),
        file_lines=len(lines),
        file_tokens=file_tokens,
        target_tokens=target_tokens,
        target_ratio=ratio,
        function_count=function_count,
        class_count=class_count,
        has_module_docstring=has_docstring,
    )


def select_tasks(
    tasks: Sequence[Any],
    criteria: SelectionCriteria = DEFAULT_CRITERIA,
) -> SelectionResult:
    """
    Applies the rule to the whole pool.

    The result is order-independent: the selected list is sorted, and the
    rejection report names every excluded task with the reason.
    """
    result = SelectionResult(criteria=criteria)
    for task in tasks:
        properties = compute_task_properties(task)
        result.properties[properties.task_id] = properties
        problems = criteria.failures(properties)
        if problems:
            result.rejected[properties.task_id] = problems
        else:
            result.selected.append(properties.task_id)
    result.selected.sort()
    result.rejected = dict(sorted(result.rejected.items()))
    return result


def write_report(
    out_path: Optional[str] = None,
    criteria: SelectionCriteria = DEFAULT_CRITERIA,
) -> Dict[str, Any]:
    """
    Writes the property/selection report for the approved task set.

    This is a *measurement of the task set*, not a modification of it.
    """
    from benchmarks.benchmark.tasks import TaskRepository

    repository = TaskRepository()
    tasks = repository.select(None)
    result = select_tasks(tasks, criteria)
    payload = {
        "schema_version": "1.0",
        "task_manifest_digest": repository.manifest_digest(),
        "task_count": len(tasks),
        "selected_count": len(result.selected),
        "rejected_count": len(result.rejected),
        "result": result.to_dict(),
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Report the selection properties of the approved task set and the "
            "outcome of the pre-registered Phase 2 selection rule. Read-only."
        )
    )
    parser.add_argument("--out", default=None, help="write the JSON report here")
    parser.add_argument(
        "--min-file-lines", type=int, default=DEFAULT_CRITERIA.min_file_lines
    )
    parser.add_argument(
        "--max-target-ratio", type=float, default=DEFAULT_CRITERIA.max_target_ratio
    )
    parser.add_argument(
        "--min-function-count", type=int, default=DEFAULT_CRITERIA.min_function_count
    )
    parser.add_argument(
        "--min-class-count", type=int, default=DEFAULT_CRITERIA.min_class_count
    )
    args = parser.parse_args(argv)

    criteria = SelectionCriteria(
        min_file_lines=args.min_file_lines,
        max_target_ratio=args.max_target_ratio,
        min_function_count=args.min_function_count,
        min_class_count=args.min_class_count,
    )
    payload = write_report(args.out, criteria)
    print("Phase 2 task-selection report (read-only)")
    print(f"task manifest digest : {payload['task_manifest_digest']}")
    print(f"tasks in the pool     : {payload['task_count']}")
    print(f"criteria              : {payload['result']['criteria']}")
    print(f"selected              : {payload['selected_count']}")
    print(f"rejected              : {payload['rejected_count']}")
    print("")
    print(f"{'task':14s} {'lines':>6s} {'tokens':>7s} {'target':>7s} {'ratio':>6s} {'funcs':>6s} {'classes':>8s}")
    for task_id, props in payload["result"]["properties"].items():
        print(
            f"{task_id:14s} {props['file_lines']:6d} {props['file_tokens']:7d} "
            f"{props['target_tokens']:7d} {props['target_ratio']:6.3f} "
            f"{props['function_count']:6d} {props['class_count']:8d}"
        )
    if payload["selected_count"] == 0:
        print("")
        print(
            "RESULT: no task in the current set satisfies the pre-registered rule. "
            "The set is retained unchanged and is reported as NOT representative "
            "of a small-target-region edit; a new multi-function task family "
            "must be built and validated by the Phase 0 pipeline first."
        )
    else:
        print("")
        print(f"RESULT: selected tasks: {', '.join(payload['result']['selected'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
