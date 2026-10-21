import unittest
import re
import time
from skeletonizer import skeletonize_code
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
    TelemetryMetrics,
    ComparisonDelta,
    CascadeLifecycleMetrics
)
import app


class TestSkeletonizer(unittest.TestCase):
    def test_spec_acceptance_criteria(self):
        """Verifies the exact test prescribed in the specification."""
        test_code = """
def helper(x):
    # This logic should disappear
    y = x * 2
    return y + 1

def target(a):
    return helper(a)
"""
        result = skeletonize_code(test_code, "target")
        self.assertIn("...", result, "Verification Failed: Ellipsis not inserted into helper function.")
        self.assertNotIn("y = x * 2", result, "Verification Failed: Internal logic was not stripped.")
        self.assertIn("def target(a):", result, "Verification Failed: Target function was altered.")

    def test_qualified_target_name(self):
        """Verifies that qualified target names like Class.method are properly matched."""
        code = """
class MyService:
    def internal_logic(self):
        return 42
    def process_data(self, val: int) -> int:
        return val * 10
"""
        result = skeletonize_code(code, "MyService.process_data")
        self.assertIn("def process_data(self, val: int) -> int:", result)
        self.assertIn("return val * 10", result)
        self.assertNotIn("return 42", result)
        self.assertIn("...", result)

    def test_async_functions(self):
        """Verifies that async helper functions are pruned properly."""
        code = """
async def fetch_remote():
    await asyncio.sleep(1)
    return "done"

async def target_coro():
    return await fetch_remote()
"""
        result = skeletonize_code(code, "target_coro")
        self.assertIn("async def target_coro():", result)
        self.assertIn("return await fetch_remote()", result)
        self.assertNotIn("await asyncio.sleep(1)", result)
        self.assertIn("...", result)

    def test_docstring_stripping(self):
        """Verifies that class and module docstrings are stripped to save tokens."""
        doc_code = '"""Module documentation."""\nclass Service:\n    """Class documentation."""\n    def do(self):\n        return 1\n'
        result = skeletonize_code(doc_code, "target", strip_docstrings=True)
        self.assertNotIn("Module documentation.", result)
        self.assertNotIn("Class documentation.", result)

    def test_reachability_pruning(self):
        """Verifies that unreferenced classes are stripped when prune_unreachable=True."""
        code = """
class UnrelatedAnalytics:
    def record(self):
        pass

class TargetDomain:
    def target(self):
        return 1
"""
        res_kept = skeletonize_code(code, "target", prune_unreachable=False)
        self.assertIn("UnrelatedAnalytics", res_kept)

        res_pruned = skeletonize_code(code, "target", prune_unreachable=True)
        self.assertNotIn("UnrelatedAnalytics", res_pruned)
        self.assertIn("TargetDomain", res_pruned)

    def test_invalid_syntax_fallback(self):
        """Verifies that invalid syntax does not raise unhandled exceptions."""
        broken_syntax = "def broken(x: return 123"
        result = skeletonize_code(broken_syntax, "target")
        self.assertEqual(result, broken_syntax)


class TestPromptManager(unittest.TestCase):
    def test_itms_escalation_clean_trace(self):
        """Verifies ITMS prompt contains no broken draft, starts with static prefix, and includes isolated trace."""
        itms = build_itms_clean_escalation_prompt(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            failing_test_case=app.SAMPLE_FAILING_TEST,
            error_type=app.SAMPLE_ERROR_TYPE,
            error_message=app.SAMPLE_ERROR_MSG
        )
        self.assertTrue(itms["full_prompt"].startswith(GLOBAL_STATIC_PREFIX))
        self.assertNotIn(app.SAMPLE_BROKEN_DRAFT, itms["full_prompt"])
        self.assertIn(app.SAMPLE_FAILING_TEST, itms["full_prompt"])
        self.assertIn(app.SAMPLE_ERROR_TYPE, itms["full_prompt"])
        self.assertIn(app.SAMPLE_ERROR_MSG, itms["full_prompt"])
        self.assertGreater(itms["static_token_count"], 0)
        self.assertGreater(itms["lines_pruned"], 0)

    def test_expanded_prefix_threshold(self):
        """Verifies expanded prefix exceeds the 1024 token caching threshold."""
        tokens = get_token_count(EXPANDED_CACHED_PREFIX_1024)
        self.assertGreaterEqual(tokens, 1024)

    def test_traceback_sanitization(self):
        """Verifies multi-line stack traces are sanitized into minimal diagnostics."""
        noisy_trace = """Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/pytest.py", line 284
  File "test_runner.py", line 102
    assert ledger.reconcile_balance() == 950.0
AssertionError: assert 0.0 == 950.0"""
        diag = sanitize_runtime_traceback(noisy_trace)
        self.assertEqual(diag["error_type"], "AssertionError")
        self.assertIn("950.0", diag["error_message"])
        self.assertIn("assert ledger.reconcile_balance() == 950.0", diag["failing_assertion"])

    def test_baseline_contaminated_prompt(self):
        """Verifies Baseline prompt contains broken draft and full traceback."""
        base = build_baseline_contaminated_prompt(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            broken_code_draft=app.SAMPLE_BROKEN_DRAFT,
            failing_test_case=app.SAMPLE_FAILING_TEST,
            full_traceback=app.SAMPLE_TRACEBACK
        )
        self.assertIn(app.SAMPLE_BROKEN_DRAFT, base["full_prompt"])
        self.assertIn(app.SAMPLE_TRACEBACK, base["full_prompt"])
        self.assertIn(app.SAMPLE_FAILING_TEST, base["full_prompt"])
        self.assertGreater(base["draft_line_count"], 0)

    def test_tiktoken_caching_performance(self):
        """Verifies that repeated token counting executes in sub-millisecond time via lru_cache."""
        t0 = time.perf_counter()
        for _ in range(100):
            _ = get_token_count("Token calculation performance benchmarking string.")
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.05, f"Token counting took too long ({elapsed:.3f}s for 100 calls), caching issue.")


