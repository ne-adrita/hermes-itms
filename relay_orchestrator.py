import time
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List, Tuple
from sandbox import ExecutionHarness, ExecutionResult
from model_dispatcher import ModelDispatcher, ModelResponse
from prompt_manager import (
    build_itms_clean_escalation_prompt,
    build_baseline_contaminated_prompt,
    get_token_count,
    GLOBAL_STATIC_PREFIX
)
from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer
from telemetry import (
    TelemetryEngine,
    PRESET_PRICING_PROFILES,
    TIER_1_BUDGET_PROFILES,
    TIER_2_FRONTIER_PROFILES,
    PricingProfile,
    resolve_tier_pricing
)

# Candidate preparation outcomes.
SPLICE_APPLIED = "spliced"            # the candidate was spliced into the context
SPLICE_FALLBACK = "fallback"          # the candidate could not be treated as a replacement
SPLICE_FAILED = "splice_failed"       # the candidate was a replacement but the splice was rejected
CANDIDATE_MISSING = "no_candidate"     # the model returned no usable code


@dataclass
class PreparedCandidate:
    """The result of turning a raw model response into evaluable source code."""
    status: str
    code: str                       # the code that must be handed to the evaluator
    context: str                    # the context that must accompany the code
    error: Optional[str] = None
    target_qualified_name: Optional[str] = None
    evaluated: bool = False         # True only when the code really reached the evaluator
    #: The complete module the candidate produced.
    #:
    #: ``code`` is whatever the pipeline hands to the legacy execution gate.
    #: This field is the *module* a module-level evaluator (the official
    #: Phase 0 evaluator) has to judge: the context with the target
    #: function replaced by the candidate, or the context unchanged when the
    #: candidate was rejected. Keeping it explicit removes any guesswork
    #: about what was evaluated.
    module: str = ""


