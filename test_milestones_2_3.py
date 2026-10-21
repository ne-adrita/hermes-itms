import unittest
import os
import re
from config import AppConfig
from sandbox import ExecutionHarness, ExecutionResult
from model_dispatcher import ModelDispatcher, ModelResponse
from relay_orchestrator import RelayOrchestrator, CascadeRunResult
import app


class TestSecurityAndConfig(unittest.TestCase):
    def test_gitignore_protects_env_and_secrets(self):
        """Verifies .gitignore exists and explicitly ignores .env and log files."""
        self.assertTrue(os.path.exists(".gitignore"), ".gitignore file must exist")
        with open(".gitignore", "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn(".env", content, ".env must be ignored in .gitignore")
            self.assertIn("*.log", content, "*.log must be ignored in .gitignore")

    def test_api_key_masking(self):
        """Verifies AppConfig masks secret keys to prevent accidental leaks in logs or UI."""
        raw_key = "sk-proj-1234567890abcdef998877"
        masked = AppConfig.mask_key(raw_key)
        self.assertEqual(masked, "sk-...8877")
        self.assertNotIn("1234567890abcdef", masked)
        self.assertEqual(AppConfig.mask_key(None), "[NOT CONFIGURED]")
        self.assertEqual(AppConfig.mask_key(""), "[NOT CONFIGURED]")


class TestSandboxExecutionHarness(unittest.TestCase):
    def setUp(self):
        self.harness = ExecutionHarness(default_timeout=2.0)

    def test_successful_execution(self):
        """Verifies clean candidate code passes sandboxed unit tests."""
        res = self.harness.execute_test(
            candidate_code="def multiply(a, b): return a * b",
            test_suite_code="assert multiply(6, 7) == 42"
        )
        self.assertTrue(res.success)
        self.assertEqual(res.exit_code, 0)
        self.assertIsNone(res.error_type)

    def test_failing_assertion(self):
        """Verifies assertion failure returns clean diagnostic signal."""
        res = self.harness.execute_test(
            candidate_code="def multiply(a, b): return a + b",
            test_suite_code="assert multiply(6, 7) == 42"
        )
        self.assertFalse(res.success)
        self.assertNotEqual(res.exit_code, 0)
        self.assertEqual(res.error_type, "AssertionError")
        self.assertIn("assert multiply(6, 7) == 42", res.failing_assertion)
        self.assertIn("- Failure Mode: AssertionError", res.to_clean_diagnostic_signal())

    def test_syntax_error_prevalidation(self):
        """Verifies invalid syntax is rejected before subprocess execution."""
        res = self.harness.execute_test(
            candidate_code="def broken_func(x: return 123"
        )
        self.assertFalse(res.success)
        self.assertFalse(res.syntax_valid)
        self.assertEqual(res.error_type, "SyntaxError")

    def test_timeout_protection(self):
        """Verifies infinite loops are safely terminated by timeout boundaries."""
        res = self.harness.execute_test(
            candidate_code="import time\nwhile True: time.sleep(0.05)",
            timeout=0.4
        )
        self.assertFalse(res.success)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.error_type, "TimeoutExpired")

    def test_class_method_binding(self):
        """Verifies candidate methods are automatically bound to target classes."""
        context = """
class Calculator:
    def calculate(self, x: int) -> int:
        return 0
"""
        candidate = """
    def calculate(self, x: int) -> int:
        return x * 10
"""
        test_suite = """
calc = Calculator()
assert calc.calculate(5) == 50
"""
        res = self.harness.execute_test(
            candidate_code=candidate,
            context_code=context,
            test_suite_code=test_suite,
            target_function_name="calculate"
        )
        self.assertTrue(res.success, f"Method binding failed: {res.stderr}")


