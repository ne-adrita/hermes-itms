import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Tuple, List
from prompt_manager import get_token_count, GLOBAL_STATIC_PREFIX
from telemetry import TIER_1_BUDGET_PROFILES, TIER_2_FRONTIER_PROFILES, PricingProfile, resolve_tier_pricing
from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer, TextDiffPatcher, SurgicalTarget
from skeletonizer import skeletonize_code
from sandbox import ExecutionHarness, ExecutionResult

# ---------------------------------------------------------------------------
# Honest measurement vocabulary.
#
# TOKEN_SOURCE_PROVIDER   the numbers came from the provider's usage payload
# TOKEN_SOURCE_ESTIMATE    the numbers came from the local tiktoken estimator
# TOKEN_SOURCE_UNAVAILABLE no token information exists for this run
#
# LATENCY_SOURCE_LOCAL    the elapsed time of *local* processing only; there was
#                          no provider request, so this is not a model latency
# ---------------------------------------------------------------------------
TOKEN_SOURCE_PROVIDER = "provider_reported"
TOKEN_SOURCE_ESTIMATE = "local_estimate"
TOKEN_SOURCE_UNAVAILABLE = "unavailable"

COST_SOURCE_ESTIMATED = "estimated_from_local_token_estimate"
COST_SOURCE_PROVIDER = "provider_reported"
COST_SOURCE_UNAVAILABLE = "unavailable"

LATENCY_SOURCE_LOCAL = (
    "measured_local_processing_only (edit application + evaluation; "
    "no provider request in this mode)"
)

# Per-strategy outcome vocabulary
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_MISSING_CANDIDATE = "missing_candidate"
STATUS_UNEVALUATED = "unevaluated"
STATUS_INVALID_EDIT = "invalid_edit"