@dataclass
class CascadeRunResult:
    """End-to-end execution and telemetry report of a live cascade run."""
    task_id: str
    target_function: str
    final_success: bool
    escalated_to_tier2: bool
    verified_code: str

    # Execution gate details
    tier1_result: Optional[ExecutionResult]
    tier2_result: Optional[ExecutionResult]
    
    # Model responses
    tier1_response: Optional[ModelResponse]
    tier2_response: Optional[ModelResponse]
    
    # Prompts inspected
    tier1_prompt: str
    tier2_prompt: Optional[str]
    
    # Financial and Token Telemetry
    total_pipeline_cost_usd: float
    total_tokens_consumed: int
    cached_tokens_consumed: int
    reasoning_tokens_consumed: int
    
    # Counterfactual Comparisons
    direct_frontier_cost_usd: float
    naive_agentic_cost_usd: float
    cost_saved_vs_frontier_pct: float
    cost_saved_vs_naive_pct: float
    
    # Execution Timings
    total_duration_ms: float
    pipeline_log: List[str]

    # Intra-Tier Elastic Cascade Telemetry
    tier1_surgical_repairs: int = 0
    escalation_reason: Optional[str] = None

    # Honest outcome bookkeeping
    outcome_status: str = "unknown"        # passed | failed | timeout | missing_candidate | unevaluated
    evaluated: bool = False                # True only if a candidate really reached the evaluator
    candidate_status: str = "not_attempted"
    splice_status: str = "not_attempted"
    splice_error: Optional[str] = None
    #: The complete module the pipeline produced for the final candidate.
    #: A module-level evaluator (the official Phase 0 evaluator) judges
    #: this; it is the context with the target function replaced by the
    #: candidate, or the context unchanged when the candidate was rejected.
    verified_module: str = ""
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RelayOrchestrator:
    """
    Asymmetric Test-Driven Speculative Cascade (TDSC / ITMS) Orchestrator.
    Manages Tier 1 speculation -> Deterministic Sandbox -> Tier 2 surgical escalation.
    """
    def __init__(
        self,
        harness: Optional[ExecutionHarness] = None,
        dispatcher: Optional[ModelDispatcher] = None
    ):
        self.harness = harness or ExecutionHarness()
        self.dispatcher = dispatcher or ModelDispatcher()

    @staticmethod
    def is_syntactic_or_trivial_error(exec_result: ExecutionResult) -> bool:
        """
        Determines if an execution failure is a repairable error suitable for cheap
        Tier 1 self-repair, rather than a deep semantic logic failure requiring
        Tier 2 frontier reasoning.
        """
        if not exec_result.syntax_valid:
            return True
        trivial_types = (
            "SyntaxError",
            "IndentationError",
            "TabError",
            "NameError",
            "UnboundLocalError",
            "TypeError",
            "AttributeError",
            "IndexError",
            "KeyError",
            "ZeroDivisionError",
            "ValueError",
            "ImportError",
            "ModuleNotFoundError",
            "OverflowError",
            "StopIteration",
            "RecursionError",
            "LookupError",
            "ArithmeticError",
            "NotImplementedError",
            "RuntimeError"
        )
        if exec_result.error_type in trivial_types:
            return True
        return False

    # ------------------------------------------------------------------
    # ITMS generation -> locate -> splice -> validate -> evaluate
    # ------------------------------------------------------------------
    def prepare_candidate(
        self,
        context_code: str,
        target_function_name: str,
        candidate_text: str
    ) -> PreparedCandidate:
        """
        Turns a raw model response into the code that will be evaluated.

        The ITMS contract is that the model returns a *replacement* for the
        target node.  That replacement is therefore located with the AST node
        locator and applied with the surgical splicer, and the **complete**
        spliced source file is what gets handed to the evaluator.  A candidate
        that cannot be spliced is reported as a failed attempt; it is never
        silently treated as a successful edit.
        """
        clean = ASTSurgicalSplicer.clean_replacement_code(candidate_text or "")
        if not clean.strip():
            return PreparedCandidate(
                status=CANDIDATE_MISSING,
                code="",
                context=context_code,
                error="The model returned no candidate code.",
                module=context_code,
            )

        target, locate_error = ASTNodeLocator.locate_target_ex(
            context_code, target_function_name
        )
        if target is None:
            # The target is not in the context at all: the candidate is treated
            # as a standalone module and evaluated as-is.
            return PreparedCandidate(
                status=SPLICE_FALLBACK,
                code=clean,
                context=context_code,
                error=locate_error,
                module=clean,
            )

        ok, spliced, splice_error = ASTSurgicalSplicer.splice(
            original_source=context_code,
            target=target,
            replacement_code=clean,
        )
        if ok:
            return PreparedCandidate(
                status=SPLICE_APPLIED,
                code=spliced,
                context="",
                target_qualified_name=target.qualified_name,
                module=spliced,
            )

        # The candidate was a replacement for a located target but the splice
        # was rejected.  The raw candidate is still handed to the evaluator so
        # that a real diagnostic is produced, but the attempt is recorded as a
        # failed splice and can never be reported as a successful repair.
        return PreparedCandidate(
            status=SPLICE_FAILED,
            code=clean,
            context=context_code,
            error=splice_error,
            target_qualified_name=target.qualified_name,
            module=context_code,
        )

    def evaluate_candidate(
        self,
        prepared: PreparedCandidate,
        test_suite_code: str,
        target_function_name: str
    ) -> ExecutionResult:
        """
        Runs the deterministic gate on a prepared candidate.

        A missing candidate is never executed and never reported as a pass.
        """
        if prepared.status == CANDIDATE_MISSING:
            return ExecutionResult(
                success=False,
                exit_code=1,
                error_type="NoCandidate",
                error_message=prepared.error or "The model returned no candidate code.",
                failing_assertion="NoCandidate: nothing to evaluate",
                stdout="",
                stderr=prepared.error or "",
                duration_ms=0.0,
                timed_out=False,
                syntax_valid=False,
            )

        result = self.harness.execute_test(
            candidate_code=prepared.code,
            context_code=prepared.context,
            test_suite_code=test_suite_code,
            target_function_name=target_function_name
        )
        prepared.evaluated = True
        return result

    def run_cascade(
        self,
        task_description: str,
        raw_context_code: str,
        target_function_name: str,
        test_suite_code: str,
        tier1_model_name: str = "Gemini 3.5 Flash",
        tier2_model_name: str = "Claude Sonnet 5.5 / Fable",
        prune_reachability: bool = True,
        use_expanded_prefix: bool = False,
        enable_tier1_self_repair: bool = True,
        max_tier1_retries: int = 2,
        max_output_tokens: int = 1024
    ) -> CascadeRunResult:
        """
        Executes the full asymmetric relay pipeline with optional Tier 1 self-repair.
        """
        t_start = time.perf_counter()
        logs: List[str] = []
        task_id = f"task_{int(time.time())}"

        t1_pricing = resolve_tier_pricing(tier1_model_name, TIER_1_BUDGET_PROFILES)
        t2_pricing = resolve_tier_pricing(tier2_model_name, TIER_2_FRONTIER_PROFILES)

        # ======================================================================
        # PHASE 1: Tier 1 Speculation
        # ======================================================================
        logs.append(f"[Step 1] Dispatching speculative pass to Tier 1: {tier1_model_name}")
        t1_prompt = f"{GLOBAL_STATIC_PREFIX}\n\nContext:\n{raw_context_code}\n\nTask:\n{task_description}\n\nImplement the function '{target_function_name}'."
        
        t1_resp = self.dispatcher.generate(
            prompt=t1_prompt,
            model_name=tier1_model_name,
            is_tier1=True,
            max_output_tokens=max_output_tokens
        )
        logs.append(f"[Step 1] Tier 1 synthesized candidate code in {t1_resp.latency_ms:.1f}ms ({t1_resp.output_tokens} output tokens)")

        total_t1_tokens_in = t1_resp.raw_tokens_in
        total_t1_tokens_out = t1_resp.output_tokens
        tier1_repairs_count = 0
        last_t1_prompt = t1_prompt
        last_t1_resp = t1_resp
        attempts = 0
        candidate_status = CANDIDATE_MISSING
        splice_status = "not_attempted"
        splice_error: Optional[str] = None

        # ======================================================================
        # PHASE 2: Deterministic Verification Gate & Intra-Tier Self-Repair
        # ======================================================================
        logs.append("[Step 2] Locating target node and splicing Tier 1 candidate, then running the Deterministic Sandbox Gate")
        prepared = self.prepare_candidate(
            raw_context_code, target_function_name, t1_resp.content
        )
        attempts += 1
        candidate_status = prepared.status
        splice_status = prepared.status
        splice_error = prepared.error
        curr_code = prepared.code
        curr_draft = t1_resp.content
        if prepared.status == SPLICE_APPLIED:
            logs.append(f"[Step 2] AST splice applied to '{prepared.target_qualified_name}'.")
        elif prepared.status == SPLICE_FAILED:
            logs.append(f"[Step 2] AST splice REJECTED: {prepared.error}")
        elif prepared.status == CANDIDATE_MISSING:
            logs.append("[Step 2] No candidate code was returned by the model.")

        curr_exec = self.evaluate_candidate(prepared, test_suite_code, target_function_name)
        attempt_ok = curr_exec.success and prepared.status != SPLICE_FAILED

        if attempt_ok:
            # First-pass success: Deliver immediately at zero additional cost!
            logs.append(f"[Step 2] PASSED! Sandboxed unit test succeeded in {curr_exec.duration_ms:.1f}ms. Escalation avoided.")
            total_time_ms = (time.perf_counter() - t_start) * 1000.0
            
            t1_cost = (t1_resp.raw_tokens_in * t1_pricing.cost_per_uncached_token) + (t1_resp.output_tokens * t1_pricing.cost_per_output_token)
            direct_frontier_cost = (t1_resp.raw_tokens_in * t2_pricing.cost_per_uncached_token) + (t1_resp.output_tokens * t2_pricing.cost_per_output_token)

            return CascadeRunResult(
                task_id=task_id,
                target_function=target_function_name,
                final_success=True,
                escalated_to_tier2=False,
                verified_code=prepared.code,
                verified_module=prepared.module,
                tier1_result=curr_exec,
                tier2_result=None,
                tier1_response=t1_resp,
                tier2_response=None,
                tier1_prompt=t1_prompt,
                tier2_prompt=None,
                total_pipeline_cost_usd=t1_cost,
                total_tokens_consumed=t1_resp.raw_tokens_in + t1_resp.output_tokens,
                cached_tokens_consumed=0,
                reasoning_tokens_consumed=0,
                direct_frontier_cost_usd=direct_frontier_cost,
                naive_agentic_cost_usd=t1_cost,
                cost_saved_vs_frontier_pct=((1.0 - (t1_cost / direct_frontier_cost)) * 100.0) if direct_frontier_cost > 0 else 0.0,
                cost_saved_vs_naive_pct=0.0,
                total_duration_ms=total_time_ms,
                pipeline_log=logs,
                tier1_surgical_repairs=0,
                escalation_reason=None,
                outcome_status="passed",
                evaluated=True,
                candidate_status=candidate_status,
                splice_status=splice_status,
                splice_error=splice_error,
                attempts=attempts
            )

        # First pass failed
        logs.append(f"[Step 2] FAILED: {curr_exec.error_type} ({curr_exec.failing_assertion})")

        # Determine repairability and budget
        is_trivial = self.is_syntactic_or_trivial_error(curr_exec)
        # Syntactic/runtime errors get max_tier1_retries; assertion errors get 1 attempt to avoid costly Tier 2 calls on minor calculation slips
        allowed_retries = max_tier1_retries if is_trivial else (1 if (enable_tier1_self_repair and curr_exec.error_type == "AssertionError") else 0)

        if enable_tier1_self_repair and allowed_retries > 0:
            logs.append(f"[Step 2-R] Triage: Detected repairable error ({curr_exec.error_type}). Triggering Tier 1 intra-tier self-repair loop (budget: up to {allowed_retries} retries).")
            attempt = 0
            while attempt < allowed_retries:
                attempt += 1
                tier1_repairs_count += 1
                diagnostic_signal = curr_exec.to_clean_diagnostic_signal()
                repair_prompt = (
                    f"{GLOBAL_STATIC_PREFIX}\n\n"
                    f"Task: Intra-Tier Surgical Repair. Implement the function '{target_function_name}'.\n\n"
                    f"Context:\n{raw_context_code}\n\n"
                    f"Your previous code produced this error:\n"
                    f"{diagnostic_signal}\n\n"
                    f"Previous Draft:\n{curr_draft}\n\n"
                    f"Fix the error in your candidate implementation. Ensure the complete corrected implementation is provided within a ```python ... ``` block."
                )
                last_t1_prompt = repair_prompt
                logs.append(f"[Step 2-R] Dispatching self-repair attempt {attempt}/{allowed_retries} to {tier1_model_name}...")
                repair_resp = self.dispatcher.generate(
                    prompt=repair_prompt,
                    model_name=tier1_model_name,
                    is_tier1=True,
                    max_output_tokens=max_output_tokens
                )
                total_t1_tokens_in += repair_resp.raw_tokens_in
                total_t1_tokens_out += repair_resp.output_tokens
                last_t1_resp = repair_resp

                prepared = self.prepare_candidate(
                    raw_context_code, target_function_name, repair_resp.content
                )
                attempts += 1
                candidate_status = prepared.status
                splice_status = prepared.status
                splice_error = prepared.error
                curr_code = prepared.code
                curr_draft = repair_resp.content
                if prepared.status == SPLICE_APPLIED:
                    logs.append(f"[Step 2-R] AST splice applied to '{prepared.target_qualified_name}'.")
                elif prepared.status == SPLICE_FAILED:
                    logs.append(f"[Step 2-R] AST splice REJECTED: {prepared.error}")

                curr_exec = self.evaluate_candidate(prepared, test_suite_code, target_function_name)
                attempt_ok = curr_exec.success and prepared.status != SPLICE_FAILED

                if attempt_ok:
                    logs.append(f"[Step 2-R] PASSED! Tier 1 intra-tier self-repair succeeded on attempt {attempt} in {curr_exec.duration_ms:.1f}ms. Tier 2 escalation avoided.")
                    total_time_ms = (time.perf_counter() - t_start) * 1000.0
                    t1_cost = (total_t1_tokens_in * t1_pricing.cost_per_uncached_token) + (total_t1_tokens_out * t1_pricing.cost_per_output_token)
                    direct_frontier_cost = (t1_resp.raw_tokens_in * t2_pricing.cost_per_uncached_token) + (t1_resp.output_tokens * t2_pricing.cost_per_output_token)

                    return CascadeRunResult(
                        task_id=task_id,
                        target_function=target_function_name,
                        final_success=True,
                        escalated_to_tier2=False,
                        verified_code=curr_code,
                        tier1_result=curr_exec,
                        tier2_result=None,
                        tier1_response=last_t1_resp,
                        tier2_response=None,
                        tier1_prompt=last_t1_prompt,
                        tier2_prompt=None,
                        total_pipeline_cost_usd=t1_cost,
                        total_tokens_consumed=total_t1_tokens_in + total_t1_tokens_out,
                        cached_tokens_consumed=0,
                        reasoning_tokens_consumed=0,
                        direct_frontier_cost_usd=direct_frontier_cost,
                        naive_agentic_cost_usd=t1_cost,
                        cost_saved_vs_frontier_pct=((1.0 - (t1_cost / direct_frontier_cost)) * 100.0) if direct_frontier_cost > 0 else 0.0,
                        cost_saved_vs_naive_pct=0.0,
                        total_duration_ms=total_time_ms,
                        pipeline_log=logs,
                        tier1_surgical_repairs=tier1_repairs_count,
                        escalation_reason=None
                    )
                else:
                    logs.append(f"[Step 2-R] Attempt {attempt} failed: {curr_exec.error_type} ({curr_exec.failing_assertion})")
                    # If error transitioned into a trivial syntax/runtime error, ensure Tier 1 gets budget to fix it
                    if self.is_syntactic_or_trivial_error(curr_exec) and allowed_retries < max_tier1_retries:
                        allowed_retries = max_tier1_retries
                        is_trivial = True

                    if curr_exec.error_type == "AssertionError" and not is_trivial:
                        logs.append("[Step 2-R] Tier 1 assertion repair attempt unresolved. Escalating to Tier 2 frontier model.")
                        break
                    if not self.is_syntactic_or_trivial_error(curr_exec) and curr_exec.error_type != "AssertionError":
                        logs.append("[Step 2-R] Error transitioned to non-trivial failure. Halting intra-tier loop for clean Tier 2 escalation.")
                        break

        # ======================================================================
        # PHASE 3: Surgical Clean-Trace Escalation (Tier 2)
        # ======================================================================
        escalation_reason = (
            f"Tier 1 self-repair retries exhausted ({tier1_repairs_count} attempts)"
            if tier1_repairs_count > 0
            else f"Tier 1 semantic logic failure ({curr_exec.error_type})"
        )
        logs.append(f"[Step 3] Escalation Reason: {escalation_reason}")
        logs.append("[Step 3] Anti-Anchoring Invariant: Discarding broken Tier 1 code draft completely.")
        
        # Build ITMS clean-trace escalation prompt
        itms_prompt_data = build_itms_clean_escalation_prompt(
            task_description=task_description,
            raw_context_code=raw_context_code,
            target_function_name=target_function_name,
            failing_test_case=curr_exec.failing_assertion or "Assertion failed",
            error_type=curr_exec.error_type or "ExecutionError",
            error_message=curr_exec.error_message or "Test harness returned non-zero exit code",
            strip_docstrings=True,
            prune_unreachable=prune_reachability,
            use_expanded_prefix=use_expanded_prefix
        )

        logs.append(f"[Step 3] AST pruned {itms_prompt_data['lines_pruned']} lines; static prefix aligned at token 0 ({itms_prompt_data['static_token_count']} cached tokens)")
        logs.append(f"[Step 3] Escalating to Tier 2 Frontier Model: {tier2_model_name}")

        t2_resp = self.dispatcher.generate(
            prompt=itms_prompt_data["full_prompt"],
            model_name=tier2_model_name,
            is_tier1=False,
            max_output_tokens=max_output_tokens
        )
        logs.append(f"[Step 3] Tier 2 synthesized solution in {t2_resp.latency_ms:.1f}ms ({t2_resp.output_tokens} output tokens, {t2_resp.reasoning_tokens} thinking tokens)")

        # ======================================================================
        # PHASE 4: Final Deterministic Verification
        # ======================================================================
        logs.append("[Step 4] Locating target node, splicing Tier 2 candidate, then re-verifying in the Deterministic Sandbox Gate")
        t2_prepared = self.prepare_candidate(
            raw_context_code, target_function_name, t2_resp.content
        )
        attempts += 1
        candidate_status = t2_prepared.status
        splice_status = t2_prepared.status
        splice_error = t2_prepared.error
        if t2_prepared.status == SPLICE_APPLIED:
            logs.append(f"[Step 4] AST splice applied to '{t2_prepared.target_qualified_name}'.")
        elif t2_prepared.status == SPLICE_FAILED:
            logs.append(f"[Step 4] AST splice REJECTED: {t2_prepared.error}")

        t2_exec = self.evaluate_candidate(t2_prepared, test_suite_code, target_function_name)

        final_pass = t2_exec.success and t2_prepared.status != SPLICE_FAILED
        if final_pass:
            logs.append(f"[Step 4] SUCCESS! Tier 2 resolved all assertion failures in {t2_exec.duration_ms:.1f}ms.")
        elif t2_prepared.status == CANDIDATE_MISSING:
            logs.append("[Step 4] Tier 2 returned no candidate code. Nothing was evaluated.")
        else:
            logs.append(f"[Step 4] Tier 2 execution failed: {t2_exec.error_type}")

        if t2_exec.timed_out:
            outcome_status = "timeout"
        elif final_pass:
            outcome_status = "passed"
        elif t2_prepared.status == CANDIDATE_MISSING:
            outcome_status = "missing_candidate"
        elif not t2_prepared.evaluated:
            outcome_status = "unevaluated"
        else:
            outcome_status = "failed"

        total_time_ms = (time.perf_counter() - t_start) * 1000.0

        # ======================================================================
        # PHASE 5: Telemetry & Counterfactual Calculations
        # ======================================================================
        # 1. Real ITMS Cost
        t1_cost = (total_t1_tokens_in * t1_pricing.cost_per_uncached_token) + (total_t1_tokens_out * t1_pricing.cost_per_output_token)
        t2_cost = (
            (itms_prompt_data["static_token_count"] * t2_pricing.cost_per_cached_token) +
            (itms_prompt_data["dynamic_token_count"] * t2_pricing.cost_per_uncached_token) +
            ((t2_resp.output_tokens + t2_resp.reasoning_tokens) * t2_pricing.cost_per_output_token)
        )
        total_itms_cost = t1_cost + t2_cost

        # 2. Counterfactual Direct Frontier Cost
        direct_frontier_tokens_in = get_token_count(f"{raw_context_code}\n\n{task_description}")
        direct_frontier_cost = (
            (direct_frontier_tokens_in * t2_pricing.cost_per_uncached_token) +
            ((300 + 1500) * t2_pricing.cost_per_output_token)
        )

        # 3. Counterfactual Naive Agentic Cost (Broken draft forwarded + 2,850 thinking tokens)
        naive_prompt_data = build_baseline_contaminated_prompt(
            task_description=task_description,
            raw_context_code=raw_context_code,
            broken_code_draft=curr_code,
            failing_test_case=curr_exec.failing_assertion or "Assertion failed",
            full_traceback=curr_exec.stderr
        )
        naive_t2_cost = (
            (naive_prompt_data["total_tokens"] * t2_pricing.cost_per_uncached_token) +
            ((310 + 2850) * t2_pricing.cost_per_output_token)
        )
        naive_agentic_cost = t1_cost + naive_t2_cost

        savings_vs_frontier = (
            (1.0 - (total_itms_cost / direct_frontier_cost)) * 100.0
            if direct_frontier_cost > 0 else 0.0
        )
        savings_vs_naive = (
            (1.0 - (total_itms_cost / naive_agentic_cost)) * 100.0
            if naive_agentic_cost > 0 else 0.0
        )

        return CascadeRunResult(
            task_id=task_id,
            target_function=target_function_name,
            final_success=final_pass,
            escalated_to_tier2=True,
            verified_code=t2_prepared.code if final_pass else curr_code,
            verified_module=t2_prepared.module,
            tier1_result=curr_exec,
            tier2_result=t2_exec,
            tier1_response=last_t1_resp,
            tier2_response=t2_resp,
            tier1_prompt=last_t1_prompt,
            tier2_prompt=itms_prompt_data["full_prompt"],
            total_pipeline_cost_usd=total_itms_cost,
            total_tokens_consumed=(total_t1_tokens_in + total_t1_tokens_out + itms_prompt_data["total_tokens"] + t2_resp.output_tokens + t2_resp.reasoning_tokens),
            cached_tokens_consumed=itms_prompt_data["static_token_count"],
            reasoning_tokens_consumed=t2_resp.reasoning_tokens,
            direct_frontier_cost_usd=direct_frontier_cost,
            naive_agentic_cost_usd=naive_agentic_cost,
            cost_saved_vs_frontier_pct=savings_vs_frontier,
            cost_saved_vs_naive_pct=savings_vs_naive,
            total_duration_ms=total_time_ms,
            pipeline_log=logs,
            tier1_surgical_repairs=tier1_repairs_count,
            escalation_reason=escalation_reason,
            outcome_status=outcome_status,
            evaluated=t2_prepared.evaluated,
            candidate_status=candidate_status,
            splice_status=splice_status,
            splice_error=splice_error,
            attempts=attempts
        )
