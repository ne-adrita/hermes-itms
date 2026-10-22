import time
import csv
import json
import os
import sys
import argparse
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from benchmarks.phase0.runner import run_official_test
from relay_orchestrator import RelayOrchestrator, CascadeRunResult
from sandbox import ExecutionHarness
from model_dispatcher import ModelDispatcher, LiveModeError, ModelConfigurationError

# --------------------------------------------------------------------------
# Phase 0 task set (the 20 approved HE-EDIT-* repair tasks)
# --------------------------------------------------------------------------

#: Root of the Phase 0 task artefacts.  The harness reads the *model-facing*
#: payload and the per-task files below it; it never reads the reference
#: solutions.
_PHASE0_DATA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "benchmarks", "data"
)
_MODEL_FACING_TASKS = os.path.join(_PHASE0_DATA_DIR, "model_facing_tasks.json")
_TASKS_DIR = os.path.join(_PHASE0_DATA_DIR, "tasks")

#: The number of approved tasks the harness must load.  A shorter set is a
#: hard error: a benchmark over fewer tasks than the approved set would
#: silently change the experiment.
_REQUIRED_TASK_COUNT = 20


@dataclass
class BenchmarkTask:
    """Canonical test case representing a software engineering problem."""
    task_id: str
    name: str
    category: str
    task_description: str
    context_code: str
    target_function_name: str
    test_suite_code: str
    tier1_initial_draft: Optional[str] = None
    #: The official test *without* the trailing ``check(...)`` call.
    #:
    #: :attr:`test_suite_code` is the complete script form (the official test
    #: plus the ``check(entry_point)`` call) and is what the legacy
    #: execution gate executes.  The official evaluator appends that call
    #: itself, so it needs the bare test source.  Keeping both prevents the
    #: check from ever being invoked twice.
    official_test_source: str = ""
    #: The task's entry point, i.e. the name the official test checks.
    entry_point: str = ""


class MockContaminationError(RuntimeError):
    """
    Raised when a run that was intended to be a live measurement produced mock
    results.

    A benchmark report whose pass rate is derived from the deterministic mock
    engine is not a measurement of anything. This error makes that state
    explicit instead of letting it be published as a 100% pass rate.
    """


@dataclass
class BenchmarkRunRecord:
    """
    One system-task-repetition outcome.

    Unknown measurements are recorded as None (never 0), so an unmeasured value
    can never be mistaken for a measured zero.
    """
    system: str
    task_id: str
    task_name: str
    category: str
    rep: int
    model: Optional[str]
    passed: bool
    syntax_valid: Optional[bool]
    timed_out: bool
    error_type: Optional[str]
    api_error: Optional[str]
    api_error_category: Optional[str]
    http_status: Optional[int]
    duration_ms: Optional[float]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    cost_usd: Optional[float]
    is_mock: bool
    escalated_to_tier2: bool
    tier1_surgical_repairs: int
    notes: Optional[str] = None
    #: The official evaluator's error message.  It is ``None`` when the
    #: official evaluator did not run or did not fail.
    error_message: Optional[str] = None
    #: The duration of the whole pipeline (model calls included), which is
    #: different from ``duration_ms`` - the official evaluation's own
    #: duration.
    pipeline_duration_ms: Optional[float] = None
    #: ``False`` when the run was planned but never executed (for example
    #: because a spend cap was reached).  Such a record describes a run that
    #: did not happen and must never be counted as a measurement.
    executed: bool = True
    #: The number of provider dispatches the pipeline made for this run.
    #: It is the real request budget of the run, not an estimate.
    provider_attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_csv_row(self) -> Dict[str, Any]:
        return self.to_dict()


def assert_live_measurement(report: MacroBenchmarkReport) -> None:
    """
    Guards against publishing a mock-derived report as a live measurement.

    Raises MockContaminationError if any recorded run was served by the mock
    engine, or if any live request failed.
    """
    if report.contains_mock_results:
        raise MockContaminationError(
            f"{len([r for r in report.run_records if r.is_mock])} of "
            f"{len(report.run_records)} recorded runs were served by the deterministic "
            f"mock engine. This report is NOT a live measurement and must not be "
            f"published as one. Set STRICT_LIVE=1 and a valid provider key."
        )
    if report.live_request_failures:
        raise MockContaminationError(
            f"{report.live_request_failures} live request(s) failed during this run. "
            f"Failures must be recorded as failures, not as passes."
        )


def export_run_records(
    records: List[BenchmarkRunRecord],
    output_dir: str = "results",
    basename: str = "benchmark_runs"
) -> Dict[str, str]:
    """
    Durably persists benchmark run records to JSON and CSV.

    Returns the mapping of written file paths. Raises OSError on failure so a
    silent loss of results is impossible.
    """
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"{basename}.json")
    csv_path = os.path.join(output_dir, f"{basename}.csv")

    payload = [r.to_dict() for r in records]
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    if records:
        fieldnames = list(records[0].to_dict().keys())
    else:
        fieldnames = list(BenchmarkRunRecord.__dataclass_fields__.keys())
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(r.to_csv_row())

    return {"json": json_path, "csv": csv_path}


