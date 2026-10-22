import os

import streamlit as st
from config import AppConfig
from prompt_manager import (
    build_itms_clean_escalation_prompt,
    build_tdsc_clean_escalation_prompt,
    build_baseline_contaminated_prompt,
    sanitize_runtime_traceback,
    get_token_count,
    GLOBAL_STATIC_PREFIX,
    EXPANDED_CACHED_PREFIX_1024
)
from telemetry import (
    TelemetryEngine,
    PRESET_PRICING_PROFILES,
    TIER_1_BUDGET_PROFILES,
    TIER_2_FRONTIER_PROFILES,
    PricingProfile,
    resolve_tier_pricing
)
from sandbox import ExecutionHarness, describe_isolation
from model_dispatcher import ModelDispatcher
from relay_orchestrator import RelayOrchestrator
from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer, TextDiffPatcher, SurgicalTarget
from edit_orchestrator import SurgicalEditOrchestrator
from benchmark_harness import MacroBenchmarkSuite
from completed_benchmark_viewer import (
    DEFAULT_ANALYSIS_DIR,
    DEFAULT_RUN_DIR,
    GRAPH_FILENAMES,
    PROJECT_ROOT,
    compute_method_metrics,
    compute_overall,
    load_run,
    read_graph_bytes,
)
from comparative_viewer import (
    SYSTEM_LABELS,
    SYSTEM_ORDER,
    compute_comparative_metrics,
    figure_paths,
    load_comparative_run,
    read_graph_bytes as read_comparative_graph_bytes,
)

# --- Standard Benchmark Presets ---
SAMPLE_CONTEXT_CODE = '''class UnusedAnalyticsTracker:
    def record_latency(self, ms: float):
        pass
    def flush_telemetry(self):
        pass

class TransactionRecord:
    def __init__(self, tx_id: str, amount: float):
        self.tx_id = tx_id
        self.amount = amount

    def validate_checksum(self) -> bool:
        running_sum = 0
        for char in self.tx_id:
            running_sum = (running_sum * 31 + ord(char)) % 1000000007
        return running_sum == 423891

    def calculate_tax_allocation(self, rate: float) -> float:
        if self.amount <= 0:
            return 0.0
        base = self.amount * rate
        surcharge = 0.02 * base if base > 1000 else 0.0
        return round(base + surcharge, 2)

class AccountLedger:
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.transactions = []

    def append_transaction(self, record: TransactionRecord):
        self.transactions.append(record)

    def reconcile_balance(self, target_date: str) -> float:
        pass
'''

SAMPLE_TASK = "Implement reconcile_balance in AccountLedger. Calculate the total verified amount of all transactions, deducting tax allocations at rate 0.05. If a transaction fails validate_checksum, do not include it."

SAMPLE_BROKEN_DRAFT = '''    def reconcile_balance(self, target_date: str) -> float:
        total = 0
        for tx in self.transactions:
            # Bug: Inverted condition and missing tax deduction logic
            if not tx.validate_checksum():
                total += tx.amount
        return float(total)'''

SAMPLE_TRACEBACK = '''Traceback (most recent call last):
  File "test_runner.py", line 42, in test_reconcile_balance
    assert ledger.reconcile_balance("2026-10-01") == 950.0
AssertionError: assert 0.0 == 950.0'''

SAMPLE_FAILING_TEST = 'assert ledger.reconcile_balance("2026-10-01") == 950.0'
SAMPLE_ERROR_TYPE = "AssertionError"
SAMPLE_ERROR_MSG = "assert 0.0 == 950.0"

SAMPLE_TEST_SUITE = '''ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
'''

# Secondary Preset
SCENARIO_2_CONTEXT = '''import hashlib

class DeadLogger:
    def write_disk(self, msg: str):
        pass

class StreamPayload:
    def __init__(self, payload_id: str, raw_bytes: bytes):
        self.payload_id = payload_id
        self.raw_bytes = raw_bytes

    def compute_sha256(self) -> str:
        hasher = hashlib.sha256()
        hasher.update(self.raw_bytes)
        return hasher.hexdigest()

    def decode_envelope(self) -> dict:
        import json
        return json.loads(self.raw_bytes.decode('utf-8'))

class IngestionPipeline:
    def __init__(self, stream_channel: str):
        self.stream_channel = stream_channel
        self.queue = []

    def dispatch_batch(self, expected_hash_prefix: str) -> int:
        pass
'''
SCENARIO_2_TASK = "Implement dispatch_batch in IngestionPipeline. Process all queued payloads whose compute_sha256() starts with expected_hash_prefix. Return the count of successfully dispatched packets."
SCENARIO_2_BROKEN_DRAFT = '''    def dispatch_batch(self, expected_hash_prefix: str) -> int:
        dispatched = 0
        for p in self.queue:
            if len(p.payload_id) == expected_hash_prefix:
                dispatched += 1
        return dispatched'''
SCENARIO_2_TRACEBACK = '''Traceback (most recent call last):
  File "test_pipeline.py", line 88, in test_dispatch_batch
    assert pipeline.dispatch_batch("a1b2") == 4
AssertionError: assert 0 == 4'''
SCENARIO_2_FAILING_TEST = 'assert pipeline.dispatch_batch("a1b2") == 4'
SCENARIO_2_ERROR_TYPE = "AssertionError"
SCENARIO_2_ERROR_MSG = "assert 0 == 4"
SCENARIO_2_TEST_SUITE = '''p1 = StreamPayload('1', b'{"msg": "data"}')
p1.compute_sha256 = lambda: "a1b2_test"
pipeline = IngestionPipeline("channel")
pipeline.queue = [p1]
assert pipeline.dispatch_batch("a1b2") == 1
'''