@dataclass
class EditStrategyResult:
    """Telemetry report for a single code editing strategy.

    Every metric carries an explicit *source* so that a simulated value can
    never be presented as a measured one.
    """
    strategy_name: str
    success: Optional[bool]          # None when the attempt was never evaluated
    status: str                       # passed | failed | timeout | missing_candidate | unevaluated | invalid_edit
    splicing_error: Optional[str]
    input_tokens: Optional[int]
    cached_input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    token_source: str
    cost_usd: Optional[float]
    cost_source: str
    latency_ms: float
    latency_source: str
    resulting_code: str
    sandbox_result: Optional[ExecutionResult] = None

    # Repetition / attempt bookkeeping (identical policy for all strategies)
    attempts: int = 0
    passed_attempts: int = 0
    failed_attempts: int = 0
    timed_out_attempts: int = 0
    unevaluated_attempts: int = 0
    missing_candidate_attempts: int = 0
    invalid_edit_attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TriWayEditBenchmarkReport:
    """Comparative benchmark of the three code editing strategies.

    IMPORTANT: `run_benchmark` in this module is a *simulation*. The candidate
    code for every strategy is supplied by the caller, so no provider request
    is made. Token counts are local tiktoken estimates, costs are derived from
    those estimates and the catalogue price, and the latency is the measured
    local processing time of the edit mechanism. They are not provider
    measurements and must not be reported as such.
    """
    task_description: str
    target_node_name: str
    full_file_result: EditStrategyResult
    search_replace_result: EditStrategyResult
    itms_surgical_result: EditStrategyResult

    # Comparative Impact Deltas (all derived from the values above, and
    # therefore only as trustworthy as those values)
    output_tokens_saved_vs_full_pct: Optional[float]
    cost_saved_vs_full_pct: Optional[float]
    cost_saved_vs_diff_pct: Optional[float]
    latency_speedup_factor: Optional[float]   # None: not a valid claim in simulation mode
    patch_reliability_itms: bool
    patch_reliability_diff: bool

    # Provenance of the numbers in this report
    measurement_mode: str = "simulated_local_processing"
    is_measurement: bool = False
    token_source: str = TOKEN_SOURCE_ESTIMATE
    latency_source: str = LATENCY_SOURCE_LOCAL
    repetitions: int = 1
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SurgicalEditOrchestrator:
    """
    Coordinates and benchmarks code modification pipelines across:
    1. Full-File Regeneration (Baseline)
    2. Text-Based Search/Replace (Aider style)
    3. ITMS AST-Guided Surgical Splicer (Our Approach)

    The benchmark is fair by construction: all three strategies receive the
    same task, are graded by the same evaluator (the deterministic execution
    harness), run the same number of repetitions, and have their metrics
    measured with the same definitions.
    """
    def __init__(self, harness: Optional[ExecutionHarness] = None):
        self.harness = harness or ExecutionHarness()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_exec(exec_result: Optional[ExecutionResult]) -> str:
        if exec_result is None:
            return STATUS_UNEVALUATED
        if exec_result.timed_out:
            return STATUS_TIMEOUT
        if exec_result.success:
            return STATUS_PASSED
        return STATUS_FAILED

    def _evaluate(
        self,
        candidate_code: str,
        test_suite_code: str,
        target_name: str
    ) -> Tuple[Optional[ExecutionResult], str]:
        """
        Runs the shared evaluator on a candidate.  Returns (result, status).
        A candidate is only ever marked as passed when the evaluator actually
        ran it and it exited cleanly.
        """
        if not test_suite_code.strip():
            return None, STATUS_UNEVALUATED
        if not candidate_code.strip():
            return None, STATUS_MISSING_CANDIDATE
        result = self.harness.execute_test(
            candidate_code=candidate_code,
            test_suite_code=test_suite_code,
            target_function_name=target_name
        )
        return result, self._classify_exec(result)

    @staticmethod
    def _estimate_cost(
        input_tokens: int,
        output_tokens: int,
        pricing: PricingProfile,
        cached_input_tokens: int = 0
    ) -> float:
        cached_input_tokens = min(cached_input_tokens, input_tokens)
        uncached = input_tokens - cached_input_tokens
        return (
            (uncached * pricing.cost_per_uncached_token)
            + (cached_input_tokens * pricing.cost_per_cached_token)
            + (output_tokens * pricing.cost_per_output_token)
        )

    # ------------------------------------------------------------------
    # main entry point
    # ------------------------------------------------------------------
    def run_benchmark(
        self,
        original_source: str,
        target_name: str,
        edit_instruction: str,
        replacement_node_code: str,
        simulated_full_file_code: str,
        simulated_search_block: str,
        simulated_replace_block: str,
        test_suite_code: str = "",
        tier1_model_name: str = "Gemini 3.5 Flash",
        tier2_model_name: str = "Claude Sonnet 5.5 / Fable",
        simulate_diff_whitespace_error: bool = False,
        repetitions: int = 1
    ) -> TriWayEditBenchmarkReport:
        """
        Executes the three-way comparative benchmark on the given edit task.

        No provider request is made: the candidate code for each strategy is
        supplied by the caller.  Every reported number therefore carries an
        explicit source label, and a strategy that could not be evaluated is
        reported as `unevaluated` rather than as a success.
        """
        repetitions = max(1, int(repetitions))
        t1_pricing = resolve_tier_pricing(tier1_model_name, TIER_1_BUDGET_PROFILES)
        t2_pricing = resolve_tier_pricing(tier2_model_name, TIER_2_FRONTIER_PROFILES)

        # ------------------------------------------------------------------
        # Strategy 1: Full-File Regeneration Baseline
        # ------------------------------------------------------------------
        full_prompt = f"{GLOBAL_STATIC_PREFIX}\n\nTask: {edit_instruction}\n\nFile:\n{original_source}"
        full_in_tokens = get_token_count(full_prompt)
        full_out_tokens = get_token_count(simulated_full_file_code or "")

        full_latencies: List[float] = []
        full_statuses: List[str] = []
        full_exec: Optional[ExecutionResult] = None
        for _ in range(repetitions):
            t0 = time.perf_counter()
            full_exec, status = self._evaluate(
                simulated_full_file_code, test_suite_code, target_name
            )
            full_latencies.append((time.perf_counter() - t0) * 1000.0)
            full_statuses.append(status)
        full_latency = sum(full_latencies) / len(full_latencies)
        full_cost = self._estimate_cost(full_in_tokens, full_out_tokens, t2_pricing)

        res_full = EditStrategyResult(
            strategy_name="Full-File Regeneration (Baseline)",
            success=(full_statuses.count(STATUS_PASSED) == repetitions),
            status=self._aggregate_status(full_statuses),
            splicing_error=None,
            input_tokens=full_in_tokens,
            cached_input_tokens=None,
            output_tokens=full_out_tokens,
            total_tokens=full_in_tokens + full_out_tokens,
            token_source=TOKEN_SOURCE_ESTIMATE,
            cost_usd=full_cost,
            cost_source=COST_SOURCE_ESTIMATED,
            latency_ms=full_latency,
            latency_source=LATENCY_SOURCE_LOCAL,
            resulting_code=simulated_full_file_code or "",
            sandbox_result=full_exec,
            attempts=repetitions,
            passed_attempts=full_statuses.count(STATUS_PASSED),
            failed_attempts=full_statuses.count(STATUS_FAILED),
            timed_out_attempts=full_statuses.count(STATUS_TIMEOUT),
            unevaluated_attempts=full_statuses.count(STATUS_UNEVALUATED),
            missing_candidate_attempts=full_statuses.count(STATUS_MISSING_CANDIDATE),
        )

        # ------------------------------------------------------------------
        # Strategy 2: Text-Based Search/Replace (Aider style)
        # ------------------------------------------------------------------
        diff_search = simulated_search_block
        if simulate_diff_whitespace_error:
            # Simulate real-world 4-space vs 8-space indentation mismatch
            diff_search = "    " + simulated_search_block.lstrip()

        diff_prompt = f"{GLOBAL_STATIC_PREFIX}\n\nTask: {edit_instruction}\n\nDiff Instructions\nFile:\n{original_source}"
        diff_in_tokens = get_token_count(diff_prompt)
        diff_out_tokens = get_token_count(
            f"<<<< SEARCH\n{diff_search}\n====\n{simulated_replace_block}\n>>>>"
        )

        diff_latencies: List[float] = []
        diff_statuses: List[str] = []
        diff_err: Optional[str] = None
        diff_exec: Optional[ExecutionResult] = None
        diff_patched_code = original_source
        for _ in range(repetitions):
            t0 = time.perf_counter()
            diff_ok, diff_patched_code, diff_err = TextDiffPatcher.apply_search_replace(
                original_source=original_source,
                search_block=diff_search,
                replace_block=simulated_replace_block
            )
            if not diff_ok:
                diff_latencies.append((time.perf_counter() - t0) * 1000.0)
                diff_statuses.append(STATUS_INVALID_EDIT)
                diff_exec = None
                continue
            diff_exec, status = self._evaluate(
                diff_patched_code, test_suite_code, target_name
            )
            diff_latencies.append((time.perf_counter() - t0) * 1000.0)
            diff_statuses.append(status)
        diff_latency = sum(diff_latencies) / len(diff_latencies)
        diff_cost = self._estimate_cost(diff_in_tokens, diff_out_tokens, t2_pricing)

        res_diff = EditStrategyResult(
            strategy_name="Text-Based Search/Replace (Aider Style)",
            success=(diff_statuses.count(STATUS_PASSED) == repetitions),
            status=self._aggregate_status(diff_statuses),
            splicing_error=diff_err,
            input_tokens=diff_in_tokens,
            cached_input_tokens=None,
            output_tokens=diff_out_tokens,
            total_tokens=diff_in_tokens + diff_out_tokens,
            token_source=TOKEN_SOURCE_ESTIMATE,
            cost_usd=diff_cost,
            cost_source=COST_SOURCE_ESTIMATED,
            latency_ms=diff_latency,
            latency_source=LATENCY_SOURCE_LOCAL,
            resulting_code=diff_patched_code if diff_statuses and diff_statuses[-1] != STATUS_INVALID_EDIT else original_source,
            sandbox_result=diff_exec,
            attempts=repetitions,
            passed_attempts=diff_statuses.count(STATUS_PASSED),
            failed_attempts=diff_statuses.count(STATUS_FAILED),
            timed_out_attempts=diff_statuses.count(STATUS_TIMEOUT),
            unevaluated_attempts=diff_statuses.count(STATUS_UNEVALUATED),
            missing_candidate_attempts=diff_statuses.count(STATUS_MISSING_CANDIDATE),
            invalid_edit_attempts=diff_statuses.count(STATUS_INVALID_EDIT),
        )

        # ------------------------------------------------------------------
        # Strategy 3: ITMS AST-Guided Surgical Splicer (Our Approach)
        # ------------------------------------------------------------------
        target = ASTNodeLocator.locate_target(original_source, target_name)
        if not target:
            raise ValueError(f"Could not locate target node '{target_name}' in source code.")

        # Skeletonize context to keep tokens compact and aligned
        skeleton_context = skeletonize_code(
            source_code=original_source,
            target_function_name=target_name,
            strip_docstrings=True,
            prune_unreachable=True,
            task_description=edit_instruction
        )

        itms_prompt = f"{GLOBAL_STATIC_PREFIX}\n\nContext Skeleton:\n{skeleton_context}\n\nTask: {edit_instruction}\n\nTarget Node: {target.qualified_name}\nImplement ONLY the modified replacement node."
        itms_in_tokens = get_token_count(itms_prompt)
        itms_out_tokens = get_token_count(replacement_node_code or "")
        # No provider request is made in this mode, so there is no measured
        # cache-hit figure.  It is reported as unavailable rather than guessed.
        itms_cached_tokens: Optional[int] = None
        itms_cost = self._estimate_cost(
            itms_in_tokens, itms_out_tokens, t1_pricing, cached_input_tokens=0
        )

        itms_latencies: List[float] = []
        itms_statuses: List[str] = []
        itms_exec: Optional[ExecutionResult] = None
        splice_ok = False
        splice_err: Optional[str] = None
        spliced_code = original_source
        for _ in range(repetitions):
            t0 = time.perf_counter()
            splice_ok, spliced_code, splice_err = ASTSurgicalSplicer.splice(
                original_source=original_source,
                target=target,
                replacement_code=replacement_node_code
            )
            if not splice_ok:
                itms_latencies.append((time.perf_counter() - t0) * 1000.0)
                itms_statuses.append(STATUS_INVALID_EDIT)
                itms_exec = None
                continue
            itms_exec, status = self._evaluate(
                spliced_code, test_suite_code, target_name
            )
            itms_latencies.append((time.perf_counter() - t0) * 1000.0)
            itms_statuses.append(status)
        itms_latency = sum(itms_latencies) / len(itms_latencies)

        res_itms = EditStrategyResult(
            strategy_name="ITMS AST-Guided Surgical Splicer",
            success=(itms_statuses.count(STATUS_PASSED) == repetitions),
            status=self._aggregate_status(itms_statuses),
            splicing_error=splice_err,
            input_tokens=itms_in_tokens,
            cached_input_tokens=itms_cached_tokens,
            output_tokens=itms_out_tokens,
            total_tokens=itms_in_tokens + itms_out_tokens,
            token_source=TOKEN_SOURCE_ESTIMATE,
            cost_usd=itms_cost,
            cost_source=COST_SOURCE_ESTIMATED,
            latency_ms=itms_latency,
            latency_source=LATENCY_SOURCE_LOCAL,
            resulting_code=spliced_code if splice_ok else original_source,
            sandbox_result=itms_exec,
            attempts=repetitions,
            passed_attempts=itms_statuses.count(STATUS_PASSED),
            failed_attempts=itms_statuses.count(STATUS_FAILED),
            timed_out_attempts=itms_statuses.count(STATUS_TIMEOUT),
            unevaluated_attempts=itms_statuses.count(STATUS_UNEVALUATED),
            missing_candidate_attempts=itms_statuses.count(STATUS_MISSING_CANDIDATE),
            invalid_edit_attempts=itms_statuses.count(STATUS_INVALID_EDIT),
        )

        # ------------------------------------------------------------------
        # Comparative Impact Deltas
        # ------------------------------------------------------------------
        output_reduction_pct = (
            (1.0 - (res_itms.output_tokens / res_full.output_tokens)) * 100.0
            if res_full.output_tokens and res_full.output_tokens > 0 and res_itms.output_tokens is not None
            else None
        )
        cost_savings_vs_full = (
            (1.0 - (res_itms.cost_usd / res_full.cost_usd)) * 100.0
            if res_full.cost_usd and res_full.cost_usd > 0 and res_itms.cost_usd is not None
            else None
        )
        cost_savings_vs_diff = (
            (1.0 - (res_itms.cost_usd / res_diff.cost_usd)) * 100.0
            if res_diff.cost_usd and res_diff.cost_usd > 0 and res_itms.cost_usd is not None
            else None
        )
        # A latency "speedup" is NOT reported: the measured elapsed time covers
        # local processing only (no provider request was made), so it is not a
        # model-latency comparison.
        speedup: Optional[float] = None

        return TriWayEditBenchmarkReport(
            task_description=edit_instruction,
            target_node_name=target_name,
            full_file_result=res_full,
            search_replace_result=res_diff,
            itms_surgical_result=res_itms,
            output_tokens_saved_vs_full_pct=output_reduction_pct,
            cost_saved_vs_full_pct=cost_savings_vs_full,
            cost_saved_vs_diff_pct=cost_savings_vs_diff,
            latency_speedup_factor=speedup,
            patch_reliability_itms=(res_itms.status == STATUS_PASSED),
            patch_reliability_diff=(res_diff.status == STATUS_PASSED),
            measurement_mode="simulated_local_processing",
            is_measurement=False,
            token_source=TOKEN_SOURCE_ESTIMATE,
            latency_source=LATENCY_SOURCE_LOCAL,
            repetitions=repetitions,
            notes=(
                "Simulated comparison: the candidate code for all three strategies was "
                "supplied by the caller, so no provider request was made. Token counts are "
                "local tiktoken estimates, costs are derived from those estimates and the "
                "catalogue price, and latency is the measured local processing time only. "
                "Provider-measured token/latency figures are unavailable in this mode."
            ),
        )

    @staticmethod
    def _aggregate_status(statuses: List[str]) -> str:
        """Reduces a list of per-attempt statuses into one honest summary."""
        if not statuses:
            return STATUS_UNEVALUATED
        if all(s == STATUS_PASSED for s in statuses):
            return STATUS_PASSED
        for severity in (
            STATUS_INVALID_EDIT,
            STATUS_MISSING_CANDIDATE,
            STATUS_TIMEOUT,
            STATUS_FAILED,
            STATUS_UNEVALUATED,
        ):
            if severity in statuses:
                return severity
        return STATUS_FAILED