class IncrementalRecordWriter:
    """
    Writes every run record to a JSONL file **as soon as it is produced**.

    The final JSON/CSV export happens at the end of the run, so a run that is
    interrupted (a crash, a killed process, a lost connection) would lose
    every record it had already produced. This writer appends and flushes
    each record immediately, so a partial run is always recoverable.

    The file is truncated when the writer is created, so a fresh run never
    mixes its records with a previous one's.
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8"):
            pass
        self._handle = open(self.path, "a", encoding="utf-8")
        self.count = 0

    def append(self, record: BenchmarkRunRecord) -> None:
        """Appends one record and flushes it to disk."""
        self._handle.write(json.dumps(record.to_dict(), default=str) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self.count += 1

    def close(self) -> None:
        """Closes the file."""
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> "IncrementalRecordWriter":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False


def _official_test_source_for(task: BenchmarkTask) -> str:
    """
    The official test source of a task, without the trailing ``check()`` call.

    The official evaluator appends that call itself, so it must not be
    present twice.
    """
    explicit = getattr(task, "official_test_source", "") or ""
    if explicit.strip():
        return explicit
    source = getattr(task, "test_suite_code", "") or ""
    entry = getattr(task, "entry_point", "") or getattr(task, "target_function_name", "")
    suffix = f"\ncheck({entry})\n"
    if entry and source.endswith(suffix):
        return source[: -len(suffix)]
    return source


def _evaluate_official(
    task: BenchmarkTask,
    cascade_result: Any,
    timeout: float,
) -> Any:
    """
    Runs the official Phase 0 evaluator on the module the pipeline produced.

    The official evaluator is the benchmark's authority: it executes the
    candidate in a scrubbed subprocess with the network blocked, under the
    same isolation the Phase 0 task set was validated with.  The legacy
    ``ExecutionHarness`` gate is kept for the syntax pre-check and for the
    pipeline's own diagnostics, but it no longer decides a pass.

    Returns ``None`` when there is no module the official evaluator could
    judge (no candidate, or a candidate that was rejected).
    """
    module = getattr(cascade_result, "verified_module", "") or ""
    if not module.strip():
        return None
    official_test_source = _official_test_source_for(task)
    if not official_test_source.strip():
        return None
    return run_official_test(
        module,
        official_test_source,
        task.entry_point or task.target_function_name,
        timeout=timeout,
    )


@dataclass
class MacroBenchmarkReport:
    """Aggregated empirical telemetry across the benchmark evaluation suite."""
    total_tasks: int
    passed_tasks: int
    pass_rate_pct: float
    tier1_first_pass_count: int
    tier1_self_repair_count: int
    tier2_escalation_count: int
    total_itms_cost_usd: float
    total_frontier_cost_usd: float
    total_naive_cost_usd: float
    cost_savings_vs_frontier_pct: float
    cost_savings_vs_naive_pct: float
    total_duration_ms: float
    task_results: List[Dict[str, Any]]
    run_records: List[BenchmarkRunRecord] = field(default_factory=list)
    contains_mock_results: bool = False
    live_request_failures: int = 0
    output_files: Dict[str, str] = field(default_factory=dict)
    #: The number of planned runs that were never executed (for example
    #: because a spend cap was reached).  They are not measurements.
    not_executed_runs: int = 0
    #: The number of recorded runs (task x repetition).  The pass rate is
    #: computed over the runs, not over the tasks: a run with 5 repetitions
    #: per task produces 5 x len(tasks) runs, and a pass rate per task would
    #: understate the denominator.
    total_runs: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def assert_free_models(model_ids: List[Optional[str]]) -> None:
    """
    Refuses a model set that is not on the free route.

    Every model id must end with ``":free"``.  The check is made *before* a
    single attempt is launched, so a paid model can never be reached by
    accident.

    Raises:
        ValueError: if any model id is not a free model.
    """
    offenders = [
        str(m) for m in model_ids
        if not str(m or "").strip().endswith(":free")
    ]
    if offenders:
        raise ValueError(
            "--require-free-model was requested but the following model id(s) "
            "are not on the free route (they do not end with ':free'): "
            + ", ".join(offenders)
            + ". No request was made."
        )


class _AttemptCountingDispatcher:
    """
    Wraps a dispatcher and counts the provider dispatches it performs.

    The count is the *measured* request budget of a run, which is what the
    free-route budget has to be judged against.
    """

    def __init__(self, dispatcher: Any):
        self._dispatcher = dispatcher
        self.attempts = 0

    def reset(self) -> None:
        self.attempts = 0

    def generate(self, *args, **kwargs):
        self.attempts += 1
        return self._dispatcher.generate(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._dispatcher, name)


class MacroBenchmarkSuite:
    """
    Standardized empirical benchmark harness evaluating ITMS speculative cascades
    and surgical self-repairs across diverse algorithmic and architectural domains.
    """

    @staticmethod
    def load_phase0_tasks() -> List[BenchmarkTask]:
        """
        Loads the 20 approved Phase 0 repair tasks.

        The loader reads only the **model-facing** payload and the per-task
        files it points at:

        *   ``benchmarks/data/model_facing_tasks.json`` - the approved task
            list (no reference solution, no mutation metadata),
        *   ``benchmarks/data/tasks/<task_id>/mutated_source.py`` - the
            defective module the model has to repair,
        *   ``benchmarks/data/tasks/<task_id>/official_test.py`` - the
            official test.

        The reference solutions (``gold_solutions.json``) and the undefective
        sources (``original_source.py``) are deliberately **not** read: a
        benchmark must never be able to see the answer.

        Raises:
            RuntimeError: if the payload is missing, unreadable, holds fewer
                than :data:`_REQUIRED_TASK_COUNT` tasks, or if a task's files
                are missing.
        """
        if not os.path.isfile(_MODEL_FACING_TASKS):
            raise RuntimeError(
                f"the approved task set is missing: {_MODEL_FACING_TASKS}. "
                f"The benchmark cannot run on an invented task set."
            )
        try:
            with open(_MODEL_FACING_TASKS, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"the approved task set could not be read ({type(exc).__name__}: "
                f"{exc}): {_MODEL_FACING_TASKS}"
            ) from exc

        entries = payload.get("tasks") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise RuntimeError(
                f"the approved task set has no 'tasks' list: {_MODEL_FACING_TASKS}"
            )
        if len(entries) < _REQUIRED_TASK_COUNT:
            raise RuntimeError(
                f"the approved task set holds {len(entries)} task(s), but "
                f"{_REQUIRED_TASK_COUNT} are required. A benchmark over a "
                f"short task set would silently change the experiment."
            )

        tasks: List[BenchmarkTask] = []
        for entry in entries:
            task_id = str(entry.get("task_id") or "").strip()
            if not task_id:
                raise RuntimeError(
                    f"the approved task set contains an entry without a task_id: "
                    f"{_MODEL_FACING_TASKS}"
                )
            task_dir = os.path.join(_TASKS_DIR, task_id)
            context_path = os.path.join(task_dir, "mutated_source.py")
            test_path = os.path.join(task_dir, "official_test.py")
            for path in (context_path, test_path):
                if not os.path.isfile(path):
                    raise RuntimeError(
                        f"task {task_id} is incomplete: {path} is missing."
                    )
            with open(context_path, "r", encoding="utf-8") as fh:
                context_code = fh.read()
            with open(test_path, "r", encoding="utf-8") as fh:
                official_test_source = fh.read()

            # The entry point of a Phase 0 task is its target function.
            entry_point = str(entry.get("target_function") or "").strip()
            if not entry_point:
                raise RuntimeError(
                    f"task {task_id} has no target function (entry point) in the "
                    f"approved payload."
                )

            description = str(entry.get("problem_prompt") or "").strip()
            if not description:
                description = str(entry.get("repair_instruction") or "").strip()
            if not description:
                raise RuntimeError(
                    f"task {task_id} has neither a problem prompt nor a repair "
                    f"instruction in the approved payload."
                )

            tasks.append(BenchmarkTask(
                task_id=task_id,
                name=task_id,
                category="humaneval_edit",
                task_description=description,
                context_code=context_code,
                target_function_name=entry_point,
                # The complete script form: the official test plus the call
                # the legacy execution gate needs.
                test_suite_code=(
                    f"{official_test_source}\ncheck({entry_point})\n"
                ),
                official_test_source=official_test_source,
                entry_point=entry_point,
            ))

        if len(tasks) < _REQUIRED_TASK_COUNT:
            raise RuntimeError(
                f"only {len(tasks)} task(s) could be loaded, but "
                f"{_REQUIRED_TASK_COUNT} are required."
            )
        return tasks

    @staticmethod
    def get_canonical_tasks() -> List[BenchmarkTask]:
        """Returns the canonical benchmark evaluation suite (20 tasks)."""
        return MacroBenchmarkSuite.load_phase0_tasks()

    @classmethod
    def run_suite(
        cls,
        orchestrator: Optional[RelayOrchestrator] = None,
        tier1_model: str = "Gemini 3.5 Flash",
        tier2_model: str = "Claude Sonnet 5.5 / Fable",
        pacing_delay_seconds: float = 0.0,
        force_mock: bool = False,
        strict_live: Optional[bool] = None,
        system: str = "ITMS",
        reps: int = 1,
        output_dir: Optional[str] = None,
        basename: Optional[str] = None,
        evaluation_timeout: float = 20.0,
        task_ids: Optional[List[str]] = None,
        max_spend_usd: Optional[float] = None,
        require_free_model: bool = False,
        max_output_tokens: int = 4096,
        max_tier1_retries: int = 0,
        enable_tier1_self_repair: bool = False,
        stop_on_quota: bool = False,
    ) -> MacroBenchmarkReport:
        """
        Executes all canonical benchmark tasks and aggregates multi-run telemetry.

        `strict_live` controls whether failed live requests raise instead of
        degrading into deterministic mock results. When it is None (the
        default) the run is strict if any selected tier model resolves to a
        provider with a configured API key, so a live measurement can never be
        silently reported as a passing mock run.

        `reps` repeats every task N times; each repetition is recorded
        separately. Live-mode failures (quota, auth, timeout, provider error)
        are recorded as FAILED runs and never converted into mock results.

        `require_free_model` refuses any model id that is not on the free
        route (it must end with ``":free"``) *before* a single attempt is
        made.

        `max_spend_usd` is a hard spend cap: once the cumulative reported
        cost of the run reaches the cap, no further attempt is launched and
        the remaining planned runs are recorded as not executed.

        `max_output_tokens` bounds the size of every provider response, and
        `max_tier1_retries` / `enable_tier1_self_repair` bound the number of
        provider calls a single run may make.

        `stop_on_quota` stops the whole run at the first quota-exceeded
        failure: every further attempt would fail the same way, so the
        remaining planned runs are recorded as not executed and no further
        request is made.
        """
        if require_free_model:
            assert_free_models([tier1_model, tier2_model])

        if strict_live is None:
            strict_live = (not force_mock) and ModelDispatcher.has_live_provider_key(
                [tier1_model, tier2_model]
            )
        if force_mock:
            strict_live = False

        runner = orchestrator or RelayOrchestrator(
            dispatcher=ModelDispatcher(force_mock=force_mock, strict_live=strict_live)
        )

        # The provider dispatches of every run are counted, so the request
        # budget of the benchmark is measured and not estimated.
        counting: Optional[_AttemptCountingDispatcher] = None
        inner_dispatcher = getattr(runner, "dispatcher", None)
        if inner_dispatcher is not None and hasattr(inner_dispatcher, "generate"):
            counting = _AttemptCountingDispatcher(inner_dispatcher)
            runner.dispatcher = counting

        tasks = cls.get_canonical_tasks()
        if task_ids:
            wanted = [str(t).strip() for t in task_ids if str(t).strip()]
            if not wanted:
                raise ValueError("task_ids was given but contains no task id")
            known = {t.task_id for t in tasks}
            unknown = [t for t in wanted if t not in known]
            if unknown:
                raise ValueError(
                    f"unknown task id(s): {', '.join(sorted(unknown))}. "
                    f"Known tasks: {', '.join(sorted(known))}"
                )
            tasks = [t for t in tasks if t.task_id in set(wanted)]

        passed_count = 0
        t1_first_pass = 0
        t1_repairs = 0
        t2_escalated = 0

        tot_itms_cost = 0.0
        tot_frontier_cost = 0.0
        tot_naive_cost = 0.0
        t_start = time.perf_counter()

        results_list: List[Dict[str, Any]] = []
        run_records: List[BenchmarkRunRecord] = []
        contains_mock = False
        live_failures = 0

        # Every record is persisted as soon as it exists, so an interrupted
        # run never loses the work it had already done.
        incremental: Optional[IncrementalRecordWriter] = None
        if output_dir:
            incremental = IncrementalRecordWriter(os.path.join(
                os.path.abspath(output_dir),
                f"{basename or f'{system}_runs'}.jsonl",
            ))

        # ---- the free-route guards -------------------------------------
        reps_planned = max(1, int(reps))
        total_planned = len(tasks) * reps_planned
        cumulative_spend = 0.0
        #: The reason the run was stopped early (``None`` = not stopped).
        #: It is also the note written on every not-executed record.
        stop_reason: Optional[str] = None
        executed_keys = set()
        progress_n = 0
        progress_pass = 0
        progress_fail = 0
        progress_mock = 0

        def _print_progress(task_id: str, rep: int, passed: bool,
                            error_type: Optional[str], is_mock: bool) -> None:
            """Prints one live progress line (no token, cost or credential)."""
            nonlocal progress_n, progress_pass, progress_fail, progress_mock
            progress_n += 1
            if passed:
                progress_pass += 1
            else:
                progress_fail += 1
            if is_mock:
                progress_mock += 1
            status = "PASS" if passed else "FAIL"
            reason = error_type or "ok"
            print(
                f"[{task_id}] rep {rep}/{reps_planned}  ->  {status}  "
                f"({reason})  [{progress_n}/{total_planned}]",
                flush=True,
            )
            if progress_n % 10 == 0:
                elapsed = int(time.perf_counter() - t_start)
                print(
                    f"--- progress: {progress_n}/{total_planned}, "
                    f"{progress_pass} pass, {progress_fail} fail, "
                    f"{progress_mock} mock, elapsed {elapsed}s ---",
                    flush=True,
                )

        def _account_spend(record: BenchmarkRunRecord) -> bool:
            """Adds the reported cost to the total and reports a cap breach."""
            nonlocal cumulative_spend, stop_reason
            cumulative_spend += (
                record.cost_usd if record.cost_usd is not None else 0.0
            )
            if max_spend_usd is not None and cumulative_spend >= max_spend_usd:
                stop_reason = "spend cap reached"
                return True
            return False

        for idx, task in enumerate(tasks):
            for rep in range(1, reps_planned + 1):
                if stop_reason:
                    break
                if idx > 0 and pacing_delay_seconds > 0:
                    time.sleep(pacing_delay_seconds)

                if counting is not None:
                    counting.reset()
                try:
                    res = runner.run_cascade(
                        task_description=task.task_description,
                        raw_context_code=task.context_code,
                        target_function_name=task.target_function_name,
                        test_suite_code=task.test_suite_code,
                        tier1_model_name=tier1_model,
                        tier2_model_name=tier2_model,
                        enable_tier1_self_repair=enable_tier1_self_repair,
                        max_tier1_retries=max_tier1_retries,
                        max_output_tokens=max_output_tokens,
                    )
                except (LiveModeError, ModelConfigurationError) as e:
                    # A live-mode failure is a FAILED run, never a mock result.
                    live_failures += 1
                    category = getattr(e, "category", "live_mode_error")
                    status_code = getattr(e, "status_code", None)
                    run_records.append(BenchmarkRunRecord(
                        system=system,
                        task_id=task.task_id,
                        task_name=task.name,
                        category=task.category,
                        rep=rep,
                        model=tier1_model,
                        passed=False,
                        syntax_valid=None,
                        timed_out=(category == "timeout"),
                        error_type=type(e).__name__,
                        api_error=str(e),
                        api_error_category=category,
                        http_status=status_code,
                        duration_ms=None,
                        input_tokens=None,
                        output_tokens=None,
                        total_tokens=None,
                        cost_usd=None,
                        is_mock=False,
                        escalated_to_tier2=False,
                        tier1_surgical_repairs=0,
                        notes="live request failed; no inference was performed",
                        provider_attempts=(
                            counting.attempts if counting is not None else 0
                        ),
                    ))
                    executed_keys.add((task.task_id, rep))
                    if incremental is not None:
                        incremental.append(run_records[-1])
                    _print_progress(
                        task.task_id, rep, False, type(e).__name__, False
                    )
                    results_list.append({
                        "task_id": task.task_id,
                        "name": task.name,
                        "category": task.category,
                        "rep": rep,
                        "success": False,
                        "escalated": False,
                        "tier1_repairs": 0,
                        "itms_cost": None,
                        "savings_vs_frontier_pct": None,
                        "api_error": str(e),
                        "api_error_category": category,
                        "is_mock": False,
                    })
                    if stop_on_quota and category == "quota_exceeded":
                        # The free daily quota is gone: every further attempt
                        # would fail the same way, so the run stops here.
                        stop_reason = "quota exhausted"
                        break
                    if _account_spend(run_records[-1]):
                        break
                    continue

                resp = res.tier1_response
                is_mock = bool(getattr(resp, "is_mock", False)) if resp is not None else False
                if is_mock:
                    contains_mock = True

                # Token/cost telemetry is only reported when it was actually
                # measured by a live response; otherwise it stays None.
                if is_mock or resp is None:
                    total_tokens = None
                    input_tokens = None
                    output_tokens = None
                    cost_usd = None
                else:
                    total_tokens = res.total_tokens_consumed
                    input_tokens = getattr(resp, "raw_tokens_in", None)
                    output_tokens = getattr(resp, "output_tokens", None)
                    cost_usd = res.total_pipeline_cost_usd

                sandbox_res = res.tier2_result or res.tier1_result
                # The legacy execution gate is kept for the syntax pre-check
                # and for the pipeline's own diagnostics.  The benchmark's
                # verdict comes from the official Phase 0 evaluator, which
                # is the stronger of the two: it blocks sockets and runs
                # the candidate under a scrubbed, resource-limited
                # subprocess.
                syntax_valid = getattr(sandbox_res, "syntax_valid", None) if sandbox_res else None

                official = _evaluate_official(task, res, evaluation_timeout)
                if official is None:
                    passed = False
                    error_type = "NoEvaluableCandidate"
                    error_message = (
                        "the pipeline produced no module the official evaluator "
                        "could judge"
                    )
                    timed_out = False
                    evaluation_duration_ms = None
                else:
                    passed = bool(official.passed)
                    error_type = official.error_type
                    error_message = official.error_message
                    timed_out = bool(official.timed_out)
                    evaluation_duration_ms = official.duration_ms

                run_records.append(BenchmarkRunRecord(
                    system=system,
                    task_id=task.task_id,
                    task_name=task.name,
                    category=task.category,
                    rep=rep,
                    model=tier1_model,
                    passed=passed,
                    syntax_valid=syntax_valid,
                    timed_out=timed_out,
                    error_type=error_type,
                    api_error=None,
                    api_error_category=None,
                    http_status=None,
                    duration_ms=evaluation_duration_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    is_mock=is_mock,
                    escalated_to_tier2=bool(res.escalated_to_tier2),
                    tier1_surgical_repairs=res.tier1_surgical_repairs,
                    notes=("mock result - not a live measurement" if is_mock else None),
                    error_message=error_message,
                    pipeline_duration_ms=res.total_duration_ms,
                    provider_attempts=(
                        counting.attempts if counting is not None else 0
                    ),
                ))
                executed_keys.add((task.task_id, rep))
                if incremental is not None:
                    incremental.append(run_records[-1])
                _print_progress(task.task_id, rep, passed, error_type, is_mock)

                if passed:
                    passed_count += 1

                if not res.escalated_to_tier2:
                    if res.tier1_surgical_repairs > 0:
                        t1_repairs += 1
                    else:
                        t1_first_pass += 1
                else:
                    t2_escalated += 1

                tot_itms_cost += res.total_pipeline_cost_usd
                tot_frontier_cost += res.direct_frontier_cost_usd
                tot_naive_cost += res.naive_agentic_cost_usd

                results_list.append({
                    "task_id": task.task_id,
                    "name": task.name,
                    "category": task.category,
                    "rep": rep,
                    "success": res.final_success,
                    "escalated": res.escalated_to_tier2,
                    "tier1_repairs": res.tier1_surgical_repairs,
                    "itms_cost": res.total_pipeline_cost_usd,
                    "savings_vs_frontier_pct": res.cost_saved_vs_frontier_pct,
                    "api_error": None,
                    "api_error_category": None,
                    "is_mock": is_mock,
                })

                if _account_spend(run_records[-1]):
                    break

        # ---- the runs that were planned but never executed --------------
        if stop_reason:
            for task in tasks:
                for rep in range(1, reps_planned + 1):
                    if (task.task_id, rep) in executed_keys:
                        continue
                    run_records.append(BenchmarkRunRecord(
                        system=system,
                        task_id=task.task_id,
                        task_name=task.name,
                        category=task.category,
                        rep=rep,
                        model=tier1_model,
                        passed=False,
                        syntax_valid=None,
                        timed_out=False,
                        error_type="NotExecuted",
                        api_error=None,
                        api_error_category=None,
                        http_status=None,
                        duration_ms=None,
                        input_tokens=None,
                        output_tokens=None,
                        total_tokens=None,
                        cost_usd=None,
                        is_mock=False,
                        escalated_to_tier2=False,
                        tier1_surgical_repairs=0,
                        notes=stop_reason,
                        executed=False,
                        provider_attempts=0,
                    ))
                    if incremental is not None:
                        incremental.append(run_records[-1])
            print(
                f"--- {stop_reason}: {len(executed_keys)} run(s) executed, "
                f"{len(run_records) - len(executed_keys)} run(s) not executed ---",
                flush=True,
            )

        duration_ms = (time.perf_counter() - t_start) * 1000.0
        savings_vs_frontier = ((1.0 - (tot_itms_cost / tot_frontier_cost)) * 100.0) if tot_frontier_cost > 0 else 0.0
        savings_vs_naive = ((1.0 - (tot_itms_cost / tot_naive_cost)) * 100.0) if tot_naive_cost > 0 else 0.0

        output_files: Dict[str, str] = {}
        if incremental is not None:
            incremental.close()
        if output_dir:
            output_files = export_run_records(
                run_records,
                output_dir=output_dir,
                basename=basename or f"{system}_runs",
            )
            output_files["jsonl"] = incremental.path if incremental is not None else ""

        total_runs = len(run_records)
        executed_runs = sum(1 for r in run_records if r.executed)
        return MacroBenchmarkReport(
            total_tasks=len(tasks),
            passed_tasks=passed_count,
            pass_rate_pct=((passed_count / executed_runs) * 100.0) if executed_runs else 0.0,
            tier1_first_pass_count=t1_first_pass,
            tier1_self_repair_count=t1_repairs,
            tier2_escalation_count=t2_escalated,
            total_itms_cost_usd=tot_itms_cost,
            total_frontier_cost_usd=tot_frontier_cost,
            total_naive_cost_usd=tot_naive_cost,
            cost_savings_vs_frontier_pct=savings_vs_frontier,
            cost_savings_vs_naive_pct=savings_vs_naive,
            total_duration_ms=duration_ms,
            task_results=results_list,
            run_records=run_records,
            contains_mock_results=contains_mock,
            live_request_failures=live_failures,
            output_files=output_files,
            total_runs=total_runs,
            not_executed_runs=total_runs - executed_runs,
        )


def _bool_arg(value: str) -> bool:
    """Parses a command line boolean (``true``/``false``/``1``/``0``/...)."""
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    raise argparse.ArgumentTypeError(
        f"expected a boolean value (true/false), got '{value}'"
    )


def _print_dry_run(
    tasks: List[BenchmarkTask],
    reps: int,
    tier1_model: str,
    tier2_model: str,
    system: str,
    require_free_model: bool = False,
    max_output_tokens: int = 4096,
    max_tier1_retries: int = 0,
    enable_tier1_self_repair: bool = False,
) -> int:
    """
    Prints the planned schedule. No provider is contacted and nothing is
    written.
    """
    planned = len(tasks) * max(1, int(reps))
    print("ITMS macro benchmark - DRY RUN (no provider is contacted)")
    print("=" * 72)
    print(f"system              : {system}")
    print(f"tier 1 model        : {tier1_model}")
    print(f"tier 2 model        : {tier2_model}")
    print(f"tasks               : {len(tasks)}")
    print(f"repetitions         : {reps}")
    print(f"planned runs        : {planned}")
    print(f"require free model  : {require_free_model}")
    print(f"max output tokens   : {max_output_tokens}")
    print(f"tier1 self repair   : {enable_tier1_self_repair}")
    print(f"max tier1 retries   : {max_tier1_retries}")
    print("-" * 72)
    print("provider request budget:")
    if enable_tier1_self_repair:
        print("  1 request per run, plus up to 3 more in the worst case")
        print("    (2 tier-1 self-repair retries + 1 tier-2 escalation)")
        print(f"  => between {planned} and {planned * 4} provider requests")
    else:
        print("  1 request per run (tier-1 self-repair is disabled)")
        print(f"  => at most {planned * 2} provider requests "
              "(1 tier-1 attempt + at most 1 tier-2 escalation)")
    print("-" * 72)
    print(f"{'task':14s} {'entry point':28s} {'context':>8s} {'official test':>14s}")
    for task in tasks:
        print(
            f"{task.task_id:14s} {task.target_function_name:28s} "
            f"{len(task.context_code.splitlines()):8d} "
            f"{len(task.official_test_source.splitlines()):14d}"
        )
    print("-" * 72)
    print("schedule (task x repetition):")
    for rep in range(1, max(1, int(reps)) + 1):
        for task in tasks:
            print(f"  rep {rep}  {task.task_id}  {task.target_function_name}")
    print("=" * 72)
    print(f"dry run: {planned} runs planned. No provider was contacted.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """
    Command line entry point of the macro benchmark.

    Exit codes:
        0  the run finished and (in live mode) is a valid live measurement,
        2  the run finished but is **not** a valid live measurement
           (mock contamination or failed live requests),
        1  the run could not be started (bad configuration, unknown task).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Run the ITMS macro benchmark. --live runs against the configured "
            "provider and refuses to publish a mock result as a measurement; "
            "--dry-run prints the schedule and contacts nobody."
        )
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="run in strict live mode (a failed request is a failed run, never a mock result)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned schedule and exit without contacting any provider",
    )
    parser.add_argument("--reps", type=int, default=1, help="repetitions per task")
    parser.add_argument(
        "--tasks",
        default=None,
        help="comma-separated task ids (default: all 20 approved tasks)",
    )
    parser.add_argument("--output-dir", default=None, help="directory for the run records")
    parser.add_argument("--basename", default=None, help="basename of the record files")
    parser.add_argument("--system", default="ITMS", help="the system label of the records")
    parser.add_argument(
        "--tier1-model", default="Gemini 3.5 Flash", help="the tier 1 model"
    )
    parser.add_argument(
        "--tier2-model", default="Claude Sonnet 5.5 / Fable", help="the tier 2 model"
    )
    parser.add_argument(
        "--pacing-delay", type=float, default=0.0, help="seconds between tasks"
    )
    parser.add_argument(
        "--eval-isolation",
        choices=("auto", "seatbelt", "off"),
        default="auto",
        help=(
            "isolation of the official evaluator: 'auto' uses the macOS "
            "Seatbelt profile when the platform supports it, 'seatbelt' "
            "requires it, 'off' uses the scrubbed-environment subprocess only"
        ),
    )
    parser.add_argument(
        "--max-spend-usd",
        type=float,
        default=None,
        help=(
            "hard spend cap in USD: the run stops as soon as the cumulative "
            "reported cost reaches this value"
        ),
    )
    parser.add_argument(
        "--require-free-model",
        action="store_true",
        help=(
            "refuse to start unless every selected tier model is on the free "
            "route (its id ends with ':free')"
        ),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="the maximum number of tokens a single provider response may use",
    )
    parser.add_argument(
        "--max-tier1-retries",
        type=int,
        default=0,
        help="the maximum number of tier-1 self-repair retries per run",
    )
    parser.add_argument(
        "--enable-tier1-self-repair",
        type=_bool_arg,
        nargs="?",
        const=True,
        default=False,
        metavar="BOOL",
        help="enable the tier-1 self-repair loop (true/false)",
    )
    parser.add_argument(
        "--stop-on-quota",
        action="store_true",
        help=(
            "stop the whole run at the first quota-exceeded failure; the "
            "remaining planned runs are recorded as not executed"
        ),
    )
    args = parser.parse_args(argv)

    if args.dry_run and args.live:
        print("error: --dry-run and --live are mutually exclusive.", file=sys.stderr)
        return 1

    if args.require_free_model:
        try:
            assert_free_models([args.tier1_model, args.tier2_model])
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    try:
        tasks = MacroBenchmarkSuite.get_canonical_tasks()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.tasks:
        wanted = [t.strip() for t in args.tasks.split(",") if t.strip()]
        known = {t.task_id for t in tasks}
        unknown = [t for t in wanted if t not in known]
        if unknown:
            print(
                f"error: unknown task id(s): {', '.join(unknown)}. "
                f"Known tasks: {', '.join(sorted(known))}",
                file=sys.stderr,
            )
            return 1
        tasks = [t for t in tasks if t.task_id in set(wanted)]

    if args.dry_run:
        return _print_dry_run(
            tasks, args.reps, args.tier1_model, args.tier2_model, args.system,
            require_free_model=args.require_free_model,
            max_output_tokens=args.max_output_tokens,
            max_tier1_retries=args.max_tier1_retries,
            enable_tier1_self_repair=args.enable_tier1_self_repair,
        )

    # -- the official evaluator's isolation level --------------------------
    if args.eval_isolation != "off":
        try:
            from benchmarks.phase0.sandbox_profile import sandbox_available

            if args.eval_isolation == "seatbelt" and not sandbox_available():
                print(
                    "error: --eval-isolation seatbelt was requested but this "
                    "platform has no Seatbelt sandbox.",
                    file=sys.stderr,
                )
                return 1
            if sandbox_available():
                os.environ["ITMS_EVAL_ISOLATION"] = "seatbelt"
        except Exception as exc:  # pragma: no cover - defensive
            print(
                f"warning: could not set the evaluation isolation: {exc}",
                file=sys.stderr,
            )

    try:
        report = MacroBenchmarkSuite.run_suite(
            tier1_model=args.tier1_model,
            tier2_model=args.tier2_model,
            pacing_delay_seconds=args.pacing_delay,
            force_mock=not args.live,
            strict_live=True if args.live else None,
            system=args.system,
            reps=args.reps,
            output_dir=args.output_dir,
            basename=args.basename,
            task_ids=[t.task_id for t in tasks],
            max_spend_usd=args.max_spend_usd,
            require_free_model=args.require_free_model,
            max_output_tokens=args.max_output_tokens,
            max_tier1_retries=args.max_tier1_retries,
            enable_tier1_self_repair=args.enable_tier1_self_repair,
            stop_on_quota=args.stop_on_quota,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("=" * 72)
    print(f"system                : {args.system}")
    print(f"tasks                 : {report.total_tasks}")
    print(f"runs                  : {report.total_runs}")
    print(f"pass_rate_pct         : {report.pass_rate_pct:.2f}")
    print(f"tier1_first_pass_count: {report.tier1_first_pass_count}")
    print(f"tier1_self_repair_count: {report.tier1_self_repair_count}")
    print(f"tier2_escalation_count: {report.tier2_escalation_count}")
    print(f"live_request_failures : {report.live_request_failures}")
    print(f"not_executed_runs     : {report.not_executed_runs}")
    print(f"contains_mock_results : {report.contains_mock_results}")
    print(f"mock records          : {sum(1 for r in report.run_records if r.is_mock)}")
    for path in report.output_files.values():
        if path:
            print(f"written               : {path}")
    print("=" * 72)

    if args.live:
        try:
            assert_live_measurement(report)
        except MockContaminationError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            print(
                "This run is NOT a live measurement and must not be published "
                "as one.",
                file=sys.stderr,
            )
            return 2
        print("OK: the run is a valid live measurement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
