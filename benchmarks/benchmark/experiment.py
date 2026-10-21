"""
Experiment orchestration for the Phase 1 benchmark.

The orchestrator owns the schedule, the output directory and the durable
persistence of results.  It never talks to a provider itself: the provider is
injected, so a mock run and a live run share exactly the same control flow.

Scheduling
----------
The schedule is the full cross product

    tasks x methods x repetitions

so the planned experiment is ``20 x 3 x 5 = 300`` attempts.  The execution
order is derived deterministically from ``--seed`` so that two runs with the
same seed issue the same requests in the same order (which matters for
rate-limited providers).

Resumption
----------
Resuming an existing output directory is **refused**: the runner cannot prove
which attempts already reached the provider, so it cannot guarantee that no
request is duplicated.  Start a new output directory instead.
"""

from __future__ import annotations

import json
import os
import platform
import random
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.adapters import METHODS, get_adapter  # noqa: E402
from benchmarks.benchmark.pipeline import AttemptPipeline  # noqa: E402
from benchmarks.benchmark.provider import BenchmarkProvider  # noqa: E402
from benchmarks.benchmark.records import (  # noqa: E402
    AttemptRecord,
    AttemptStatus,
    CsvWriter,
    FailureCategory,
    JsonlWriter,
    write_json,
)
from benchmarks.benchmark.tasks import TaskRepository  # noqa: E402

__all__ = ["AttemptPlan", "ExperimentRunner", "ExperimentError"]


class ExperimentError(RuntimeError):
    """Raised for invalid experiment configuration."""


@dataclass(frozen=True)
class AttemptPlan:
    """One scheduled attempt."""

    task_id: str
    method: str
    rep: int

    @property
    def key(self) -> str:
        return f"{self.task_id}|{self.method}|{self.rep}"


def build_schedule(
    task_ids: Sequence[str],
    methods: Sequence[str],
    reps: int,
    seed: Optional[int] = None,
    shuffle: bool = True,
) -> List[AttemptPlan]:
    """Build the deterministic attempt schedule."""
    if reps < 1:
        raise ExperimentError("reps must be >= 1")
    unknown = [m for m in methods if m not in METHODS]
    if unknown:
        raise ExperimentError(f"unknown methods: {unknown}")

    plans: List[AttemptPlan] = []
    for rep in range(1, reps + 1):
        for task_id in task_ids:
            for method in methods:
                plans.append(AttemptPlan(task_id=task_id, method=method, rep=rep))

    if shuffle and seed is not None:
        rng = random.Random(seed)
        rng.shuffle(plans)
    return plans