def inject_custom_styles():
    """Injects high-contrast dark theme CSS with complementary accent palettes."""
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #0B0F17;
            color: #E2E8F0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        }

        .header-card {
            padding: 1.1rem 1.4rem;
            background: linear-gradient(135deg, #111827 0%, #161F30 100%);
            border: 1px solid #1E293B;
            border-radius: 10px;
            margin-bottom: 1.25rem;
        }
        .header-title {
            font-size: 1.6rem;
            font-weight: 700;
            color: #F8FAFC;
            letter-spacing: -0.02em;
            margin-bottom: 0.25rem;
        }
        .header-desc {
            font-size: 0.88rem;
            color: #94A3B8;
        }

        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 0.85rem;
            margin-bottom: 1.25rem;
        }
        @media (max-width: 900px) {
            .kpi-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        .kpi-box {
            background: #111827;
            border-radius: 8px;
            padding: 1rem 1.15rem;
            border: 1px solid #1E293B;
        }
        .kpi-box-green { border-top: 3px solid #10B981; }
        .kpi-box-cyan  { border-top: 3px solid #0EA5E9; }
        .kpi-box-purple{ border-top: 3px solid #8B5CF6; }
        .kpi-box-amber { border-top: 3px solid #F59E0B; }

        .kpi-title {
            font-size: 0.75rem;
            text-transform: uppercase;
            font-weight: 600;
            color: #94A3B8;
            letter-spacing: 0.04em;
            margin-bottom: 0.25rem;
        }
        .kpi-number {
            font-size: 1.65rem;
            font-weight: 700;
            color: #F8FAFC;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }
        .kpi-number-green { color: #34D399; }
        .kpi-detail {
            font-size: 0.74rem;
            color: #64748B;
            margin-top: 0.2rem;
        }

        .bar-card {
            background: #111827;
            border: 1px solid #1E293B;
            border-radius: 8px;
            padding: 0.9rem 1.15rem;
            margin-bottom: 1.25rem;
        }
        .bar-header {
            font-size: 0.78rem;
            font-weight: 600;
            color: #94A3B8;
            margin-bottom: 0.4rem;
            display: flex;
            justify-content: space-between;
        }
        .bar-track {
            display: flex;
            height: 18px;
            border-radius: 4px;
            overflow: hidden;
            background: #1E293B;
            margin-bottom: 0.45rem;
        }
        .seg-cached { background-color: #10B981; }
        .seg-uncached { background-color: #0EA5E9; }
        .seg-output { background-color: #6366F1; }
        .seg-reasoning { background-color: #A855F7; }
        .seg-ruminate { background-color: #E11D48; }

        .legend-row {
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            font-size: 0.72rem;
            color: #94A3B8;
            margin-top: 0.35rem;
        }
        .legend-dot {
            width: 8px;
            height: 8px;
            border-radius: 2px;
            display: inline-block;
            margin-right: 0.3rem;
        }

        .system-card {
            background: #111827;
            border: 1px solid #1E293B;
            border-radius: 10px;
            padding: 1.15rem 1.25rem;
            height: 100%;
        }
        .system-card-itms { border-top: 3px solid #10B981; }
        .system-card-baseline { border-top: 3px solid #F43F5E; }
        .system-card-header {
            font-size: 1.05rem;
            font-weight: 700;
            color: #F8FAFC;
            margin-bottom: 0.2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .system-subtitle {
            font-size: 0.78rem;
            color: #94A3B8;
            margin-bottom: 0.9rem;
        }

        .status-container {
            background: #0B0F17;
            border: 1px solid #1E293B;
            border-radius: 6px;
            padding: 0.75rem 0.9rem;
            margin-top: 0.85rem;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.78rem;
            line-height: 1.55;
        }
        .badge {
            font-size: 0.68rem;
            font-weight: 700;
            padding: 0.1rem 0.35rem;
            border-radius: 3px;
            margin-right: 0.45rem;
        }
        .badge-green { background: rgba(16,185,129,0.2); color: #34D399; border: 1px solid rgba(16,185,129,0.3); }
        .badge-rose  { background: rgba(244,63,94,0.2);  color: #FB7185; border: 1px solid rgba(244,63,94,0.3); }
        .badge-blue  { background: rgba(14,165,233,0.2); color: #38BDF8; border: 1px solid rgba(14,165,233,0.3); }

        .terminal-box {
            background: #050811;
            border: 1px solid #1E293B;
            border-radius: 6px;
            padding: 0.85rem 1rem;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.82rem;
            color: #E2E8F0;
            line-height: 1.6;
        }

        .model-card {
            background: #111827;
            border: 1px solid #1E293B;
            border-radius: 8px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.75rem;
        }
        .model-card-tier1 { border-left: 3px solid #0EA5E9; }
        .model-card-tier2 { border-left: 3px solid #8B5CF6; }
        .model-name { font-size: 0.95rem; font-weight: 700; color: #F8FAFC; }
        .model-meta { font-size: 0.75rem; color: #94A3B8; margin-top: 0.2rem; }
        .model-cost { font-family: ui-monospace, monospace; font-size: 0.8rem; color: #34D399; margin-top: 0.25rem; }
        .model-desc { font-size: 0.78rem; color: #CBD5E1; margin-top: 0.35rem; }

        section[data-testid="stSidebar"] {
            background-color: #0D131F !important;
            border-right: 1px solid #1E293B;
        }
        </style>
        """,
        unsafe_allow_html=True
    )


def main():
    st.set_page_config(layout="wide", page_title="ITMS - Test-Driven Speculative Cascade")
    inject_custom_styles()

    # --- Top Header ---
    st.markdown(
        """
        <div class="header-card">
            <div class="header-title">Input Token Minimization System (ITMS)</div>
            <div class="header-desc">Front-end token minimization, sandboxed deterministic gate, and two-tiered model relay.</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # --- Completed Benchmark At A Glance (read-only) ---
    # The finished 120-attempt ITMS run is summarised at the top of EVERY tab
    # so the results are visible without navigating to another tab. This only
    # reads the artefacts of the completed run: it never runs a benchmark and
    # never contacts a model provider.
    completed_run = load_run(DEFAULT_RUN_DIR, DEFAULT_ANALYSIS_DIR)
    _render_completed_benchmark_banner(completed_run)

    # --- Sidebar Controls ---
    st.sidebar.markdown("### Configuration")

    scenario_option = st.sidebar.selectbox(
        "Benchmark Task",
        options=[
            "Account Ledger Reconciliation [Default]",
            "Data Pipeline & SHA256 Validator",
            "Custom Code Input"
        ],
        index=0
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Provider Secrets & Status")
    
    # Provider status checklist with masked keys
    configured = AppConfig.get_configured_providers()
    for prov, is_set in configured.items():
        key_val = AppConfig.get_api_key(prov.lower())
        masked = AppConfig.mask_key(key_val)
        if is_set:
            st.sidebar.caption(f"{prov}: active ({masked})")
        else:
            st.sidebar.caption(f"{prov}: [Mock Mode]")

    force_mock = st.sidebar.checkbox("Force Deterministic Mock Mode", value=False)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Hardware & Cache Optimization")

    prefix_mode = st.sidebar.radio(
        "Static Prefix Sizing",
        options=["Standard (73 Tokens - vLLM/SGLang)", "Provider-Padded (1024+ Tokens - Anthropic/OpenAI)"],
        index=0
    )
    use_expanded = (prefix_mode == "Provider-Padded (1024+ Tokens - Anthropic/OpenAI)")

    prune_reachability = st.sidebar.checkbox(
        "AST Reachability Pruning (Strip Dead Classes)",
        value=True,
        help="Analyzes symbol references and discards unreferenced classes/helpers completely."
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Model Tiers")
    tier1_names = list(TIER_1_BUDGET_PROFILES.keys())
    tier1_default = AppConfig.get_default_tier1_model(tier1_names)
    if tier1_default and tier1_default not in tier1_names:
        # An explicit OpenRouter model ID configured via DEFAULT_TIER1_MODEL.
        tier1_names.insert(0, tier1_default)
    tier1_choice = st.sidebar.selectbox(
        "Tier 1 Speculator",
        options=tier1_names,
        index=tier1_names.index(tier1_default) if tier1_default in tier1_names else 0
    )
    selected_t1_pricing: PricingProfile = resolve_tier_pricing(tier1_choice, TIER_1_BUDGET_PROFILES)

    tier2_names = list(TIER_2_FRONTIER_PROFILES.keys())
    tier2_default = AppConfig.get_default_tier2_model(tier2_names)
    if tier2_default and tier2_default not in tier2_names:
        tier2_names.insert(0, tier2_default)
    tier2_choice = st.sidebar.selectbox(
        "Tier 2 Escalation Target",
        options=tier2_names,
        index=tier2_names.index(tier2_default) if tier2_default in tier2_names else 0
    )
    selected_t2_pricing: PricingProfile = resolve_tier_pricing(tier2_choice, TIER_2_FRONTIER_PROFILES)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Self-Repair & Cascade Limits")
    enable_tier1_repair = st.sidebar.checkbox(
        "Tier 1 Intra-Tier Self-Repair",
        value=True,
        help="Permits cheap Tier 1 model to repair trivial syntax/indentation slips without Tier 2 escalation."
    )
    max_tier1_retries = st.sidebar.number_input(
        "Max Tier 1 Retries",
        min_value=1,
        max_value=5,
        value=2,
        step=1
    )

    # --- Setup Current Scenario Data ---
    if scenario_option == "Account Ledger Reconciliation [Default]":
        current_context = SAMPLE_CONTEXT_CODE
        current_task = SAMPLE_TASK
        current_target = "reconcile_balance"
        current_failing_test = SAMPLE_FAILING_TEST
        current_error_type = SAMPLE_ERROR_TYPE
        current_error_msg = SAMPLE_ERROR_MSG
        current_broken_draft = SAMPLE_BROKEN_DRAFT
        current_traceback = SAMPLE_TRACEBACK
        current_test_suite = SAMPLE_TEST_SUITE
    elif scenario_option == "Data Pipeline & SHA256 Validator":
        current_context = SCENARIO_2_CONTEXT
        current_task = SCENARIO_2_TASK
        current_target = "dispatch_batch"
        current_failing_test = SCENARIO_2_FAILING_TEST
        current_error_type = SCENARIO_2_ERROR_TYPE
        current_error_msg = SCENARIO_2_ERROR_MSG
        current_broken_draft = SCENARIO_2_BROKEN_DRAFT
        current_traceback = SCENARIO_2_TRACEBACK
        current_test_suite = SCENARIO_2_TEST_SUITE
    else:
        st.subheader("Custom Code Scenario")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            current_task = st.text_area("Task Description", value=SAMPLE_TASK, height=70)
            current_context = st.text_area("Background Helper Code", value=SAMPLE_CONTEXT_CODE, height=150)
            current_target = st.text_input("Target Function Name", value="reconcile_balance")
        with col_c2:
            current_test_suite = st.text_area("Executable Test Suite Assertions", value=SAMPLE_TEST_SUITE, height=90)
            current_broken_draft = st.text_area("Broken Draft (Baseline Only)", value=SAMPLE_BROKEN_DRAFT, height=90)
            current_traceback = st.text_area("Raw Execution Traceback", value=SAMPLE_TRACEBACK, height=70)
            
            clean_diag = sanitize_runtime_traceback(current_traceback)
            current_failing_test = clean_diag["failing_assertion"]
            current_error_type = clean_diag["error_type"]
            current_error_msg = clean_diag["error_message"]

    # --- Navigation Tabs ---
    (
        tab_completed,
        tab_comparative,
        tab_runner,
        tab_telemetry,
        tab_cascade,
        tab_models,
        tab_surgical,
        tab_benchmark,
    ) = st.tabs([
        "Completed Benchmark Results",
        "Comparative Benchmark Results",
        "Live Relay Runner (Milestones 2 & 3)",
        "Single-Query Prompt Telemetry",
        "Full Cascade Lifecycle Economics",
        "Model Catalog & Tier Analysis",
        "Surgical Code Modification (Phase 2)",
        "Macro Benchmark Suite (5-Task)"
    ])

    # =========================================================================
    # TAB 1: Live Relay Runner
    # =========================================================================
    with tab_runner:
        st.subheader("Deterministic Execution Gate & Live Relay Orchestrator")
        st.caption(
            "Executes Tier 1 speculation -> AST splice -> Deterministic Execution Gate -> Tier 2 Clean-Trace Escalation."
        )
        iso = describe_isolation()
        with st.expander("Execution gate isolation level (read before trusting it with untrusted code)"):
            st.write(f"- Isolation level: `{iso['isolation_level']}`")
            st.markdown("**Enforced**")
            for item in iso["enforced"]:
                st.write(f"- {item}")
            st.markdown("**Not enforced**")
            for item in iso["not_enforced"]:
                st.write(f"- {item}")

        col_btn, _ = st.columns([1, 4])
        run_relay = col_btn.button("Run Live Cascade Relay", type="primary")

        if run_relay:
            dispatcher = ModelDispatcher(force_mock=force_mock)
            harness = ExecutionHarness(default_timeout=AppConfig.get_execution_timeout())
            orchestrator = RelayOrchestrator(harness=harness, dispatcher=dispatcher)

            with st.spinner("Executing asymmetric cascade relay..."):
                run_res = orchestrator.run_cascade(
                    task_description=current_task,
                    raw_context_code=current_context,
                    target_function_name=current_target,
                    test_suite_code=current_test_suite,
                    tier1_model_name=tier1_choice,
                    tier2_model_name=tier2_choice,
                    prune_reachability=prune_reachability,
                    use_expanded_prefix=use_expanded,
                    enable_tier1_self_repair=enable_tier1_repair,
                    max_tier1_retries=int(max_tier1_retries)
                )

            # Determine granular status narrative
            if run_res.escalated_to_tier2:
                status_detail = f"Escalated to Tier 2 ({run_res.escalation_reason or 'Logic failure'})"
            elif run_res.tier1_surgical_repairs > 0:
                status_detail = f"Tier 1 Intra-Tier Repair ({run_res.tier1_surgical_repairs} attempts)"
            else:
                status_detail = "Direct Tier 1 First-Pass Pass"

            # Impact KPI Ribbon
            st.markdown(
                f"""
                <div class="kpi-grid">
                    <div class="kpi-box kpi-box-green">
                        <div class="kpi-title">Final Status</div>
                        <div class="kpi-number kpi-number-green">{run_res.outcome_status.upper()}</div>
                        <div class="kpi-detail">{status_detail}</div>
                    </div>
                    <div class="kpi-box kpi-box-cyan">
                        <div class="kpi-title">Savings vs Direct Frontier</div>
                        <div class="kpi-number">{run_res.cost_saved_vs_frontier_pct:.1f}%</div>
                        <div class="kpi-detail">Modelled from the catalogue price profile</div>
                    </div>
                    <div class="kpi-box kpi-box-purple">
                        <div class="kpi-title">Savings vs Naive Agentic</div>
                        <div class="kpi-number">{run_res.cost_saved_vs_naive_pct:.1f}%</div>
                        <div class="kpi-detail">Modelled from the catalogue price profile</div>
                    </div>
                    <div class="kpi-box kpi-box-amber">
                        <div class="kpi-title">Total Latency</div>
                        <div class="kpi-number">{run_res.total_duration_ms:.0f}ms</div>
                        <div class="kpi-detail">Measured end-to-end (monotonic clock)</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

            st.caption(
                f"Attempts: {run_res.attempts} | Evaluated: {run_res.evaluated} | "
                f"Candidate status: {run_res.candidate_status} | Splice status: {run_res.splice_status}"
                + (f" | Splice error: {run_res.splice_error}" if run_res.splice_error else "")
                + ". Cost savings are modelled from the catalogue price profile and the token counts "
                "reported by the provider (or by the mock engine in mock mode); they are not billed spend. "
                "The execution gate is a subprocess with a scrubbed environment, not an OS-level sandbox."
            )

            # Pipeline Trace Logs
            st.markdown("#### Execution Trace Log")
            log_text = "\n".join(run_res.pipeline_log)
            st.code(log_text, language="text")

            # Verified Code Delivery
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                st.markdown("#### Delivered Code Solution")
                st.code(run_res.verified_code, language="python")
            with col_c2:
                st.markdown("#### Deterministic Sandbox Verification")
                last_exec = run_res.tier2_result if run_res.escalated_to_tier2 else run_res.tier1_result
                if last_exec:
                    st.write(f"- Exit Code: `{last_exec.exit_code}`")
                    st.write(f"- Execution Time: `{last_exec.duration_ms:.1f}ms`")
                    st.write(f"- AST Syntax Valid: `{last_exec.syntax_valid}`")
                    st.write(f"- Sandboxed Test Result: `{'PASS' if last_exec.success else 'FAIL'}`")

    # =========================================================================
    # TAB 2: Single-Query Prompt Telemetry
    # =========================================================================
    with tab_telemetry:
        itms_data = build_itms_clean_escalation_prompt(
            task_description=current_task,
            raw_context_code=current_context,
            target_function_name=current_target,
            failing_test_case=current_failing_test,
            error_type=current_error_type,
            error_message=current_error_msg,
            strip_docstrings=True,
            prune_unreachable=prune_reachability,
            use_expanded_prefix=use_expanded
        )

        baseline_data = build_baseline_contaminated_prompt(
            task_description=current_task,
            raw_context_code=current_context,
            broken_code_draft=current_broken_draft,
            failing_test_case=current_failing_test,
            full_traceback=current_traceback
        )

        itms_metrics = TelemetryEngine.calculate_itms_metrics(
            prompt_data=itms_data,
            output_tokens=180,
            reasoning_tokens=420,
            pricing=selected_t2_pricing
        )

        baseline_metrics = TelemetryEngine.calculate_baseline_metrics(
            prompt_data=baseline_data,
            output_tokens=310,
            reasoning_tokens=2850,
            pricing=selected_t2_pricing
        )

        delta = TelemetryEngine.compare(itms_metrics, baseline_metrics)

        st.markdown(
            f"""
            <div class="kpi-grid">
                <div class="kpi-box kpi-box-green">
                    <div class="kpi-title">Cost Reduction</div>
                    <div class="kpi-number kpi-number-green">{delta.cost_reduction_percent:.1f}%</div>
                    <div class="kpi-detail">Saved per escalated query</div>
                </div>
                <div class="kpi-box kpi-box-cyan">
                    <div class="kpi-title">Total Tokens Saved</div>
                    <div class="kpi-number">{delta.total_tokens_saved:,}</div>
                    <div class="kpi-detail">{delta.token_reduction_percent:.1f}% total footprint cut</div>
                </div>
                <div class="kpi-box kpi-box-purple">
                    <div class="kpi-title">Thinking Tokens Avoided</div>
                    <div class="kpi-number">{delta.reasoning_tokens_saved:,}</div>
                    <div class="kpi-detail">Zero cognitive anchoring</div>
                </div>
                <div class="kpi-box kpi-box-amber">
                    <div class="kpi-title">Cache Hit Rate</div>
                    <div class="kpi-number">{itms_metrics.cache_hit_rate:.1f}%</div>
                    <div class="kpi-detail">Baseline: 0.0% (Cache miss)</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

        # Token Allocation Bar
        st.markdown(
            f"""
            <div class="bar-card">
                <div class="bar-header">
                    <span>[ITMS Clean Pipeline] Total: {itms_metrics.total_tokens:,} Tokens | Cost: ${itms_metrics.total_cost:.5f}</span>
                    <span style="color:#34D399;">Prefix Cache Applied ({itms_data['prefix_mode']})</span>
                </div>
                <div class="bar-track">
                    <div class="seg-cached" style="width:{(itms_metrics.cached_input_tokens / itms_metrics.total_tokens)*100:.1f}%;"></div>
                    <div class="seg-uncached" style="width:{(itms_metrics.uncached_input_tokens / itms_metrics.total_tokens)*100:.1f}%;"></div>
                    <div class="seg-output" style="width:{(itms_metrics.output_tokens / itms_metrics.total_tokens)*100:.1f}%;"></div>
                    <div class="seg-reasoning" style="width:{(itms_metrics.reasoning_tokens / itms_metrics.total_tokens)*100:.1f}%;"></div>
                </div>
                <div class="bar-header" style="margin-top:0.75rem;">
                    <span>[Baseline System] Total: {baseline_metrics.total_tokens:,} Tokens | Cost: ${baseline_metrics.total_cost:.5f}</span>
                    <span style="color:#FB7185;">Uncached Draft Inflation</span>
                </div>
                <div class="bar-track">
                    <div class="seg-uncached" style="width:{(baseline_metrics.uncached_input_tokens / baseline_metrics.total_tokens)*100:.1f}%;"></div>
                    <div class="seg-output" style="width:{(baseline_metrics.output_tokens / baseline_metrics.total_tokens)*100:.1f}%;"></div>
                    <div class="seg-ruminate" style="width:{(baseline_metrics.reasoning_tokens / baseline_metrics.total_tokens)*100:.1f}%;"></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

        # Side-by-Side Prompt Expanders
        st.subheader("Prompt Verification")
        exp_itms = st.expander("View ITMS Prompt (Clean Trace)", expanded=False)
        with exp_itms:
            st.text(f"Length: {len(itms_data['full_prompt'])} characters | Tokens: {itms_data['total_tokens']}")
            st.code(itms_data["full_prompt"], language="text")

        exp_base = st.expander("View Baseline Prompt (Contaminated)", expanded=False)
        with exp_base:
            st.text(f"Length: {len(baseline_data['full_prompt'])} characters | Tokens: {baseline_data['total_tokens']}")
            st.code(baseline_data["full_prompt"], language="text")

    # =========================================================================
    # TAB 3: Full Cascade Lifecycle Economics
    # =========================================================================
    with tab_cascade:
        st.subheader("Full Cascade Lifecycle Economics (Batch Simulation)")
        col_c_ctrl1, col_c_ctrl2 = st.columns(2)
        with col_c_ctrl1:
            batch_tasks = st.slider("Total Coding Tasks in Batch", min_value=100, max_value=10000, value=1000, step=100)
        with col_c_ctrl2:
            pass_rate = st.slider("Tier 1 First-Pass Pass Rate", min_value=0.40, max_value=0.90, value=0.72, step=0.01)

        lifecycle = TelemetryEngine.calculate_cascade_lifecycle_metrics(
            total_tasks=batch_tasks,
            tier1_pass_rate=pass_rate,
            tier1_profile=selected_t1_pricing,
            tier2_profile=selected_t2_pricing
        )

        st.markdown(
            f"""
            <div class="kpi-grid">
                <div class="kpi-box kpi-box-green">
                    <div class="kpi-title">ITMS Relay Total Cost</div>
                    <div class="kpi-number kpi-number-green">${lifecycle.itms_relay_cost:.2f}</div>
                    <div class="kpi-detail">For {batch_tasks:,} complete tasks</div>
                </div>
                <div class="kpi-box kpi-box-cyan">
                    <div class="kpi-title">Direct Frontier Cost</div>
                    <div class="kpi-number">${lifecycle.direct_frontier_cost:.2f}</div>
                    <div class="kpi-detail">Brute-force approach</div>
                </div>
                <div class="kpi-box kpi-box-purple">
                    <div class="kpi-title">Savings vs Direct Frontier</div>
                    <div class="kpi-number">{lifecycle.savings_vs_direct_frontier_pct:.1f}%</div>
                    <div class="kpi-detail">${lifecycle.dollar_savings_vs_frontier:.2f} saved</div>
                </div>
                <div class="kpi-box kpi-box-amber">
                    <div class="kpi-title">Savings vs Naive Agentic</div>
                    <div class="kpi-number">{lifecycle.savings_vs_naive_agentic_pct:.1f}%</div>
                    <div class="kpi-detail">${lifecycle.dollar_savings_vs_naive:.2f} saved</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    # =========================================================================
    # TAB 4: Model Catalog & Tier Analysis
    # =========================================================================
    with tab_models:
        st.subheader("Model Ecosystem & Two-Tiered Relay Analysis")
        col_t1, col_t2 = st.columns(2)

        with col_t1:
            st.markdown("#### Tier 1: Budget Workhorses (First-Pass Speculators)")
            for name, model in TIER_1_BUDGET_PROFILES.items():
                st.markdown(
                    f"""
                    <div class="model-card model-card-tier1">
                        <div class="model-name">{model.name}</div>
                        <div class="model-meta">Provider: {model.provider} | Latency: {model.latency_profile}</div>
                        <div class="model-cost">Input: ${model.price_input_uncached:.3f} / 1M | Cached: ${model.price_input_cached:.4f} / 1M | Output: ${model.price_output:.2f} / 1M</div>
                        <div class="model-desc">{model.suitability_notes}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        with col_t2:
            st.markdown("#### Tier 2: Frontier Reasoning Titans (Escalation Targets)")
            for name, model in TIER_2_FRONTIER_PROFILES.items():
                if name == "Frontier Standard (Spec Default)":
                    continue
                st.markdown(
                    f"""
                    <div class="model-card model-card-tier2">
                        <div class="model-name">{model.name}</div>
                        <div class="model-meta">Provider: {model.provider} | Latency: {model.latency_profile}</div>
                        <div class="model-cost">Input: ${model.price_input_uncached:.2f} / 1M | Cached: ${model.price_input_cached:.3f} / 1M | Output: ${model.price_output:.2f} / 1M</div>
                        <div class="model-desc">{model.suitability_notes}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

    # =========================================================================
    # TAB 5: Surgical Code Modification (Phase 2)
    # =========================================================================
    with tab_surgical:
        st.subheader("AST-Guided Surgical Splicer & Speculative Edit Cascade (Phase 2)")
        st.caption(
            "Solves Full-File Rewrite Inflation and Search/Replace Whitespace Fragility using AST node line slicing. "
            "The comparison below is a *simulated* three-way run on candidate code that you supply in the form: "
            "no provider request is made, so its token, cost and latency figures are labelled with their source."
        )

        col_edit_top1, col_edit_top2 = st.columns([2, 1])
        with col_edit_top1:
            edit_scenario = st.selectbox(
                "Surgical Modification Preset",
                options=[
                    "Update Tax Deduction Logic in AccountLedger (reconcile_balance)",
                    "Adjust Tax Surcharge Threshold in TransactionRecord (calculate_tax_allocation)",
                    "Custom Surgical Code Edit"
                ],
                index=0
            )
        with col_edit_top2:
            simulate_drift = st.checkbox(
                "Simulate Whitespace/Indentation Drift in Text Diff",
                value=True,
                help="Simulates the common real-world 15-25% failure mode where LLM outputs 4-space indent instead of 8-space indent in text search/replace."
            )

        if edit_scenario == "Update Tax Deduction Logic in AccountLedger (reconcile_balance)":
            edit_source = SAMPLE_CONTEXT_CODE
            edit_target_name = "AccountLedger.reconcile_balance"
            edit_task = "Update reconcile_balance to deduct tax allocations at rate 0.10 instead of 0.05, and round to 2 decimal places."
            edit_replacement = '''def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            # Modified tax rate: 0.10
            tax = tx.calculate_tax_allocation(0.10)
            total += (tx.amount - tax)
    return round(float(total), 2)'''
            
            edit_full_file = SAMPLE_CONTEXT_CODE.replace(
                "    def reconcile_balance(self, target_date: str) -> float:\n        pass",
                "    def reconcile_balance(self, target_date: str) -> float:\n        total = 0.0\n        for tx in self.transactions:\n            if tx.validate_checksum():\n                tax = tx.calculate_tax_allocation(0.10)\n                total += (tx.amount - tax)\n        return round(float(total), 2)"
            )
            edit_search = "    def reconcile_balance(self, target_date: str) -> float:\n        pass"
            edit_replace = "    def reconcile_balance(self, target_date: str) -> float:\n        total = 0.0\n        for tx in self.transactions:\n            if tx.validate_checksum():\n                tax = tx.calculate_tax_allocation(0.10)\n                total += (tx.amount - tax)\n        return round(float(total), 2)"
            edit_test = '''ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 100.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 900.0
'''
        elif edit_scenario == "Adjust Tax Surcharge Threshold in TransactionRecord (calculate_tax_allocation)":
            edit_source = SAMPLE_CONTEXT_CODE
            edit_target_name = "TransactionRecord.calculate_tax_allocation"
            edit_task = "Lower surcharge threshold from 1000 to 500 in calculate_tax_allocation."
            edit_replacement = '''def calculate_tax_allocation(self, rate: float) -> float:
    if self.amount <= 0:
        return 0.0
    base = self.amount * rate
    # Lowered threshold to 500
    surcharge = 0.03 * base if base > 500 else 0.0
    return round(base + surcharge, 2)'''
            
            edit_full_file = SAMPLE_CONTEXT_CODE.replace(
                "        surcharge = 0.02 * base if base > 1000 else 0.0",
                "        surcharge = 0.03 * base if base > 500 else 0.0"
            )
            edit_search = "        surcharge = 0.02 * base if base > 1000 else 0.0"
            edit_replace = "        surcharge = 0.03 * base if base > 500 else 0.0"
            edit_test = '''r = TransactionRecord('tx', 2000.0)
assert r.calculate_tax_allocation(0.5) == 1030.0
'''
        else:
            col_u1, col_u2 = st.columns(2)
            with col_u1:
                edit_source = st.text_area("Original Source File", value=SAMPLE_CONTEXT_CODE, height=180)
                edit_target_name = st.text_input("Target Node to Edit", value="AccountLedger.reconcile_balance")
                edit_task = st.text_input("Modification Instruction", value="Apply 10% tax deduction")
            with col_u2:
                edit_replacement = st.text_area("Surgical Replacement Node Code", value='''def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            tax = tx.calculate_tax_allocation(0.10)
            total += (tx.amount - tax)
    return round(float(total), 2)''', height=130)
                edit_full_file = edit_source
                edit_search = "    def reconcile_balance(self, target_date: str) -> float:\n        pass"
                edit_replace = edit_replacement
                edit_test = st.text_area("Test Assertions", value="assert True", height=70)

        col_btn, _ = st.columns([1, 4])
        run_edit_btn = col_btn.button("Run Comparative Edit Benchmark", type="primary")

        if run_edit_btn:
            edit_orch = SurgicalEditOrchestrator()
            with st.spinner("Executing tri-way edit benchmark (Full File vs Text Diff vs ITMS Splicer)..."):
                report = edit_orch.run_benchmark(
                    original_source=edit_source,
                    target_name=edit_target_name,
                    edit_instruction=edit_task,
                    replacement_node_code=edit_replacement,
                    simulated_full_file_code=edit_full_file,
                    simulated_search_block=edit_search,
                    simulated_replace_block=edit_replace,
                    test_suite_code=edit_test,
                    tier1_model_name=tier1_choice,
                    tier2_model_name=tier2_choice,
                    simulate_diff_whitespace_error=simulate_drift
                )

            # Impact KPI Ribbon
            st.markdown(
                f"""
                <div class="kpi-grid">
                    <div class="kpi-box kpi-box-green">
                        <div class="kpi-title">Estimated Output Tokens Saved</div>
                        <div class="kpi-number kpi-number-green">{report.output_tokens_saved_vs_full_pct:.1f}%</div>
                        <div class="kpi-detail">{report.itms_surgical_result.output_tokens} vs {report.full_file_result.output_tokens} (local tokenizer estimate)</div>
                    </div>
                    <div class="kpi-box kpi-box-cyan">
                        <div class="kpi-title">Estimated Cost vs Full-File</div>
                        <div class="kpi-number">{report.cost_saved_vs_full_pct:.1f}%</div>
                        <div class="kpi-detail">${report.itms_surgical_result.cost_usd:.5f} vs ${report.full_file_result.cost_usd:.5f} (estimate x catalogue price)</div>
                    </div>
                    <div class="kpi-box kpi-box-purple">
                        <div class="kpi-title">Edit Application Time</div>
                        <div class="kpi-number">{report.itms_surgical_result.latency_ms:.2f} ms</div>
                        <div class="kpi-detail">Measured local processing only - no provider request was made</div>
                    </div>
                    <div class="kpi-box kpi-box-amber">
                        <div class="kpi-title">Splicing Status</div>
                        <div class="kpi-number">{report.itms_surgical_result.status.upper()}</div>
                        <div class="kpi-detail">Search/Replace: {report.search_replace_result.status} | Full-File: {report.full_file_result.status}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

            st.warning(
                "Simulated comparison, not a measured benchmark run. The candidate code for all "
                "three strategies was supplied in the form above, so no provider request was made. "
                f"Token counts are {report.token_source} values produced by the local tokenizer; the "
                "cost figures are those estimates multiplied by the catalogue price; and the latency "
                "is the measured wall-clock time of the local edit mechanism only. A latency "
                "'speedup' is therefore not reported, and no claim is made about which method is "
                "faster, cheaper or more accurate than another outside of this simulated run."
            )

            # 3-Way Comparative Matrix
            st.markdown("#### Tri-Way Strategy Comparison Matrix")
            st.markdown(
                f"""
                | Strategy | Status | Evaluated Attempts | Output Tokens (est.) | Total Cost (est., $) | Local Edit Time (ms) | Notes |
                | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
                | **Full-File Regeneration (Baseline)** | `{report.full_file_result.status}` | {report.full_file_result.passed_attempts}/{report.full_file_result.attempts} passed | **{report.full_file_result.output_tokens}** | **${report.full_file_result.cost_usd:.5f}** | **{report.full_file_result.latency_ms:.2f} ms** | token source: {report.full_file_result.token_source} |
                | **Text-Based Search/Replace (Aider Style)** | `{report.search_replace_result.status}` | {report.search_replace_result.passed_attempts}/{report.search_replace_result.attempts} passed | **{report.search_replace_result.output_tokens}** | **${report.search_replace_result.cost_usd:.5f}** | **{report.search_replace_result.latency_ms:.2f} ms** | {report.search_replace_result.splicing_error or 'exact text match applied'} |
                | **ITMS AST-Guided Surgical Splicer (Our Work)** | `{report.itms_surgical_result.status}` | {report.itms_surgical_result.passed_attempts}/{report.itms_surgical_result.attempts} passed | **{report.itms_surgical_result.output_tokens}** | **${report.itms_surgical_result.cost_usd:.5f}** | **{report.itms_surgical_result.latency_ms:.2f} ms** | token source: {report.itms_surgical_result.token_source}; cached tokens: {report.itms_surgical_result.cached_input_tokens if report.itms_surgical_result.cached_input_tokens is not None else 'unavailable'} |
                """
            )

            if not report.search_replace_result.success:
                st.warning(f"Text-Based Diff Failure Caught: {report.search_replace_result.splicing_error}")

            # Code inspection panels
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                st.markdown("#### Located Target AST Node")
                target_loc = ASTNodeLocator.locate_target(edit_source, edit_target_name)
                if target_loc:
                    st.write(f"- Qualified Name: `{target_loc.qualified_name}`")
                    st.write(f"- Line Range: `Lines {target_loc.start_line} – {target_loc.end_line}`")
                    st.write(f"- Base Indentation: `'{target_loc.base_indentation}'` ({len(target_loc.base_indentation)} spaces)")
                    st.code(target_loc.original_text, language="python")
            with col_c2:
                st.markdown("#### Surgically Spliced Output File")
                st.caption(
                    "The splice replaces only the target's line range; comments, formatting and "
                    "unrelated definitions outside that range are preserved byte-for-byte. "
                    "This is a property of the line-boundary splice, not a success guarantee for "
                    "the edit itself - the edit is graded by the evaluator above."
                )
                st.code(report.itms_surgical_result.resulting_code, language="python")

    # =========================================================================
    # TAB 6: Macro Benchmark Suite (5-Task)
    # =========================================================================
    with tab_benchmark:
        st.subheader("Canonical Macro Benchmark Evaluation Suite")
        st.caption("Executes 5 standardized algorithmic and software engineering tasks across diverse domains.")

        col_bm1, col_bm2 = st.columns([3, 1])
        with col_bm1:
            st.markdown(
                """
                This automated empirical evaluation suite runs 5 canonical tasks through the full ITMS pipeline:
                1. **BM-01**: Account Ledger Reconciliation (Financial / Rounding)
                2. **BM-02**: Data Pipeline & SHA-256 Validator (Cryptographic Integrity)
                3. **BM-03**: LRU Eviction Policy (Algorithmic / Capacity Bounds)
                4. **BM-04**: Token Bucket Rate Limiter (Distributed / Concurrency)
                5. **BM-05**: Intra-Tier Syntax Self-Repair (Self-Healing Synthesis)
                """
            )
        with col_bm2:
            run_macro = st.button("Run 5-Task Benchmark Suite", type="primary", use_container_width=True)

        if run_macro:
            with st.spinner("Executing 5-Task Macro Benchmark Suite..."):
                macro_rep = MacroBenchmarkSuite.run_suite(
                    tier1_model=tier1_choice,
                    tier2_model=tier2_choice,
                    force_mock=force_mock
                )

            st.markdown(
                f"""
                <div class="kpi-grid">
                    <div class="kpi-box kpi-box-green">
                        <div class="kpi-title">Pass Rate</div>
                        <div class="kpi-number kpi-number-green">{macro_rep.pass_rate_pct:.0f}%</div>
                        <div class="kpi-detail">{macro_rep.passed_tasks} of {macro_rep.total_tasks} tasks verified</div>
                    </div>
                    <div class="kpi-box kpi-box-cyan">
                        <div class="kpi-title">Savings vs Direct Frontier</div>
                        <div class="kpi-number">{macro_rep.cost_savings_vs_frontier_pct:.1f}%</div>
                        <div class="kpi-detail">Total: ${macro_rep.total_itms_cost_usd:.5f}</div>
                    </div>
                    <div class="kpi-box kpi-box-purple">
                        <div class="kpi-title">Savings vs Naive Agentic</div>
                        <div class="kpi-number">{macro_rep.cost_savings_vs_naive_pct:.1f}%</div>
                        <div class="kpi-detail">Contamination avoided</div>
                    </div>
                    <div class="kpi-box kpi-box-amber">
                        <div class="kpi-title">Cascade Distribution</div>
                        <div class="kpi-number">{macro_rep.tier1_first_pass_count} / {macro_rep.tier1_self_repair_count} / {macro_rep.tier2_escalation_count}</div>
                        <div class="kpi-detail">Direct T1 / Self-Repair / Tier 2</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

            st.caption(
                ("**MOCK RUN - not a live measurement.** " if macro_rep.contains_mock_results else "")
                + f"Mock results: {macro_rep.contains_mock_results} | "
                + f"Live request failures: {macro_rep.live_request_failures} | "
                + "Costs are modelled from the catalogue price profile and the token counts "
                "actually reported by the provider; in mock mode no provider was called and no "
                "billed spend occurred."
            )

            st.markdown("#### Task Execution Telemetry")
            table_rows = []
            for r in macro_rep.task_results:
                status_str = "PASSED" if r["success"] else "FAILED"
                path_str = f"Tier 1 Self-Repair ({r['tier1_repairs']})" if r["tier1_repairs"] > 0 else ("Tier 2 Escalation" if r["escalated"] else "Tier 1 Direct Pass")
                table_rows.append(
                    f"| `{r['task_id']}` | **{r['name']}** | {r['category']} | `{status_str}` | {path_str} | **${r['itms_cost']:.5f}** | **{r['savings_vs_frontier_pct']:.1f}%** |"
                )

            st.markdown(
                """
                | Task ID | Benchmark Name | Category | Status | Execution Path | Pipeline Cost ($) | Savings vs Frontier |
                | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
                """ + "\n".join(table_rows)
            )

    # =========================================================================
    # TAB 1: Completed Benchmark Results (read-only viewer)
    # =========================================================================
    with tab_completed:
        _render_completed_benchmark_results(run=completed_run)

    # =========================================================================
    # TAB: Comparative Benchmark Results (ITMS vs Aider vs Cline, read-only)
    # =========================================================================
    with tab_comparative:
        _render_comparative_benchmark_results()


def _render_comparative_benchmark_results():
    """
    Render the real ITMS-vs-Aider-vs-Cline comparative benchmark.

    Strictly read-only: it reads the recorded Phase 2 dataset and the
    Phase 3 analysis artefacts. It never launches a benchmark, never
    contacts a model provider and never writes a file.
    """
    run = load_comparative_run()
    records = run.get("records") or []
    metadata = run.get("metadata") or {}
    analysis = run.get("analysis") or {}

    st.subheader("ITMS vs Aider vs Cline — Comparative Benchmark Results")
    st.caption(
        "Real three-system comparison: the ITMS agent, the `aider` CLI and the "
        "`cline` CLI over the same 20 HumanEval-derived repair tasks. "
        "Read-only viewer — it never launches a benchmark and never contacts "
        "a model provider."
    )

    if not records:
        st.warning(
            "No comparative benchmark dataset is available yet. "
            f"{run.get('errors') or ['No live run directory was found.']}",
            icon="⚠️",
        )
        return

    metrics = compute_comparative_metrics(run)
    systems = [s for s in SYSTEM_ORDER if s in metrics["systems"]]

    # ---------------- dataset identity ----------------
    st.markdown("#### Dataset used")
    identity_rows = [
        ("Run directory", f"`{run['run_dir']}`"),
        ("Run id", f"`{metadata.get('run_id', 'n/a')}`"),
        ("Model", f"`{metadata.get('requested_model_id', 'n/a')}`"),
        ("Provider", f"`{metadata.get('declared_provider', 'n/a')}`"),
        ("Temperature", str(metadata.get("temperature", "n/a"))),
        ("Repetitions per cell", str(metadata.get("repetitions", "n/a"))),
        ("Seed policy", f"`{metadata.get('seed_policy', 'n/a')}`"),
        ("Spending limit (USD)", str(metadata.get("max_spend_usd", "n/a"))),
        ("Analysis directory", f"`{run['analysis_dir']}`"),
    ]
    st.dataframe(
        [{"Field": k, "Value": v} for k, v in identity_rows],
        hide_index=True,
        width="stretch",
    )

    # ---------------- integrity ----------------
    st.markdown("#### Experiment integrity and completion status")
    integrity_cols = st.columns(4)
    recorded = metrics["recorded_attempts"]
    planned = metrics.get("planned_attempts") or 0
    integrity_cols[0].metric("Planned attempts", planned)
    integrity_cols[1].metric("Recorded attempts", recorded)
    integrity_cols[2].metric(
        "Matrix complete", "yes" if recorded == planned and planned else "no"
    )
    integrity_cols[3].metric(
        "Mock records", sum(1 for r in records if r.get("is_mock"))
    )

    if run.get("integrity_report_exists"):
        st.success(
            "Integrity report present: "
            f"`{os.path.relpath(run['integrity_report_path'], PROJECT_ROOT)}`",
            icon="✅",
        )
    else:
        st.warning(
            "No `BENCHMARK_INTEGRITY_REPORT.md` found in the run directory.",
            icon="⚠️",
        )

    missing_cells = _missing_cells(metrics, metadata)
    if missing_cells:
        st.warning(
            f"**{len(missing_cells)} planned cells have no recorded attempt.** "
            "They are excluded from every numerator and are counted as "
            "incomplete in the completion rate.",
            icon="⚠️",
        )

    # ---------------- system metrics ----------------
    st.markdown("#### System-level metrics")
    table_rows = []
    for system in systems:
        m = metrics["systems"][system]
        table_rows.append({
            "System": m["label"],
            "Planned": m["planned"],
            "Recorded": m["recorded"],
            "Completed": m["completed"],
            "Evaluated": m["evaluated"],
            "Passed": m["passed"],
            "Completion (of planned)": _fmt_pct(m["completion_rate"]),
            "Pass rate (of evaluated)": _fmt_pct(m["pass_rate"]),
            "Patch applied (of recorded)": _fmt_pct(m["patch_rate"]),
            "Seed supported": "yes" if m["seed_supported"] else "no",
            "Seed applied": "yes" if m["seed_applied_any"] else "no",
        })
    st.dataframe(table_rows, hide_index=True, width="stretch")
    st.caption(
        "**Completion rate** denominator = all planned attempts. "
        "**Pass rate** denominator = evaluated attempts only; unevaluated "
        "attempts are never counted as passes and are counted as failures "
        "in the completion rate."
    )

    # ---------------- figures ----------------
    st.markdown("#### Figures")
    graphs = figure_paths(run["analysis_dir"])
    if not graphs:
        st.info(
            "No figures found. Run "
            "`python -m experiments.itms_aider_cline_v1.analyze --run-dir <run>`.",
            icon="ℹ️",
        )
    else:
        for name, path in graphs:
            data = read_comparative_graph_bytes(path)
            if not data:
                st.error(f"could not read figure `{name}`", icon="⚠️")
                continue
            st.image(data, caption=name, width="stretch")

    # ---------------- per-task comparison ----------------
    st.markdown("#### Per-task results")
    task_matrix = analysis.get("task_matrix") or {}
    if task_matrix:
        rows = []
        for task in metrics["tasks"]:
            row = {"Task": task}
            for system in systems:
                cell = task_matrix.get(task, {}).get(system, {})
                evaluated = cell.get("evaluated", 0)
                passed = cell.get("passed", 0)
                row[SYSTEM_LABELS.get(system, system)] = (
                    f"{passed}/{evaluated}"
                    + (f" ({_fmt_pct(cell.get('pass_rate'))})" if evaluated else " n/a")
                )
            rows.append(row)
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("Cells show passed/evaluated attempts (and the pass rate).")
    else:
        st.info("Per-task breakdown is available in the analysis JSON only.", icon="ℹ️")

    # ---------------- failure analysis ----------------
    st.markdown("#### Failure analysis")
    failure_rows = []
    for system in systems:
        m = metrics["systems"][system]
        for category, count in sorted(m["failure_categories"].items()):
            if category == "none":
                continue
            failure_rows.append({
                "System": SYSTEM_LABELS.get(system, system),
                "Failure category": category,
                "Attempts": count,
            })
    if failure_rows:
        st.dataframe(failure_rows, hide_index=True, width="stretch")
    else:
        st.success("No failures were recorded in this dataset.", icon="✅")

    # ---------------- limitations ----------------
    st.markdown("#### Limitations, telemetry provenance and scope")
    st.markdown(
        f"""
- **Token provenance.** ITMS token counts are *provider-reported*; Aider and
  Cline counts are *tool-reported* summaries. They are not pooled into a
  single cross-system total.
- **Unavailable telemetry.** A token or cost figure is shown only where the
  tool actually reported one; missing values are never imputed with zero.
  Attempts with no reported usage in this dataset: **{sum(1 for r in records if r.get('usage_source') == 'missing')}**.
- **Seed control.** {_seed_note(metrics, systems)}
- **Cline has no temperature control**, so its sampling is at the provider default.
- **Latency** is whole-system wall clock (including each tool's own planning
  and tool-call overhead), not pure model latency.
- **Context strategy differs by design** (ITMS skeletonises, Aider builds a
  repo map, Cline plans with its own tools), so token counts are a property of
  the system rather than of the task.
- **These are not HumanEval scores.** The tasks are synthetic single-mutation
  repair tasks derived from pinned HumanEval problems.
- **Task difficulty was assigned a priori** by a static rubric; no observed
  model output influenced task selection.
- The earlier **120-attempt ITMS run** (prompt/edit-format variants of one
  model) is a different experiment and is deliberately kept separate.
"""
    )

    if run.get("errors"):
        st.markdown("#### Data loading notes")
        for error in run["errors"]:
            st.caption(f"• {error}")


def _fmt_pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * value:.1f}%"


def _missing_cells(metrics, metadata) -> List[str]:
    tasks = metadata.get("tasks") or []
    systems = metadata.get("systems") or []
    reps = int(metadata.get("repetitions") or 0)
    present = metrics.get("present_cells") or set()
    missing = []
    for task in tasks:
        for system in systems:
            for rep in range(1, reps + 1):
                if (task, system, rep) not in present:
                    missing.append(f"{task}::{system}::rep{rep:02d}")
    return missing


def _seed_note(metrics, systems) -> str:
    lines = []
    for system in systems:
        m = metrics["systems"][system]
        if m["seed_supported"] and m["seed_applied_any"]:
            lines.append(f"{m['label']}: a request-level seed was sent")
        else:
            lines.append(
                f"{m['label']}: no request-level seed is available, so its "
                "repetitions are identified by repetition id only"
            )
    return "; ".join(lines) + ". No claim of independent seed control is made " \
        "for a system that cannot accept one."


def _render_completed_benchmark_banner(run):
    """
    Render the completed benchmark as a prominent summary at the top of the
    page, so it is visible on every tab without any navigation.

    Read-only: it only uses the already-loaded artefacts of the finished run.
    """
    records = run.get("records") or []
    if not records:
        st.warning(
            "The completed benchmark results could not be loaded from "
            f"`{run.get('run_dir')}`. The full report is in the "
            "'Completed Benchmark Results' tab.",
            icon="⚠️",
        )
        return

    overall = compute_overall(records)
    scheduled = overall["total_scheduled"]
    successful = overall["successful"]
    evaluated = overall["reached_evaluation"]
    passed = overall["evaluator_passed"]

    completion_rate = (
        f"{100.0 * overall['completion_rate']:.1f}% ({successful}/{scheduled})"
        if overall["completion_rate"] is not None
        else "n/a"
    )
    pass_rate = (
        f"{100.0 * overall['evaluator_pass_rate']:.1f}% ({passed}/{evaluated})"
        if overall["evaluator_pass_rate"] is not None
        else "n/a"
    )

    st.markdown("#### Completed 120-Attempt ITMS Benchmark (live run, read-only)")
    kpi_columns = st.columns(6)
    kpi_items = [
        ("Scheduled attempts", scheduled),
        ("Successful completions", successful),
        ("Output failures", overall["output_failures"]),
        ("Completion rate", completion_rate),
        ("Evaluated attempts", evaluated),
        ("Evaluator pass rate", pass_rate),
    ]
    for column, (label, value) in zip(kpi_columns, kpi_items):
        with column:
            st.metric(label, value)

    st.caption(
        f"**Completion rate** = successful completions / **all scheduled attempts** "
        f"({successful}/{scheduled}). **Evaluator pass rate** = candidates that "
        f"passed the official evaluator / attempts that **reached evaluation** "
        f"({passed}/{evaluated}); the {overall['output_failures']} output failures "
        f"never reached the evaluator, so they are excluded from that denominator "
        f"and counted as failures in the completion rate. Every candidate that "
        f"reached the evaluator passed, so these results do **not** show that ITMS "
        f"outperforms the baselines. Full details and figures: the "
        f"'Completed Benchmark Results' tab."
    )


def _render_completed_benchmark_results(
    run=None,
    run_dir: str = DEFAULT_RUN_DIR,
    analysis_dir: str = DEFAULT_ANALYSIS_DIR,
):
    """
    Render the already-completed benchmark run.

    This is a pure viewer: it reads the artefacts of a finished run and the
    figures of the offline analysis. It never runs a benchmark, never contacts
    a model provider and never writes anything.
    """
    st.subheader("Completed Benchmark Results (read-only)")
    st.caption(
        f"Loads the artefacts of the completed run at "
        f"`{os.path.relpath(run_dir, PROJECT_ROOT)}`. "
        "This tab only reads files: it never re-runs the benchmark and never "
        "contacts a model provider."
    )

    if run is None:
        run = load_run(run_dir, analysis_dir)
    records = run["records"]

    if run["problems"]:
        st.warning(
            "Some result files could not be loaded, so the figures below may be "
            "incomplete:",
            icon="⚠️",
        )
        for problem in run["problems"]:
            st.warning(problem, icon="⚠️")

    if not records:
        st.error(
            "No attempt records were found, so no benchmark statistics can be "
            f"displayed. Expected `attempts.jsonl` in `{run['run_dir']}`."
        )
        return

    overall = compute_overall(records)
    per_method = compute_method_metrics(records)

    # -- 1. Overall experiment summary -----------------------------------
    st.markdown("#### 1. Overall experiment summary")

    kpi_columns = st.columns(6)
    kpi_items = [
        ("Scheduled attempts", overall["total_scheduled"]),
        ("Successful completions", overall["successful"]),
        ("Output failures", overall["output_failures"]),
        (
            "Completion rate",
            f"{100.0 * overall['completion_rate']:.1f}%"
            f" ({overall['successful']}/{overall['total_scheduled']})"
            if overall["completion_rate"] is not None
            else "n/a",
        ),
        ("Evaluated attempts", overall["reached_evaluation"]),
        (
            "Evaluator pass rate",
            f"{100.0 * overall['evaluator_pass_rate']:.1f}%"
            f" ({overall['evaluator_passed']}/{overall['reached_evaluation']})"
            if overall["evaluator_pass_rate"] is not None
            else "n/a",
        ),
    ]
    for column, (label, value) in zip(kpi_columns, kpi_items):
        with column:
            st.metric(label, value)

    st.markdown(
        f"""
        - **Tasks:** {overall['task_count']} synthetic HumanEval-derived repair
          tasks, crossed with {overall['method_count']} editing methods and
          {max(1, overall['total_scheduled'] // max(1, overall['task_count'] * overall['method_count']))}
          repetition(s) per task/method combination.
        - **Requested model id:** `{', '.join(overall['requested_model_ids']) or 'n/a'}`
        - **Served model id(s) observed:** `{', '.join(overall['served_model_ids']) or 'none recorded'}`
        - **Mock attempts:** {overall['mock_attempts']} (this run is a live measurement)
        """
    )

    if overall["output_failures"]:
        categories = ", ".join(
            f"`{k}` ({v})" for k, v in sorted(overall["failure_categories"].items())
        )
        st.info(
            f"**An output failure is not automatically an evaluator failure.** "
            f"{overall['output_failures']} attempt(s) failed *before* the official "
            f"evaluator could run, because the model's output was truncated or "
            f"could not be parsed ({categories}). They are excluded from the "
            f"evaluator pass rate and counted as failures in the completion rate. "
            f"The evaluator itself rejected {overall['evaluator_failed']} of the "
            f"{overall['reached_evaluation']} attempts that reached it."
        )

    for warning in run["warnings"]:
        st.caption(f"ℹ️ {warning}")

    # -- 2. Method comparison table ---------------------------------------
    st.markdown("#### 2. Method comparison")

    header = (
        "| Method | Scheduled | Successful | Output failures | Completion rate "
        "(successful/scheduled) | Reached evaluation | Evaluator pass rate "
        "(of evaluated) | Mean execution time | Mean token usage |\n"
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    body = []
    for method, m in per_method.items():
        completion = (
            f"{100.0 * m['completion_rate']:.1f}% "
            f"({m['successful']}/{m['scheduled']})"
            if m["completion_rate"] is not None
            else "n/a"
        )
        pass_rate = (
            f"{100.0 * m['evaluator_pass_rate']:.1f}%"
            f" ({m['evaluator_passed']}/{m['reached_evaluation']})"
            if m["evaluator_pass_rate"] is not None
            else "n/a"
        )
        latency = (
            f"{m['mean_latency_ms']:,.0f} ms (n={m['latency_samples']})"
            if m["mean_latency_ms"] is not None
            else "n/a"
        )
        tokens = (
            f"{m['mean_total_tokens']:,.0f} (n={m['token_samples']})"
            if m["mean_total_tokens"] is not None
            else "n/a"
        )
        body.append(
            f"| `{method}` | {m['scheduled']} | {m['successful']} | "
            f"{m['output_failures']} | {completion} | {m['reached_evaluation']} | "
            f"{pass_rate} | {latency} | {tokens} |"
        )
    st.markdown(header + "\n" + "\n".join(body))

    st.caption(
        "**Denominators:** *Completion rate* = successful completions divided by "
        "**all scheduled attempts** for that method. *Evaluator pass rate* = "
        "attempts that passed the official evaluator divided by the attempts "
        "that **reached evaluation** for that method. Attempts that failed "
        "before evaluation (truncated or unparsable output) count as failures in "
        "the completion rate and are absent from the evaluator denominator. "
        "*Mean execution time* uses every attempt with a recorded latency; "
        "*mean token usage* uses provider-reported totals only."
    )

    # -- 3. Existing graphs ----------------------------------------------
    st.markdown("#### 3. Figures from the offline analysis")

    graph_captions = {
        "fig1_pass_rate_by_method.png": (
            "Official evaluator pass rate by method "
            "(denominator: attempts that reached evaluation)"
        ),
        "fig1b_completion_rate_by_method.png": (
            "Attempt completion rate by method "
            "(denominator: all scheduled attempts)"
        ),
        "fig2_success_vs_failure_by_method.png": (
            "Successful versus failed attempts by method"
        ),
        "fig3_mean_execution_time_by_method.png": (
            "Mean execution time per attempt by method"
        ),
        "fig4_mean_token_usage_by_method.png": (
            "Mean token usage per attempt by method (provider-reported totals)"
        ),
        "fig5_task_by_method_heatmap.png": (
            "Task-by-method evaluator pass rate heatmap "
            "(denominator: evaluated attempts per cell)"
        ),
    }
    for filename in GRAPH_FILENAMES:
        data = read_graph_bytes(run["analysis_dir"], filename)
        if data is None:
            st.warning(
                f"Figure `{filename}` was not found in `{run['analysis_dir']}` "
                "and cannot be displayed.",
                icon="⚠️",
            )
            continue
        st.image(
            data,
            width="stretch",
        )
        st.caption(graph_captions.get(filename, filename))

    # -- 4. Limitations and interpretation --------------------------------
    st.markdown("#### 4. Research limitations and interpretation")

    st.warning(
        """
**How to read these numbers**

- **Evaluator pass rate** is calculated **only from attempts that reached
  evaluation** (the official evaluator actually ran). Attempts whose output was
  truncated or unparsable never reached it.
- **Completion rate** is calculated from **all scheduled attempts**, so those
  same output failures count against it.
- **Every candidate that reached the evaluator passed in this run.** The
  evaluator pass rate is therefore 100% for all three methods and, on its own,
  **does not demonstrate that ITMS is better than the baselines** — it only
  shows that the harness works end to end on a pinned model.
- **Six attempts had truncated or unparsable output.** They failed before
  evaluation, so they reduce the completion rate while leaving the evaluator
  pass rate unchanged. The visible difference between methods in this run is
  therefore a *completion* difference (largely an output-token-budget effect),
  not a demonstrated difference in repair quality.
- **The task-by-method heatmap shows the pass rate among evaluated attempts.**
  A cell can read **100%** even when one of its scheduled attempts failed
  before evaluation; only a cell whose attempts *all* failed before evaluation
  is marked "n/e".
- **This is a 120-attempt pilot** over 20 synthetic HumanEval-derived repair
  tasks with two repetitions per method/task combination, on a single run
  order and a single seed. It must **not** be presented as conclusive evidence
  of general superiority of any editing method.
""",
        icon="⚠️",
    )


if __name__ == "__main__":
    main()
