"""
The experiment runner for the ITMS vs Aider vs Cline comparison.

Guarantees that matter for the fairness of the comparison:

* every system gets the **same task instruction** and the **same starting
  file** (``candidate.py``, byte-identical across systems);
* the **hidden official test is not present in the workspace** while the
  system runs, and is copied in only afterwards, for evaluation;
* every system is graded by the **same evaluator**
  (``benchmarks.phase0.runner.run_official_test``, isolated subprocess with
  sockets blocked);
* every system gets the **same wall-clock timeout policy**;
* the results directory is **new**: this runner never appends to, and never
  rewrites, the historical 120-attempt dataset under ``pilot_runs/`` or
  anything under ``results/``;
* a failed *execution* (the tool never produced a candidate) is recorded
  separately from an *evaluator failure* (the candidate ran and failed the
  hidden test).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmarks.phase0.runner import run_official_test  # noqa: E402

from .config import ExperimentConfig, load_manifest  # noqa: E402
from .systems import (  # noqa: E402
    AiderSystemAdapter,
    ClineSystemAdapter,
    FailureCategory,
    ItmsSystemAdapter,
    MockSystemAdapter,
    SystemAdapter,
    SystemOutcome,
    TaskBundle,
)

# ---------------------------------------------------------------------------
# record schema
# ---------------------------------------------------------------------------

RECORD_COLUMNS: List[str] = [
    "attempt_id",
    "run_id",
    "timestamp_utc",
    "experiment_id",
    "task_id",
    "humaneval_task_id",
    "system",
    "method",
    "repetition",
    "repetition_id",
    "mode",
    "is_mock",
    "provider",
    "requested_model_id",
    "served_model_id",
    "seed_policy",
    "seed_requested",
    "seed_supported",
    "seed_applied",
    "execution_completed",
    "execution_error",
    "exit_code",
    "timed_out",
    "latency_ms",
    "patch_applied",
    "syntax_valid",
    "evaluation_attempted",
    "test_passed",
    "test_error_type",
    "test_error_message",
    "test_duration_ms",
    "status",
    "failure_category",
    "failure_reason",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "usage_source",
    "cost_usd",
    "cost_source",
    "telemetry_source",
    "candidate_sha256",
    "candidate_path",
    "artifacts_path",
    "artifacts_status",
    "raw_response_sha256",
    "evaluation_timeout_seconds",
    "system_timeout_seconds",
    "python_version",
    "platform",
]


@dataclass(frozen=True)
class AttemptPlan:
    """One scheduled cell: a (task, system, repetition) triple."""

    task_id: str
    system: str
    repetition: int

    @property
    def repetition_id(self) -> str:
        return f"{self.task_id}::{self.system}::rep{self.repetition:02d}"

    @property
    def key(self) -> str:
        return self.repetition_id


def build_schedule(
    task_ids: List[str],
    systems: List[str],
    repetitions: int,
    seed: int,
    shuffle: bool = True,
) -> List[AttemptPlan]:
    """Full cross product, deterministically ordered."""
    plans = [
        AttemptPlan(task_id=task_id, system=system, repetition=rep)
        for task_id in task_ids
        for system in systems
        for rep in range(1, repetitions + 1)
    ]
    if shuffle:
        random.Random(seed).shuffle(plans)
    return plans


def attempt_id_for(plan: AttemptPlan, run_id: str) -> str:
    """Stable, collision-free attempt id derived from the cell identity."""
    digest = hashlib.sha256(f"{run_id}|{plan.key}".encode("utf-8")).hexdigest()
    return digest[:32]


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


class SpendingLimitReached(RuntimeError):
    """Raised when the configured maximum spend has been reached."""


class ExperimentRunner:
    def __init__(
        self,
        config: ExperimentConfig,
        manifest: Dict[str, Any],
        adapters: Dict[str, SystemAdapter],
        output_dir: str,
        mode: str = "live",
        run_id: Optional[str] = None,
    ) -> None:
        self.config = config
        self.manifest = manifest
        self.adapters = adapters
        self.output_dir = os.path.abspath(output_dir)
        self.mode = mode
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._tasks = {t["task_id"]: t for t in manifest["tasks"]}
        #: cumulative cost in USD, summed from *reported* cost only.
        self.cumulative_spend_usd = 0.0
        #: cells that were not executed because the spending limit was hit
        self.not_executed_cells: List[str] = []
        #: set when the spending limit was reached during the run
        self.spending_limit_error: Optional[str] = None

    # -- budget gate ------------------------------------------------------

    def check_budget_preflight(self) -> Dict[str, Any]:
        """Validate the spend configuration before any provider call.

        Raises when the configuration would allow an unbounded or
        unexpectedly expensive run.  This is the Phase 2 budget gate: it
        runs *before* the first live call, and it never consults a network
        resource.
        """
        cap = float(self.config.max_spend_usd)
        if cap <= 0:
            raise SpendingLimitReached(
                "max_spend_usd must be a positive explicit limit; "
                f"got {self.config.max_spend_usd!r}"
            )
        model_id = (self.config.model_id or "").strip()
        if not model_id:
            raise SpendingLimitReached("model_id is not configured")
        if self.config.require_free_model and not model_id.endswith(":free"):
            raise SpendingLimitReached(
                f"require_free_model is enabled and model_id {model_id!r} is not a "
                "':free' model id; refusing to start an unbounded-spend run"
            )
        return {
            "max_spend_usd": cap,
            "require_free_model": self.config.require_free_model,
            "model_id": model_id,
            "model_is_free": model_id.endswith(":free"),
            "expected_max_cost_usd": 0.0 if model_id.endswith(":free") else cap,
        }

    def _charge(self, cost_usd: Optional[float]) -> None:
        """Add a reported cost to the running total."""
        if cost_usd is None:
            return
        try:
            self.cumulative_spend_usd += float(cost_usd)
        except (TypeError, ValueError):
            return
        if self.cumulative_spend_usd >= float(self.config.max_spend_usd):
            raise SpendingLimitReached(
                f"cumulative reported cost ${self.cumulative_spend_usd:.4f} reached the "
                f"configured maximum of ${self.config.max_spend_usd:.4f}"
            )

    # -- paths ------------------------------------------------------------

    @property
    def attempts_jsonl(self) -> str:
        return os.path.join(self.output_dir, "attempts.jsonl")

    @property
    def attempts_csv(self) -> str:
        return os.path.join(self.output_dir, "attempts.csv")

    @property
    def artifacts_dir(self) -> str:
        return os.path.join(self.output_dir, "artifacts")

    @property
    def metadata_path(self) -> str:
        return os.path.join(self.output_dir, "run_metadata.json")

    @property
    def summary_path(self) -> str:
        return os.path.join(self.output_dir, "summary.json")

    # -- schedule ---------------------------------------------------------

    def plan(self) -> List[AttemptPlan]:
        return build_schedule(
            self.config.task_ids,
            self.config.systems,
            self.config.repetitions,
            self.config.run_order_seed,
        )

    def planned_call_count(self) -> int:
        return len(self.config.task_ids) * len(self.config.systems) * self.config.repetitions

    # -- helpers ----------------------------------------------------------

    def _task_bundle(self, task_id: str) -> TaskBundle:
        entry = self._tasks[task_id]
        return TaskBundle(
            task_id=entry["task_id"],
            humaneval_task_id=entry["source"]["humaneval_task_id"],
            target_function=entry["source"]["entry_point"],
            problem_prompt=entry["task_description"]["problem_prompt"],
            repair_instruction=entry["task_description"]["repair_instruction"],
            mutated_source=_read_text(
                os.path.join(REPO_ROOT, entry["task_description"]["mutated_source_path"])
            ),
            mutated_source_path=os.path.join(
                REPO_ROOT, entry["task_description"]["mutated_source_path"]
            ),
            official_test_path=os.path.join(REPO_ROOT, entry["official_test"]["path"]),
        )

    def _prepare_workspace(self, bundle: TaskBundle) -> str:
        """Create a fresh workspace outside the git repository.

        The workspace lives under the system temp dir on purpose: Aider and
        Cline both walk upwards looking for a repository root, and a
        workspace inside this repository would expose the whole project to
        the editing agent.
        """
        workspace = tempfile.mkdtemp(prefix=f"itms_cmp_{self.config.experiment_id}_")
        target = os.path.join(workspace, bundle.workspace_filename)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(bundle.mutated_source)
        return workspace

    # -- execution --------------------------------------------------------

    def run(self, progress: bool = True) -> Dict[str, Any]:
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.artifacts_dir, exist_ok=True)

        # The budget gate runs before the first provider call.
        budget = self.check_budget_preflight()
        if self.mode == "live":
            print(
                f"[budget] max_spend_usd={budget['max_spend_usd']} "
                f"model={budget['model_id']} free_model={budget['model_is_free']}",
                flush=True,
            )

        # Write the metadata up front so that an in-progress run is
        # discoverable and its configuration is on disk even if the process
        # is interrupted. It is rewritten with the final values at the end.
        _write_json(self.metadata_path, self._metadata())

        plans = self.plan()
        records: List[Dict[str, Any]] = []
        started = time.perf_counter()
        seen_ids: set = set()

        for index, plan in enumerate(plans, start=1):
            if self.spending_limit_error is not None:
                self.not_executed_cells.append(plan.key)
                continue
            record = self._run_one(plan, seen_ids)
            records.append(record)
            with open(self.attempts_jsonl, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._append_csv(record)
            if progress:
                print(
                    f"[{index}/{len(plans)}] {plan.key} -> "
                    f"{record['status']} ({record['failure_category']})",
                    flush=True,
                )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        summary = self._summarise(records, elapsed_ms)
        _write_json(self.summary_path, summary)
        _write_json(self.metadata_path, self._metadata())
        return summary

    def _run_one(self, plan: AttemptPlan, seen_ids: set) -> Dict[str, Any]:
        attempt_id = attempt_id_for(plan, self.run_id)
        if attempt_id in seen_ids:  # pragma: no cover - guarded by the tests
            raise RuntimeError(f"duplicate attempt id generated for {plan.key}")
        seen_ids.add(attempt_id)

        adapter = self.adapters[plan.system]
        bundle = self._task_bundle(plan.task_id)
        seed = self.config.seed_for(plan.task_id, plan.system, plan.repetition)

        workspace = self._prepare_workspace(bundle)
        outcome = SystemOutcome(
            system=plan.system,
            task_id=plan.task_id,
            repetition_id=plan.repetition_id,
        )
        try:
            outcome = adapter.run(bundle, workspace, plan.repetition_id, seed)
        except Exception as exc:  # noqa: BLE001 - a crashed adapter is a failed execution
            outcome = SystemOutcome(
                system=plan.system,
                task_id=plan.task_id,
                repetition_id=plan.repetition_id,
                seed_requested=seed,
                seed_supported=adapter.supports_seed,
                seed_applied=seed if adapter.supports_seed else None,
                execution_completed=False,
                execution_error=f"{type(exc).__name__}: {exc}",
                failure_category=FailureCategory.INTERNAL_ERROR,
                failure_reason=str(exc)[:500],
            )
        finally:
            _copy_workspace_outputs(workspace, self.artifacts_dir, attempt_id)

        evaluation, evaluation_attempted = self._evaluate(outcome, bundle)
        record = self._build_record(
            plan=plan,
            attempt_id=attempt_id,
            adapter=adapter,
            outcome=outcome,
            evaluation=evaluation,
            evaluation_attempted=evaluation_attempted,
        )
        self._persist_artifacts(attempt_id, record, outcome, evaluation)
        shutil.rmtree(workspace, ignore_errors=True)
        # Charge the reported cost against the explicit spending limit.  A
        # breach stops the run *after* this attempt has been fully recorded,
        # so no attempt is ever lost.
        try:
            self._charge(record.get("cost_usd"))
        except SpendingLimitReached as exc:
            self.spending_limit_error = str(exc)
        return record

    def _evaluate(self, outcome: SystemOutcome, bundle: TaskBundle):
        """Run the hidden official test against the candidate, if any.

        A candidate that does not parse is never evaluated: the hidden test
        cannot distinguish "the edit was malformed" from "the repair was
        wrong", and the upstream Phase 1 pipeline uses the same rule.
        """
        if not outcome.execution_completed or not outcome.candidate_source:
            return None, False
        if outcome.syntax_valid is False:
            return None, False
        test_code = _read_text(bundle.official_test_path)
        if test_code is None:
            return None, False
        try:
            result = run_official_test(
                outcome.candidate_source,
                test_code,
                bundle.target_function,
                timeout=self.config.evaluation_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                {
                    "passed": False,
                    "exit_code": -1,
                    "timed_out": False,
                    "error_type": "EvaluatorError",
                    "error_message": str(exc)[:500],
                    "duration_ms": 0.0,
                },
                True,
            )
        return result.to_dict(), True

    def _build_record(
        self,
        plan: AttemptPlan,
        attempt_id: str,
        adapter: SystemAdapter,
        outcome: SystemOutcome,
        evaluation: Optional[Dict[str, Any]],
        evaluation_attempted: bool,
    ) -> Dict[str, Any]:
        status, failure_category, failure_reason = _classify(
            outcome, evaluation, evaluation_attempted
        )
        return {
            "attempt_id": attempt_id,
            "run_id": self.run_id,
            "timestamp_utc": _utc_now(),
            "experiment_id": self.config.experiment_id,
            "task_id": plan.task_id,
            "humaneval_task_id": self._tasks[plan.task_id]["source"]["humaneval_task_id"],
            "system": plan.system,
            "method": plan.system,
            "repetition": plan.repetition,
            "repetition_id": plan.repetition_id,
            "mode": self.mode,
            "is_mock": bool(outcome.extra.get("is_mock")) or self.mode == "mock",
            "provider": self.config.provider,
            "requested_model_id": self.config.model_id,
            "served_model_id": outcome.served_model_id,
            "seed_policy": self.config.seed_policy,
            "seed_requested": outcome.seed_requested,
            "seed_supported": outcome.seed_supported,
            "seed_applied": outcome.seed_applied,
            "execution_completed": outcome.execution_completed,
            "execution_error": outcome.execution_error,
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "latency_ms": round(outcome.latency_ms, 2),
            "patch_applied": outcome.patch_applied,
            "syntax_valid": outcome.syntax_valid,
            "evaluation_attempted": evaluation_attempted,
            "test_passed": (evaluation or {}).get("passed") if evaluation_attempted else None,
            "test_error_type": (evaluation or {}).get("error_type") if evaluation_attempted else None,
            "test_error_message": (
                (evaluation or {}).get("error_message") if evaluation_attempted else None
            ),
            "test_duration_ms": (evaluation or {}).get("duration_ms") if evaluation_attempted else None,
            "status": status,
            "failure_category": failure_category,
            "failure_reason": failure_reason,
            "prompt_tokens": outcome.prompt_tokens,
            "completion_tokens": outcome.completion_tokens,
            "total_tokens": outcome.total_tokens,
            "cache_read_tokens": outcome.cache_read_tokens,
            "cache_write_tokens": outcome.cache_write_tokens,
            "usage_source": outcome.usage_source,
            "cost_usd": outcome.cost_usd,
            "cost_source": outcome.cost_source,
            "telemetry_source": outcome.telemetry_source,
            "candidate_sha256": outcome.candidate_sha256,
            "candidate_path": None,
            "artifacts_path": os.path.join("artifacts", attempt_id),
            "artifacts_status": "pending",
            "raw_response_sha256": None,
            "evaluation_timeout_seconds": self.config.evaluation_timeout_seconds,
            "system_timeout_seconds": self.config.system_timeout_seconds,
            "python_version": sys.version.split()[0],
            "platform": _platform(),
        }

    def _persist_artifacts(
        self,
        attempt_id: str,
        record: Dict[str, Any],
        outcome: SystemOutcome,
        evaluation: Optional[Dict[str, Any]],
    ) -> None:
        attempt_dir = os.path.join(self.artifacts_dir, attempt_id)
        os.makedirs(attempt_dir, exist_ok=True)
        _write_json(os.path.join(attempt_dir, "attempt.json"), {
            "record": record,
            "system_outcome": outcome.to_dict(),
            "evaluation": evaluation,
        })
        if outcome.candidate_source is not None:
            candidate_path = os.path.join(attempt_dir, "candidate.py")
            with open(candidate_path, "w", encoding="utf-8") as fh:
                fh.write(outcome.candidate_source)
            record["candidate_path"] = os.path.relpath(candidate_path, self.output_dir)
            record["candidate_sha256"] = outcome.candidate_sha256
        if outcome.stdout_path and os.path.exists(outcome.stdout_path):
            shutil.copyfile(outcome.stdout_path, os.path.join(attempt_dir, "stdout.log"))
        if outcome.stderr_path and os.path.exists(outcome.stderr_path):
            shutil.copyfile(outcome.stderr_path, os.path.join(attempt_dir, "stderr.log"))
        record["artifacts_status"] = "ok"

    def _append_csv(self, record: Dict[str, Any]) -> None:
        """Append one record to the CSV mirror, with a stable column order."""
        new_file = not os.path.exists(self.attempts_csv) or os.path.getsize(self.attempts_csv) == 0
        with open(self.attempts_csv, "a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=RECORD_COLUMNS, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            writer.writerow({k: record.get(k) for k in RECORD_COLUMNS})
            fh.flush()
            os.fsync(fh.fileno())

    def _metadata(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "experiment_id": self.config.experiment_id,
            "started_at_utc": _utc_now(),
            "mode": self.mode,
            "is_measurement": self.mode == "live",
            "declared_provider": self.config.provider,
            "requested_model_id": self.config.model_id,
            "served_model_id": None,
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_output_tokens,
            "system_timeout_seconds": self.config.system_timeout_seconds,
            "evaluation_timeout_seconds": self.config.evaluation_timeout_seconds,
            "request_timeout_seconds": self.config.request_timeout_seconds,
            "repetitions": self.config.repetitions,
            "systems": self.config.systems,
            "tasks": self.config.task_ids,
            "task_count": len(self.config.task_ids),
            "planned_attempts": self.planned_call_count(),
            "run_order_seed": self.config.run_order_seed,
            "bootstrap_seed": self.config.bootstrap_seed,
            "seed_policy": self.config.seed_policy,
            "seed_base": self.config.seed_base,
            "max_spend_usd": self.config.max_spend_usd,
            "require_free_model": self.config.require_free_model,
            "cumulative_reported_spend_usd": round(self.cumulative_spend_usd, 6),
            "spending_limit_reached": self.spending_limit_error,
            "task_manifest_digest": self.manifest.get("task_manifest_digest"),
            "config": self.config.to_dict(),
            "system_configurations": {
                name: adapter.describe_configuration()
                for name, adapter in self.adapters.items()
            },
            "python_version": sys.version.split()[0],
            "platform": _platform(),
            "notes": [
                "This run compares three REAL systems: the ITMS agent, the "
                "aider CLI and the cline CLI.",
                "Scores are NOT HumanEval scores. The tasks are synthetic "
                "single-mutation repair tasks derived from pinned HumanEval "
                "problems.",
                "The historical 120-attempt dataset under pilot_runs/ is "
                "immutable and was not read, appended to, or rewritten.",
            ],
        }

    def _summarise(self, records: List[Dict[str, Any]], elapsed_ms: float) -> Dict[str, Any]:
        by_system: Dict[str, Any] = {}
        for system in self.config.systems:
            subset = [r for r in records if r["system"] == system]
            scheduled = len(self.config.task_ids) * self.config.repetitions
            evaluated = [r for r in subset if r["evaluation_attempted"]]
            passed = [r for r in subset if r["test_passed"] is True]
            by_system[system] = {
                "scheduled": scheduled,
                "recorded": len(subset),
                "execution_completed": sum(1 for r in subset if r["execution_completed"]),
                "execution_failures": sum(1 for r in subset if not r["execution_completed"]),
                "evaluated": len(evaluated),
                "test_passed": len(passed),
                "test_failed": len(evaluated) - len(passed),
                "syntax_errors": sum(1 for r in subset if r["failure_category"] == "syntax_error"),
                "patch_not_applied": sum(
                    1 for r in subset if r["failure_category"] == "patch_not_applied"
                ),
                "timeouts": sum(1 for r in subset if r["timed_out"]),
                "failure_categories": _counter(r["failure_category"] for r in subset),
                "status_counts": _counter(r["status"] for r in subset),
                "seed_supported": bool(subset and subset[0]["seed_supported"]),
                "tokens": {
                    "attempts_with_provider_or_tool_reported_usage": sum(
                        1 for r in subset if r["usage_source"] in ("provider_reported", "tool_reported")
                    ),
                    "attempts_without_usage": sum(1 for r in subset if r["total_tokens"] is None),
                    "total_tokens": sum(r["total_tokens"] or 0 for r in subset),
                },
                "cost": {
                    "attempts_with_reported_cost": sum(1 for r in subset if r["cost_usd"] is not None),
                    "total_cost_usd": sum(r["cost_usd"] or 0.0 for r in subset),
                },
                "latency_ms": {
                    "mean": round(
                        sum(r["latency_ms"] for r in subset) / len(subset), 2
                    ) if subset else None,
                    "max": round(max((r["latency_ms"] for r in subset), default=0.0), 2),
                },
            }
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "mode": self.mode,
            "is_measurement": self.mode == "live",
            "planned_attempts": self.planned_call_count(),
            "recorded_attempts": len(records),
            "not_executed_cells": len(self.not_executed_cells),
            "not_executed_cell_keys": self.not_executed_cells,
            "spending_limit": {
                "max_spend_usd": self.config.max_spend_usd,
                "cumulative_reported_spend_usd": round(self.cumulative_spend_usd, 6),
                "limit_reached": self.spending_limit_error is not None,
                "limit_reached_reason": self.spending_limit_error,
                "require_free_model": self.config.require_free_model,
            },
            "elapsed_wall_clock_ms": round(elapsed_ms, 2),
            "systems": by_system,
            "status_counts": _counter(r["status"] for r in records),
            "failure_categories": _counter(r["failure_category"] for r in records),
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _classify(
    outcome: SystemOutcome,
    evaluation: Optional[Dict[str, Any]],
    evaluation_attempted: bool,
) -> Tuple[str, str, Optional[str]]:
    """Separate *execution* failures from *evaluator* failures."""
    if not outcome.execution_completed:
        return (
            "execution_failed",
            outcome.failure_category or FailureCategory.INTERNAL_ERROR,
            outcome.failure_reason or outcome.execution_error,
        )
    if not outcome.candidate_source:
        return (
            "invalid_candidate",
            FailureCategory.NO_CANDIDATE,
            outcome.failure_reason or "no candidate was produced",
        )
    if outcome.syntax_valid is False:
        return (
            "invalid_candidate",
            FailureCategory.SYNTAX_ERROR,
            outcome.failure_reason or "candidate did not parse",
        )
    if not evaluation_attempted:
        return (
            "invalid_candidate",
            FailureCategory.NO_CANDIDATE,
            outcome.failure_reason or "candidate was not evaluated",
        )
    if evaluation and evaluation.get("passed"):
        return "repair_success", FailureCategory.NONE, None
    return (
        "repair_failed",
        FailureCategory.TEST_FAILED,
        (evaluation or {}).get("error_message"),
    )


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")


def _copy_workspace_outputs(workspace: str, artifacts_dir: str, attempt_id: str) -> None:
    """Copy any tool logs the adapter left in the workspace."""
    target = os.path.join(artifacts_dir, attempt_id)
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(workspace):
        if not name.endswith((".log", ".jsonl")):
            continue
        try:
            shutil.copyfile(os.path.join(workspace, name), os.path.join(target, name))
        except OSError:
            pass


def _counter(values) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _utc_now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _platform() -> str:
    import platform

    return platform.platform()


def build_adapters(
    config: ExperimentConfig,
    mode: str,
    api_key: Optional[str] = None,
) -> Dict[str, SystemAdapter]:
    """Instantiate the adapters for the requested mode.

    ``mode='mock'`` returns offline mocks so the dry run never touches the
    network.  ``mode='live'`` returns the real systems.
    """
    if mode == "mock":
        return {name: MockSystemAdapter(behaviour="mixed") for name in config.systems}

    adapters: Dict[str, SystemAdapter] = {}
    for name in config.systems:
        if name == "itms":
            adapters[name] = ItmsSystemAdapter(
                model_id=config.model_id,
                provider=config.provider,
                temperature=config.temperature,
                max_output_tokens=config.max_output_tokens,
                request_timeout=config.request_timeout_seconds,
                api_key=api_key,
            )
        elif name == "aider":
            adapters[name] = AiderSystemAdapter(
                model_id=config.model_id,
                provider=config.provider,
                temperature=config.temperature,
                timeout_seconds=config.system_timeout_seconds,
                api_key=api_key,
            )
        elif name == "cline":
            adapters[name] = ClineSystemAdapter(
                model_id=config.model_id,
                provider=config.provider,
                timeout_seconds=config.system_timeout_seconds,
                api_key=api_key,
            )
        else:
            raise ValueError(f"unknown system '{name}'")
    return adapters


def run_experiment(
    config: ExperimentConfig,
    manifest: Dict[str, Any],
    output_dir: str,
    mode: str = "live",
    api_key: Optional[str] = None,
    progress: bool = True,
) -> Dict[str, Any]:
    adapters = build_adapters(config, mode, api_key=api_key)
    runner = ExperimentRunner(
        config=config,
        manifest=manifest,
        adapters=adapters,
        output_dir=output_dir,
        mode=mode,
    )
    return runner.run(progress=progress)