class ExperimentRunner:
    """Runs an experiment and writes every attempt durably."""

    def __init__(
        self,
        repository: TaskRepository,
        provider: BenchmarkProvider,
        mode: str,
        output_dir: str,
        methods: Sequence[str] = METHODS,
        reps: int = 5,
        task_ids: Optional[Sequence[str]] = None,
        seed: Optional[int] = None,
        evaluation_timeout: float = 10.0,
        run_id: Optional[str] = None,
        allow_overwrite: bool = False,
        model_id: Optional[str] = None,
    ):
        self.repository = repository
        self.provider = provider
        self.mode = mode
        self.output_dir = os.path.abspath(output_dir)
        self.methods = list(methods)
        self.reps = int(reps)
        self.seed = seed
        self.evaluation_timeout = float(evaluation_timeout)
        self.model_id = model_id or getattr(provider, "model_id", None)
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.allow_overwrite = allow_overwrite
        self._records: List[AttemptRecord] = []
        self._output_paths: Optional[Dict[str, str]] = None
        self._artifact_store = None

        self.task_ids = list(task_ids) if task_ids else self.repository.task_ids
        for task_id in self.task_ids:
            self.repository.get(task_id)  # fail fast on unknown task ids

    # -- planning --------------------------------------------------------
    def plan(self) -> List[AttemptPlan]:
        return build_schedule(self.task_ids, self.methods, self.reps, seed=self.seed)

    def planned_call_count(self) -> int:
        return len(self.task_ids) * len(self.methods) * self.reps

    # -- output directory -------------------------------------------------
    def prepare_output_dir(self) -> None:
        if os.path.exists(self.output_dir) and os.listdir(self.output_dir):
            if not self.allow_overwrite:
                raise ExperimentError(
                    f"output directory already exists and is not empty: "
                    f"{self.output_dir}. Refusing to overwrite it. Use a new "
                    f"directory (or pass --allow-overwrite explicitly)."
                )
        os.makedirs(self.output_dir, exist_ok=True)

    def output_paths(self) -> Dict[str, str]:
        """Return the artefact paths for this run.

        The paths are resolved **once**, when the run starts. When a previous
        run already wrote into this directory, the new run uses run-scoped
        file names, so two runs never share a file and a directory can never
        contain the attempts of one run next to the summary of another.
        """
        if self._output_paths is not None:
            return dict(self._output_paths)
        return self._resolve_output_paths()

    def _resolve_output_paths(self) -> Dict[str, str]:
        from benchmarks.benchmark.artifacts import resolve_artifact_root

        base = self.output_dir
        suffix = ""
        attempts_jsonl = os.path.join(base, "attempts.jsonl")
        if os.path.exists(attempts_jsonl):
            suffix = f"-{self.run_id}"
        return {
            "attempts_jsonl": os.path.join(base, f"attempts{suffix}.jsonl"),
            "attempts_csv": os.path.join(base, f"attempts{suffix}.csv"),
            "summary_json": os.path.join(base, f"summary{suffix}.json"),
            "run_metadata_json": os.path.join(base, f"run_metadata{suffix}.json"),
            # The *actual* artefact root for this run. A re-used output
            # directory gets a run-scoped ``artifacts-<run_id>`` root, so the
            # metadata always points at the directory that really holds the
            # artefacts instead of at a stale shared one.
            "artifacts_dir": resolve_artifact_root(base, self.run_id),
        }

    # -- execution --------------------------------------------------------
    def run(self, progress: bool = True) -> Dict[str, Any]:
        self.prepare_output_dir()
        # The artefact paths are resolved once, at the start of the run, so
        # every artefact of this run belongs to this run.
        self._output_paths = self._resolve_output_paths()
        paths = self._output_paths
        plans = self.plan()
        started = time.perf_counter()

        self._write_metadata(paths, planned_attempts=len(plans))

        jsonl = JsonlWriter(paths["attempts_jsonl"])
        csvw = CsvWriter(paths["attempts_csv"])
        try:
            for index, plan in enumerate(plans, start=1):
                task = self.repository.get(plan.task_id)
                adapter = get_adapter(plan.method)
                pipeline = AttemptPipeline(
                    provider=self.provider,
                    adapter=adapter,
                    mode=self.mode,
                    run_id=self.run_id,
                    evaluation_timeout=self.evaluation_timeout,
                )
                outcome = pipeline.run(task, plan.rep)
                record = outcome.record
                self._persist_artifacts(outcome)
                self._records.append(record)
                jsonl.append(record)
                csvw.append(record)
                if progress:
                    print(
                        f"[{index}/{len(plans)}] {record.task_id} {record.method} "
                        f"rep={record.rep} -> {record.status}"
                        f"{' (mock)' if record.is_mock else ''}",
                        flush=True,
                    )
        finally:
            jsonl.close()
            csvw.close()

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        summary = self._summarise(elapsed_ms)
        write_json(paths["summary_json"], summary)
        return summary

    # -- artefact persistence ---------------------------------------------
    def _persist_artifacts(self, outcome) -> None:
        """Write the per-attempt artefacts and link them from the record.

        A persistence failure is **never** silent: the attempt is demoted to a
        failed/incomplete record so that a missing artefact can never be
        reported as a successful benchmark result, and the error is printed.
        """
        from benchmarks.benchmark.artifacts import ArtifactPersistenceError, ArtifactStore

        if self._artifact_store is None:
            self._artifact_store = ArtifactStore(self.output_dir, self.run_id)
        record = outcome.record
        try:
            info = self._artifact_store.persist(outcome)
        except ArtifactPersistenceError as exc:
            record.artifacts_status = "failed"
            record.status = AttemptStatus.INCOMPLETE
            record.failure_category = FailureCategory.INTERNAL_ERROR
            record.error_message = f"artifact persistence failed: {exc}"
            # A partially written attempt directory is kept, but it is made
            # explicitly discoverable through the record instead of being
            # left as an orphan. The attempt is not a success either way.
            try:
                record.artifacts_path = self._artifact_store.relative_attempt_dir(
                    record.attempt_id
                )
            except Exception:
                record.artifacts_path = None
            print(f"[artifact error] {record.attempt_id}: {exc}", file=sys.stderr)
            return
        except Exception as exc:  # pragma: no cover - defensive
            record.artifacts_status = "failed"
            record.status = AttemptStatus.INCOMPLETE
            record.failure_category = FailureCategory.INTERNAL_ERROR
            record.error_message = f"artifact persistence failed: {type(exc).__name__}: {exc}"
            try:
                record.artifacts_path = self._artifact_store.relative_attempt_dir(
                    record.attempt_id
                )
            except Exception:
                record.artifacts_path = None
            print(f"[artifact error] {record.attempt_id}: {exc}", file=sys.stderr)
            return

        for key, value in info.items():
            if hasattr(record, key):
                setattr(record, key, value)
        if record.evaluation_attempted is None and outcome.evaluation is not None:
            record.evaluation_attempted = bool(outcome.evaluation.get("attempted"))

    # -- outputs -----------------------------------------------------------
    def _write_metadata(self, paths: Dict[str, str], planned_attempts: int) -> None:
        from benchmarks.benchmark.analysis import BOOTSTRAP_SEED

        provider = self.provider
        metadata = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "created_at_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            # -- mode and provider -----------------------------------------
            "mode": self.mode,
            "declared_provider": getattr(provider, "provider", None)
            or getattr(provider, "name", None),
            "effective_provider": getattr(provider, "effective_provider", None),
            "is_measurement": self.mode == "live",
            # -- model identity ---------------------------------------------
            "requested_model_id": self.model_id,
            # A served model id only exists once a provider has answered; it is
            # never invented here. The observed values are listed in the
            # summary under "observed_served_model_ids".
            "served_model_id": None,
            # -- generation configuration (planned) -------------------------
            "temperature": getattr(provider, "temperature", None),
            "max_output_tokens": getattr(provider, "max_output_tokens", None),
            "request_timeout_seconds": getattr(provider, "request_timeout", None),
            "max_retries": getattr(provider, "max_retries", None),
            "retry_policy": (
                "bounded; transient HTTP 429 only; HTTP 402 and daily/per-minute "
                "quota caps are never retried"
            ),
            "evaluation_timeout_seconds": self.evaluation_timeout,
            # -- schedule ---------------------------------------------------
            "reps": self.reps,
            "tasks": list(self.task_ids),
            "task_count": len(self.task_ids),
            "methods": list(self.methods),
            "run_order_seed": self.seed,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "planned_attempts": planned_attempts,
            "task_manifest_digest": self.repository.manifest_digest(),
            # -- environment -------------------------------------------------
            "python_version": sys.version,
            "platform": platform.platform(),
            "software_versions": _software_versions(),
            "artifact_files": {
                key: os.path.relpath(value, self.output_dir)
                for key, value in paths.items()
                if key != "artifacts_dir"
            },
            "artifacts_dir": os.path.relpath(paths["artifacts_dir"], self.output_dir),
            "notes": [
                "This benchmark evaluates three editing strategies of this project "
                "on synthetic HumanEval-derived repair tasks. It is not the "
                "HumanEval benchmark and produces no HumanEval pass rate.",
                "Mock-mode runs are pipeline tests only and are not measurements.",
                "The evaluation sandbox is best-effort isolation, not a security "
                "boundary: filesystem and subprocess containment are absent and "
                "the socket guard can be bypassed by a child process.",
            ]
            + (
                [
                    "This run used the deterministic mock engine, which performs no "
                    "inference. temperature / max_output_tokens / "
                    "request_timeout_seconds / max_retries are therefore null: the "
                    "mock has no sampling parameters, and no value is invented."
                ]
                if self.mode != "live"
                else []
            ),
        }
        write_json(paths["run_metadata_json"], metadata)

    def _summarise(self, elapsed_ms: float) -> Dict[str, Any]:
        from benchmarks.benchmark.analysis import (
            method_metrics,
            paired_task_comparison,
            summarise_attempts,
        )

        summary = summarise_attempts(self._records)
        summary["run_id"] = self.run_id
        summary["mode"] = self.mode
        summary["elapsed_wall_clock_ms"] = round(elapsed_ms, 3)
        summary["planned_attempts"] = self.planned_call_count()
        summary["recorded_attempts"] = len(self._records)
        summary["is_measurement"] = self.mode == "live"
        summary["mock_warning"] = (
            "MOCK RUN — pipeline test only, not a measurement of model quality."
            if self.mode == "mock"
            else None
        )

        tasks_count = len(self.task_ids)
        summary["method_metrics"] = {
            method: method_metrics(
                self._records,
                scheduled=tasks_count * self.reps,
                method=method,
                tasks_count=tasks_count,
                reps=self.reps,
            )
            for method in self.methods
        }
        comparisons = []
        for i, method_a in enumerate(self.methods):
            for method_b in self.methods[i + 1 :]:
                comparisons.append(
                    paired_task_comparison(
                        self._records, method_a, method_b, list(self.task_ids)
                    )
                )
        summary["paired_task_comparisons"] = comparisons
        summary["artifact_files"] = {
            key: os.path.relpath(value, self.output_dir)
            for key, value in self.output_paths().items()
            if key != "artifacts_dir"
        }
        summary["artifacts_dir"] = os.path.relpath(
            self.output_paths()["artifacts_dir"], self.output_dir
        )
        summary["artifacts_persisted"] = sum(
            1 for r in self._records if r.artifacts_status == "ok"
        )
        summary["artifacts_failed"] = sum(
            1 for r in self._records if r.artifacts_status == "failed"
        )
        summary["candidates_persisted"] = sum(
            1 for r in self._records if r.candidate_state == "produced"
        )
        return summary


def _software_versions() -> Dict[str, Optional[str]]:
    versions: Dict[str, Optional[str]] = {"python": sys.version.split()[0]}
    for module_name in ("httpx", "pytest", "tiktoken", "dotenv"):
        try:
            module = __import__(module_name)
            versions[module_name] = getattr(module, "__version__", "unknown")
        except Exception:
            versions[module_name] = None
    return versions