class TestModelDispatcher(unittest.TestCase):
    def test_mock_generation_format(self):
        """Verifies ModelDispatcher returns structured ModelResponse in mock mode."""
        dispatcher = ModelDispatcher(force_mock=True)
        resp = dispatcher.generate(
            prompt="Implement test function",
            model_name="Gemini 3.5 Flash",
            is_tier1=True
        )
        self.assertTrue(resp.is_mock)
        self.assertGreater(resp.raw_tokens_in, 0)
        self.assertGreater(resp.output_tokens, 0)
        self.assertIn("def reconcile_balance", resp.content)

    def test_provider_resolution(self):
        """Verifies model name resolution to provider category."""
        self.assertEqual(ModelDispatcher._resolve_provider("Gemini 3.5 Flash"), "gemini")
        self.assertEqual(ModelDispatcher._resolve_provider("Claude Sonnet 5.5"), "anthropic")
        self.assertEqual(ModelDispatcher._resolve_provider("GPT-5.6 Astra"), "openai")
        self.assertEqual(ModelDispatcher._resolve_provider("DeepSeek V4"), "deepseek")


class TestRelayOrchestrator(unittest.TestCase):
    def test_tier1_failure_triggers_clean_trace_escalation(self):
        """
        Verifies that when Tier 1 fails, the broken draft is discarded,
        and Tier 2 resolves the problem via ITMS clean-trace prompt.
        """
        orchestrator = RelayOrchestrator()
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable"
        )

        # Verification of cascade states
        self.assertTrue(result.final_success, "Final result should pass after Tier 2 resolution")
        self.assertTrue(result.escalated_to_tier2, "Should have escalated after Tier 1 failure")
        self.assertFalse(result.tier1_result.success, "Tier 1 should have failed")
        self.assertTrue(result.tier2_result.success, "Tier 2 should have succeeded")

        # Context Hygiene / Anti-Anchoring Invariant:
        # Tier 2 prompt MUST NOT contain Tier 1's broken code draft!
        self.assertNotIn("Inverted condition and missing tax deduction logic", result.tier2_prompt)
        self.assertNotIn(result.tier1_response.content, result.tier2_prompt)

        # Telemetry savings verification
        self.assertGreater(result.cost_saved_vs_frontier_pct, 50.0)
        self.assertGreater(result.cost_saved_vs_naive_pct, 50.0)

    def test_tier1_direct_pass_avoids_escalation(self):
        """Verifies that when Tier 1 passes, Tier 2 is never called, saving maximum cost."""
        succ_candidate = """
def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            tax = tx.calculate_tax_allocation(0.05)
            total += (tx.amount - tax)
    return round(float(total), 2)
"""
        class PassingTier1Dispatcher(ModelDispatcher):
            def generate(self, prompt, model_name, is_tier1=True, **kwargs):
                return ModelResponse(
                    content=succ_candidate,
                    raw_tokens_in=200,
                    cached_tokens_in=0,
                    output_tokens=75,
                    reasoning_tokens=0,
                    model_name=model_name,
                    provider="Gemini Mock",
                    latency_ms=110.0,
                    is_mock=True
                )

        orchestrator = RelayOrchestrator(dispatcher=PassingTier1Dispatcher())
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable"
        )

        self.assertTrue(result.final_success)
        self.assertFalse(result.escalated_to_tier2, "Escalation must be avoided when Tier 1 passes")
        self.assertIsNone(result.tier2_response)
        self.assertIsNone(result.tier2_prompt)
        self.assertGreater(result.cost_saved_vs_frontier_pct, 80.0)


    def test_tier1_syntactic_error_triggers_intra_tier_self_repair(self):
        """
        Verifies that when Tier 1 produces a syntax/indentation slip, the orchestrator
        triages it into the intra-tier self-repair loop, repairs it in Tier 1,
        and avoids expensive Tier 2 escalation.
        """
        class SyntaxErrorThenPassDispatcher(ModelDispatcher):
            def __init__(self):
                super().__init__(force_mock=True)
                self.calls = 0

            def generate(self, prompt, model_name, is_tier1=True, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    # Initial pass produces a syntax slip (missing colon)
                    broken_syntax = "def reconcile_balance(self, target_date: str) -> float\n    return 0.0"
                    return ModelResponse(
                        content=broken_syntax,
                        raw_tokens_in=180,
                        cached_tokens_in=0,
                        output_tokens=40,
                        reasoning_tokens=0,
                        model_name=model_name,
                        provider="Gemini Mock",
                        latency_ms=90.0,
                        is_mock=True
                    )
                else:
                    # Self-repair prompt received; Tier 1 repairs the slip
                    repaired_code = """
def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            tax = tx.calculate_tax_allocation(0.05)
            total += (tx.amount - tax)
    return round(float(total), 2)
"""
                    return ModelResponse(
                        content=repaired_code,
                        raw_tokens_in=250,
                        cached_tokens_in=0,
                        output_tokens=80,
                        reasoning_tokens=0,
                        model_name=model_name,
                        provider="Gemini Mock",
                        latency_ms=95.0,
                        is_mock=True
                    )

        dispatcher = SyntaxErrorThenPassDispatcher()
        orchestrator = RelayOrchestrator(dispatcher=dispatcher)
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable",
            enable_tier1_self_repair=True,
            max_tier1_retries=2
        )

        self.assertTrue(result.final_success, "Pipeline should succeed")
        self.assertFalse(result.escalated_to_tier2, "Should NOT escalate to Tier 2 for trivial syntax slip")
        self.assertEqual(result.tier1_surgical_repairs, 1, "Should record exactly 1 Tier 1 intra-tier repair")
        self.assertIsNone(result.tier2_response, "Tier 2 model should not have been invoked")
        self.assertIsNone(result.tier2_prompt, "Tier 2 prompt should not exist")
        self.assertGreater(result.cost_saved_vs_frontier_pct, 75.0)

    def test_tier1_self_repair_disabled_escalates_immediately(self):
        """
        Verifies that when enable_tier1_self_repair is False, even a syntax error
        bypasses the intra-tier loop and escalates immediately to Tier 2.
        """
        class SyntaxErrorDispatcher(ModelDispatcher):
            def __init__(self):
                super().__init__(force_mock=True)

            def generate(self, prompt, model_name, is_tier1=True, **kwargs):
                if is_tier1:
                    broken_syntax = "def reconcile_balance(self, target_date: str) -> float\n    return 0.0"
                    return ModelResponse(
                        content=broken_syntax,
                        raw_tokens_in=180,
                        cached_tokens_in=0,
                        output_tokens=40,
                        reasoning_tokens=0,
                        model_name=model_name,
                        provider="Gemini Mock",
                        latency_ms=90.0,
                        is_mock=True
                    )
                else:
                    valid_code = """
def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            tax = tx.calculate_tax_allocation(0.05)
            total += (tx.amount - tax)
    return round(float(total), 2)
"""
                    return ModelResponse(
                        content=valid_code,
                        raw_tokens_in=250,
                        cached_tokens_in=0,
                        output_tokens=80,
                        reasoning_tokens=200,
                        model_name=model_name,
                        provider="Anthropic Mock",
                        latency_ms=600.0,
                        is_mock=True
                    )

        orchestrator = RelayOrchestrator(dispatcher=SyntaxErrorDispatcher())
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable",
            enable_tier1_self_repair=False
        )

        self.assertTrue(result.final_success)
        self.assertTrue(result.escalated_to_tier2, "Should escalate to Tier 2 when self repair is disabled")
        self.assertEqual(result.tier1_surgical_repairs, 0)
        self.assertIsNotNone(result.tier2_response)

    def test_tier1_exhausted_retries_escalates_to_tier2(self):
        """
        Verifies that when Tier 1 self-repair retries are exhausted without passing,
        the orchestrator cleanly escalates to Tier 2 and preserves the anti-anchoring invariant.
        """
        class AlwaysBrokenTier1Dispatcher(ModelDispatcher):
            def __init__(self):
                super().__init__(force_mock=True)

            def generate(self, prompt, model_name, is_tier1=True, **kwargs):
                if is_tier1:
                    broken_syntax = "def reconcile_balance(self, target_date: str) -> float\n    return 0.0"
                    return ModelResponse(
                        content=broken_syntax,
                        raw_tokens_in=180,
                        cached_tokens_in=0,
                        output_tokens=40,
                        reasoning_tokens=0,
                        model_name=model_name,
                        provider="Gemini Mock",
                        latency_ms=90.0,
                        is_mock=True
                    )
                else:
                    valid_code = """
def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            tax = tx.calculate_tax_allocation(0.05)
            total += (tx.amount - tax)
    return round(float(total), 2)
"""
                    return ModelResponse(
                        content=valid_code,
                        raw_tokens_in=250,
                        cached_tokens_in=0,
                        output_tokens=80,
                        reasoning_tokens=300,
                        model_name=model_name,
                        provider="Anthropic Mock",
                        latency_ms=700.0,
                        is_mock=True
                    )

        orchestrator = RelayOrchestrator(dispatcher=AlwaysBrokenTier1Dispatcher())
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable",
            enable_tier1_self_repair=True,
            max_tier1_retries=2
        )

        self.assertTrue(result.final_success)
        self.assertTrue(result.escalated_to_tier2, "Should escalate after retries exhausted")
        self.assertEqual(result.tier1_surgical_repairs, 2, "Should have executed 2 self-repair attempts")
        self.assertIn("exhausted", result.escalation_reason.lower())
        self.assertIsNotNone(result.tier2_response)

    def test_scenario_2_data_pipeline_execution(self):
        """
        Verifies that Scenario 2 (Data Pipeline & SHA256 Validator) executes cleanly,
        escalates on Tier 1 assertion logic failure, and resolves on Tier 2 in mock mode.
        """
        orchestrator = RelayOrchestrator()
        result = orchestrator.run_cascade(
            task_description=app.SCENARIO_2_TASK,
            raw_context_code=app.SCENARIO_2_CONTEXT,
            target_function_name="dispatch_batch",
            test_suite_code=app.SCENARIO_2_TEST_SUITE,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable",
            enable_tier1_self_repair=True,
            max_tier1_retries=2
        )

        self.assertTrue(result.final_success, "Scenario 2 should succeed")
        self.assertTrue(result.escalated_to_tier2, "Scenario 2 should escalate to Tier 2")
        self.assertFalse(result.tier1_result.success, "Tier 1 should fail assertion check")
        self.assertTrue(result.tier2_result.success, "Tier 2 should succeed")
        self.assertIn("def dispatch_batch", result.verified_code)
    def test_class_wrapped_candidate_unwrapping_prevents_escalation(self):
        """
        Verifies that when Tier 1 wraps candidate method inside a redundant class definition,
        the AST splicer unwraps the method directly into the context class, preventing
        nested-class AttributeError and avoiding spurious Tier 2 escalation.
        """
        class ClassWrappedDispatcher(ModelDispatcher):
            def generate(self, prompt, model_name, is_tier1=True, **kwargs):
                wrapped_code = """```python
class AccountLedger:
    def reconcile_balance(self, target_date: str) -> float:
        total = 0.0
        for tx in self.transactions:
            if tx.validate_checksum():
                tax = tx.calculate_tax_allocation(0.05)
                total += (tx.amount - tax)
        return round(float(total), 2)
```"""
                return ModelResponse(
                    content=wrapped_code,
                    raw_tokens_in=200,
                    cached_tokens_in=0,
                    output_tokens=80,
                    reasoning_tokens=0,
                    model_name=model_name,
                    provider="Gemini Mock",
                    latency_ms=100.0,
                    is_mock=True
                )

        orchestrator = RelayOrchestrator(dispatcher=ClassWrappedDispatcher())
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable"
        )
        self.assertTrue(result.final_success, "Pipeline should succeed")
        self.assertFalse(result.escalated_to_tier2, "Should NOT escalate to Tier 2 when candidate is class-wrapped")
        self.assertEqual(result.tier1_surgical_repairs, 0, "Should pass on first attempt")

    def test_multiblock_markdown_code_extraction(self):
        """
        Verifies that when Tier 1 generates auxiliary helpers in separate markdown code fences,
        both fences are preserved, preventing spurious NameError on helper functions.
        """
        class MultiBlockDispatcher(ModelDispatcher):
            def generate(self, prompt, model_name, is_tier1=True, **kwargs):
                multi_block = """Certainly! Here is the helper function:
```python
def compute_clean_tax(amount: float) -> float:
    return amount * 0.05
```
And here is the target method implementation:
```python
def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            total += (tx.amount - compute_clean_tax(tx.amount))
    return round(float(total), 2)
```
"""
                return ModelResponse(
                    content=multi_block,
                    raw_tokens_in=220,
                    cached_tokens_in=0,
                    output_tokens=90,
                    reasoning_tokens=0,
                    model_name=model_name,
                    provider="Gemini Mock",
                    latency_ms=115.0,
                    is_mock=True
                )

        orchestrator = RelayOrchestrator(dispatcher=MultiBlockDispatcher())
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable"
        )
        self.assertTrue(result.final_success)
        self.assertFalse(result.escalated_to_tier2, "Should NOT escalate to Tier 2 on multi-block response")

    def test_convenience_imports_prevent_spurious_name_error(self):
        """
        Verifies that candidate code using standard library utilities (defaultdict, Decimal, math)
        without explicit import statements executes without NameError.
        """
        harness = ExecutionHarness()
        candidate = """def calculate_total(items: list) -> float:
    counts = defaultdict(int)
    for it in items:
        counts[it] += 1
    d = Decimal('10.5')
    return float(math.sqrt(100) + counts['apple'] * float(d))
"""
        test_suite = "assert calculate_total(['apple', 'banana', 'apple']) == 31.0"
        res = harness.execute_test(candidate_code=candidate, test_suite_code=test_suite)
        self.assertTrue(res.success, f"Failed with: {res.stderr}")

    def test_assertion_error_repair_grant_avoids_tier2(self):
        """
        Verifies that when Tier 1 makes an initial arithmetic slip (AssertionError),
        the intra-tier repair loop gives Tier 1 an opportunity to fix the assertion,
        avoiding a costly Tier 2 escalation.
        """
        class AssertionThenFixDispatcher(ModelDispatcher):
            def __init__(self):
                super().__init__(force_mock=True)
                self.calls = 0

            def generate(self, prompt, model_name, is_tier1=True, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    # Initial pass has a calculation slip (adds tax instead of subtracting)
                    slip_code = """
def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            tax = tx.calculate_tax_allocation(0.05)
            total += (tx.amount + tax)
    return round(float(total), 2)
"""
                    return ModelResponse(
                        content=slip_code,
                        raw_tokens_in=200,
                        cached_tokens_in=0,
                        output_tokens=70,
                        reasoning_tokens=0,
                        model_name=model_name,
                        provider="Gemini Mock",
                        latency_ms=90.0,
                        is_mock=True
                    )
                else:
                    # Tier 1 receives the AssertionError signal and fixes the sign!
                    fixed_code = """
def reconcile_balance(self, target_date: str) -> float:
    total = 0.0
    for tx in self.transactions:
        if tx.validate_checksum():
            tax = tx.calculate_tax_allocation(0.05)
            total += (tx.amount - tax)
    return round(float(total), 2)
"""
                    return ModelResponse(
                        content=fixed_code,
                        raw_tokens_in=260,
                        cached_tokens_in=0,
                        output_tokens=70,
                        reasoning_tokens=0,
                        model_name=model_name,
                        provider="Gemini Mock",
                        latency_ms=95.0,
                        is_mock=True
                    )

        orchestrator = RelayOrchestrator(dispatcher=AssertionThenFixDispatcher())
        test_suite = """
ledger = AccountLedger('acc_1')
r1 = TransactionRecord('tx_valid', 1000.0)
r1.validate_checksum = lambda: True
r1.calculate_tax_allocation = lambda rate: 50.0
ledger.append_transaction(r1)
assert ledger.reconcile_balance('2026-10-01') == 950.0
"""
        result = orchestrator.run_cascade(
            task_description=app.SAMPLE_TASK,
            raw_context_code=app.SAMPLE_CONTEXT_CODE,
            target_function_name="reconcile_balance",
            test_suite_code=test_suite,
            tier1_model_name="Gemini 3.5 Flash",
            tier2_model_name="Claude Sonnet 5.5 / Fable",
            enable_tier1_self_repair=True,
            max_tier1_retries=2
        )
        self.assertTrue(result.final_success, "Pipeline should succeed")
        self.assertFalse(result.escalated_to_tier2, "Should NOT escalate to Tier 2 when Tier 1 repairs assertion")
        self.assertEqual(result.tier1_surgical_repairs, 1, "Should record exactly 1 Tier 1 intra-tier repair")
        self.assertIsNone(result.tier2_response, "Tier 2 should NOT be called")


if __name__ == "__main__":
    unittest.main()