class TestTelemetryEngine(unittest.TestCase):
    def setUp(self):
        self.itms_prompt = build_itms_clean_escalation_prompt(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            failing_test_case=app.SAMPLE_FAILING_TEST,
            error_type=app.SAMPLE_ERROR_TYPE,
            error_message=app.SAMPLE_ERROR_MSG
        )
        self.base_prompt = build_baseline_contaminated_prompt(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            broken_code_draft=app.SAMPLE_BROKEN_DRAFT,
            failing_test_case=app.SAMPLE_FAILING_TEST,
            full_traceback=app.SAMPLE_TRACEBACK
        )

    def test_standard_frontier_calculations(self):
        """Verifies standard pricing calculations and KV cache hit rate."""
        itms_metrics = TelemetryEngine.calculate_itms_metrics(self.itms_prompt, 180, 420)
        base_metrics = TelemetryEngine.calculate_baseline_metrics(self.base_prompt, 310, 2850)

        self.assertGreater(itms_metrics.cache_hit_rate, 0.0)
        self.assertEqual(base_metrics.cache_hit_rate, 0.0)
        self.assertLess(itms_metrics.total_cost, base_metrics.total_cost)
        self.assertEqual(itms_metrics["total_cost"], itms_metrics.total_cost)

    def test_counterfactual_comparison(self):
        """Verifies delta calculations between ITMS and Baseline."""
        itms_m = TelemetryEngine.calculate_itms_metrics(self.itms_prompt, 180, 420)
        base_m = TelemetryEngine.calculate_baseline_metrics(self.base_prompt, 310, 2850)
        delta = TelemetryEngine.compare(itms_m, base_m)

        self.assertGreater(delta.cost_reduction_percent, 70.0)
        self.assertGreater(delta.token_reduction_percent, 70.0)
        self.assertEqual(delta.reasoning_tokens_saved, 2850 - 420)
        self.assertGreater(delta.cost_saved_usd, 0.0)

    def test_cascade_lifecycle_economics(self):
        """Verifies macro-level cascade savings across 1,000 tasks."""
        lifecycle = TelemetryEngine.calculate_cascade_lifecycle_metrics(total_tasks=1000, tier1_pass_rate=0.72)
        self.assertGreater(lifecycle.savings_vs_direct_frontier_pct, 70.0)
        self.assertGreater(lifecycle.savings_vs_naive_agentic_pct, 50.0)
        self.assertLess(lifecycle.itms_relay_cost, lifecycle.naive_agentic_cost)
        self.assertLess(lifecycle.itms_relay_cost, lifecycle.direct_frontier_cost)

    def test_division_by_zero_safety(self):
        """Verifies that empty prompt structures do not throw ZeroDivisionError."""
        empty_prompt = {"static_token_count": 0, "dynamic_token_count": 0, "total_tokens": 0}
        itms_m = TelemetryEngine.calculate_itms_metrics(empty_prompt, 0, 0)
        base_m = TelemetryEngine.calculate_baseline_metrics(empty_prompt, 0, 0)
        delta = TelemetryEngine.compare(itms_m, base_m)

        self.assertEqual(itms_m.cache_hit_rate, 0.0)
        self.assertEqual(delta.cost_reduction_percent, 0.0)

    def test_custom_pricing_profiles(self):
        """Verifies multi-profile support (e.g. Claude 3.7, DeepSeek R1)."""
        r1_pricing = PRESET_PRICING_PROFILES["DeepSeek R1 (Reasoning)"]
        itms_r1 = TelemetryEngine.calculate_itms_metrics(self.itms_prompt, 180, 420, pricing=r1_pricing)
        self.assertEqual(itms_r1.pricing_profile_name, "DeepSeek R1 (Reasoning)")


class TestCodebaseQuality(unittest.TestCase):
    def test_plaintext_strictness_no_emojis(self):
        """Ensures all source files adhere to the strict plaintext constraint (zero emojis)."""
        emoji_pattern = re.compile(r'[\U00010000-\U0010ffff]', flags=re.UNICODE)
        target_files = [
            "skeletonizer.py", "prompt_manager.py", "telemetry.py", "app.py",
            "config.py", "sandbox.py", "model_dispatcher.py", "relay_orchestrator.py",
            "surgical_splicer.py", "edit_orchestrator.py", "benchmark_harness.py"
        ]
        for filename in target_files:
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()
                emojis = emoji_pattern.findall(content)
                self.assertEqual(len(emojis), 0, f"Found emojis in {filename}: {emojis}")


if __name__ == "__main__":
    unittest.main()
