"""
Task repository for the Phase 1 benchmark.

The repository exposes **only** what a model is allowed to see:

*   the model-facing task payload (``benchmarks/data/model_facing_tasks.json``),
*   the mutated source file of a task, loaded through an explicit
    ``task_id -> path`` mapping,
*   the official test of a task, used for evaluation only and never placed in
    a prompt.

The gold solutions (``benchmarks/data/gold_solutions.json`` and
``benchmarks/data/tasks/<id>/original_source.py``) are deliberately *not*
reachable through this class, so a benchmark bug cannot leak a reference
solution into a prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

__all__ = ["BenchmarkTask", "TaskRepository", "TaskRepositoryError"]


class TaskRepositoryError(RuntimeError):
    """Raised when a task cannot be resolved safely."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class BenchmarkTask:
    """A single model-facing repair task."""

    task_id: str
    humaneval_task_id: str
    target_function: str
    problem_prompt: str
    repair_instruction: str
    mutated_source: str
    mutated_source_path: str
    official_test: str
    official_test_path: str
    mutated_source_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "humaneval_task_id": self.humaneval_task_id,
            "target_function": self.target_function,
            "problem_prompt": self.problem_prompt,
            "repair_instruction": self.repair_instruction,
            "mutated_source": self.mutated_source,
            "mutated_source_path": self.mutated_source_path,
            "official_test_path": self.official_test_path,
            "mutated_source_sha256": self.mutated_source_sha256,
        }


class TaskRepository:
    """
    Loads the approved model-facing task set.

    The repository verifies that the mutated source it serves matches the
    checksum recorded by Phase 0, so a tampered task file is detected before
    any attempt is made.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        model_facing_path: Optional[str] = None,
    ):
        self.data_dir = os.path.abspath(data_dir or os.path.join(_ROOT, "benchmarks", "data"))
        self.model_facing_path = os.path.abspath(
            model_facing_path or os.path.join(self.data_dir, "model_facing_tasks.json")
        )
        self._tasks: Dict[str, BenchmarkTask] = {}
        self._load()

    # -- loading --------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.model_facing_path):
            raise TaskRepositoryError(
                f"model-facing task payload not found: {self.model_facing_path}"
            )
        with open(self.model_facing_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)

        tasks_dir = os.path.join(self.data_dir, "tasks")
        for entry in payload.get("tasks", []):
            task_id = entry["task_id"]
            mutated_path = os.path.join(tasks_dir, task_id, "mutated_source.py")
            test_path = os.path.join(tasks_dir, task_id, "official_test.py")
            if not os.path.exists(mutated_path):
                raise TaskRepositoryError(
                    f"task {task_id}: mutated source not found at {mutated_path}"
                )
            if not os.path.exists(test_path):
                raise TaskRepositoryError(
                    f"task {task_id}: official test not found at {test_path}"
                )
            with open(mutated_path, "r", encoding="utf-8") as fh:
                mutated = fh.read()
            with open(test_path, "r", encoding="utf-8") as fh:
                official_test = fh.read()

            expected = entry.get("checksums", {}).get("mutated_source_sha256")
            actual = _sha256_text(mutated)
            if expected and expected != actual:
                raise TaskRepositoryError(
                    f"task {task_id}: mutated source checksum mismatch "
                    f"(expected {expected}, got {actual})"
                )
            if mutated != entry.get("mutated_source"):
                raise TaskRepositoryError(
                    f"task {task_id}: mutated source on disk differs from the "
                    f"approved model-facing payload"
                )

            self._tasks[task_id] = BenchmarkTask(
                task_id=task_id,
                humaneval_task_id=entry["humaneval_task_id"],
                target_function=entry["target_function"],
                problem_prompt=entry["problem_prompt"],
                repair_instruction=entry["repair_instruction"],
                mutated_source=mutated,
                mutated_source_path=mutated_path,
                official_test=official_test,
                official_test_path=test_path,
                mutated_source_sha256=actual,
            )

    # -- access ---------------------------------------------------------
    @property
    def task_ids(self) -> List[str]:
        return sorted(self._tasks.keys())

    def get(self, task_id: str) -> BenchmarkTask:
        if task_id not in self._tasks:
            raise TaskRepositoryError(f"unknown task id: {task_id}")
        return self._tasks[task_id]

    def select(self, task_ids: Optional[List[str]] = None) -> List[BenchmarkTask]:
        if not task_ids:
            return [self._tasks[t] for t in self.task_ids]
        selected = []
        for task_id in task_ids:
            selected.append(self.get(task_id))
        return selected

    def manifest_digest(self) -> str:
        """Digest of the approved task set, used to pin a run's inputs."""
        digest = hashlib.sha256()
        for task_id in self.task_ids:
            task = self._tasks[task_id]
            digest.update(task.task_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(task.mutated_source_sha256.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()
