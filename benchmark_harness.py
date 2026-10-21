import time
import csv
import json
import os
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional
from relay_orchestrator import RelayOrchestrator, CascadeRunResult
from sandbox import ExecutionHarness
from model_dispatcher import ModelDispatcher, LiveModeError, ModelConfigurationError

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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MacroBenchmarkSuite:
    """
    Standardized empirical benchmark harness evaluating ITMS speculative cascades
    and surgical self-repairs across diverse algorithmic and architectural domains.
    """
    @staticmethod
    def get_canonical_tasks() -> List[BenchmarkTask]:
        """Returns the canonical benchmark evaluation suite."""
        return [
            BenchmarkTask(
                task_id="BM-01-FIN-RECON",
                name="Account Ledger Reconciliation",
                category="Financial Domain & Rounding",
                task_description="Implement reconcile_balance(target_date) to sum valid transactions, deduct 5% tax allocation per record, and return rounded float.",
                context_code="""class TransactionRecord:
    def __init__(self, tx_id: str, amount: float):
        self.tx_id = tx_id
        self.amount = float(amount)
    def validate_checksum(self) -> bool:
        return True
    def calculate_tax_allocation(self, rate: float) -> float:
        return self.amount * rate

class AccountLedger:
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.transactions: list[TransactionRecord] = []
    def append_transaction(self, tx: TransactionRecord) -> None:
        self.transactions.append(tx)
    def reconcile_balance(self, target_date: str) -> float:
        pass
""",
                target_function_name="reconcile_balance",
                test_suite_code="""ledger = AccountLedger('acc_001')
tx1 = TransactionRecord('t1', 1000.0)
tx2 = TransactionRecord('t2', 500.0)
ledger.append_transaction(tx1)
ledger.append_transaction(tx2)
assert ledger.reconcile_balance('2026-10-01') == 1425.0
"""
            ),
            BenchmarkTask(
                task_id="BM-02-SEC-HASH",
                name="Data Pipeline & SHA-256 Validator",
                category="Cryptographic Validation",
                task_description="Implement dispatch_batch(records) to filter corrupted items, calculate sha256 checksums on clean payloads, and return list of valid hex digests.",
                context_code="""import hashlib

class IngestionBatch:
    def __init__(self, batch_id: str):
        self.batch_id = batch_id
    def dispatch_batch(self, raw_records: list[dict]) -> list[str]:
        pass
""",
                target_function_name="dispatch_batch",
                test_suite_code="""batch = IngestionBatch('b_1')
records = [
    {'id': 'r1', 'payload': 'valid_data', 'corrupt': False},
    {'id': 'r2', 'payload': 'corrupt_data', 'corrupt': True},
    {'id': 'r3', 'payload': 'more_valid', 'corrupt': False}
]
digests = batch.dispatch_batch(records)
assert len(digests) == 2
import hashlib
assert digests[0] == hashlib.sha256(b'valid_data').hexdigest()
assert digests[1] == hashlib.sha256(b'more_valid').hexdigest()
"""
            ),
            BenchmarkTask(
                task_id="BM-03-DS-LRU",
                name="LRU Eviction Policy",
                category="Data Structures & Algorithms",
                task_description="Implement put(key, value) in LRUCache adhering to strict capacity bounds and O(1) eviction.",
                context_code="""class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = {}
        self.order = []
    def get(self, key: str) -> int:
        if key not in self.cache:
            return -1
        self.order.remove(key)
        self.order.append(key)
        return self.cache[key]
    def put(self, key: str, value: int) -> None:
        pass
""",
                target_function_name="put",
                test_suite_code="""lru = LRUCache(2)
lru.put('a', 1)
lru.put('b', 2)
assert lru.get('a') == 1
lru.put('c', 3)
assert lru.get('b') == -1
assert lru.get('a') == 1
assert lru.get('c') == 3
"""
            ),
            BenchmarkTask(
                task_id="BM-04-NET-RATELIMIT",
                name="Token Bucket Rate Limiter",
                category="Distributed Systems / Concurrency",
                task_description="Implement acquire(tokens) in TokenBucketLimiter replenishing tokens at replenishment_rate per second and guarding capacity.",
                context_code="""import time

class TokenBucketLimiter:
    def __init__(self, capacity: float, replenishment_rate: float):
        self.capacity = float(capacity)
        self.rate = float(replenishment_rate)
        self.tokens = float(capacity)
        self.last_check = time.time()
    def acquire(self, tokens: float) -> bool:
        pass
""",
                target_function_name="acquire",
                test_suite_code="""limiter = TokenBucketLimiter(capacity=10.0, replenishment_rate=1.0)
assert limiter.acquire(5.0) is True
assert limiter.acquire(5.0) is True
assert limiter.acquire(1.0) is False
"""
            ),
            BenchmarkTask(
                task_id="BM-05-SYNTAX-REPAIR",
                name="Intra-Tier Syntax Self-Repair Demo",
                category="Self-Healing Code Synthesis",
                task_description="Implement calculate_net_yield(gross_margin, opex) returning float(gross_margin - opex).",
                context_code="""class YieldCalculator:
    def calculate_net_yield(self, gross_margin: float, opex: float) -> float:
        pass
""",
                target_function_name="calculate_net_yield",
                test_suite_code="""calc = YieldCalculator()
assert calc.calculate_net_yield(100.0, 35.0) == 65.0
assert calc.calculate_net_yield(50.0, 10.0) == 40.0
"""
            )
        ]

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
        basename: Optional[str] = None
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
        """
        if strict_live is None:
            strict_live = (not force_mock) and ModelDispatcher.has_live_provider_key(
                [tier1_model, tier2_model]
            )
        if force_mock:
            strict_live = False

        runner = orchestrator or RelayOrchestrator(
            dispatcher=ModelDispatcher(force_mock=force_mock, strict_live=strict_live)
        )
        tasks = cls.get_canonical_tasks()

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

        for idx, task in enumerate(tasks):
            for rep in range(1, max(1, int(reps)) + 1):
                if idx > 0 and pacing_delay_seconds > 0:
                    time.sleep(pacing_delay_seconds)

                try:
                    res = runner.run_cascade(
                        task_description=task.task_description,
                        raw_context_code=task.context_code,
                        target_function_name=task.target_function_name,
                        test_suite_code=task.test_suite_code,
                        tier1_model_name=tier1_model,
                        tier2_model_name=tier2_model,
                        enable_tier1_self_repair=True,
                        max_tier1_retries=2
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
                    ))
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
                timed_out = bool(getattr(sandbox_res, "timed_out", False)) if sandbox_res else False
                syntax_valid = getattr(sandbox_res, "syntax_valid", None) if sandbox_res else None
                error_type = getattr(sandbox_res, "error_type", None) if sandbox_res else None

                run_records.append(BenchmarkRunRecord(
                    system=system,
                    task_id=task.task_id,
                    task_name=task.name,
                    category=task.category,
                    rep=rep,
                    model=tier1_model,
                    passed=bool(res.final_success),
                    syntax_valid=syntax_valid,
                    timed_out=timed_out,
                    error_type=error_type,
                    api_error=None,
                    api_error_category=None,
                    http_status=None,
                    duration_ms=res.total_duration_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    is_mock=is_mock,
                    escalated_to_tier2=bool(res.escalated_to_tier2),
                    tier1_surgical_repairs=res.tier1_surgical_repairs,
                    notes=("mock result - not a live measurement" if is_mock else None),
                ))

                if res.final_success:
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

        duration_ms = (time.perf_counter() - t_start) * 1000.0
        savings_vs_frontier = ((1.0 - (tot_itms_cost / tot_frontier_cost)) * 100.0) if tot_frontier_cost > 0 else 0.0
        savings_vs_naive = ((1.0 - (tot_itms_cost / tot_naive_cost)) * 100.0) if tot_naive_cost > 0 else 0.0

        output_files: Dict[str, str] = {}
        if output_dir:
            output_files = export_run_records(
                run_records,
                output_dir=output_dir,
                basename=basename or f"{system}_runs",
            )

        return MacroBenchmarkReport(
            total_tasks=len(tasks),
            passed_tasks=passed_count,
            pass_rate_pct=(passed_count / len(tasks)) * 100.0,
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
        )
