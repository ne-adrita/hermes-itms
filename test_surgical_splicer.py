import unittest
import ast
import os
from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer, TextDiffPatcher, SurgicalTarget
from edit_orchestrator import SurgicalEditOrchestrator
from sandbox import ExecutionHarness
from skeletonizer import skeletonize_code
from benchmark_harness import MacroBenchmarkSuite

SAMPLE_MODULE_CODE = """# Copyright 2026 NSU ECE Capstone
# Author: ITMS Engineering Team

class UnusedLogger:
    def log_event(self, event: str):
        pass

class AccountLedger:
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.balance = 1000.0

    def reconcile_balance(self, target_date: str) -> float:
        # Developer Comment: Baseline logic
        total = self.balance
        return total

    def close_account(self) -> bool:
        return True
"""

REPLACEMENT_FUNCTION = """def reconcile_balance(self, target_date: str) -> float:
    # Modified: Apply 10% discount deduction
    discount = self.balance * 0.10
    return float(self.balance - discount)
"""

class TestASTNodeLocator(unittest.TestCase):
    def test_locate_class_method(self):
        target = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "AccountLedger.reconcile_balance")
        self.assertIsNotNone(target)
        self.assertEqual(target.name, "reconcile_balance")
        self.assertEqual(target.parent_class, "AccountLedger")
        self.assertEqual(target.base_indentation, "    ")
        self.assertTrue(target.start_line > 1)
        self.assertTrue(target.end_line >= target.start_line)
        self.assertIn("def reconcile_balance", target.original_text)

    def test_locate_by_simple_name(self):
        target = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "reconcile_balance")
        self.assertIsNotNone(target)
        self.assertEqual(target.name, "reconcile_balance")
        self.assertEqual(target.parent_class, "AccountLedger")

    def test_locate_nonexistent_target(self):
        target = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "non_existent_function")
        self.assertIsNone(target)


class TestASTSurgicalSplicer(unittest.TestCase):
    def setUp(self):
        self.target = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "AccountLedger.reconcile_balance")

    def test_clean_splicing_preserves_comments(self):
        success, spliced, err = ASTSurgicalSplicer.splice(
            original_source=SAMPLE_MODULE_CODE,
            target=self.target,
            replacement_code=REPLACEMENT_FUNCTION
        )
        self.assertTrue(success)
        self.assertIsNone(err)
        # Check comments outside the node are 100% preserved
        self.assertIn("# Copyright 2026 NSU ECE Capstone", spliced)
        self.assertIn("# Author: ITMS Engineering Team", spliced)
        self.assertIn("def close_account", spliced)
        # Check replacement is present and correctly indented
        self.assertIn("    def reconcile_balance", spliced)
        self.assertIn("    discount = self.balance * 0.10", spliced)
        self.assertNotIn("Baseline logic", spliced)

    def test_markdown_fence_stripping(self):
        fenced_code = f"```python\n{REPLACEMENT_FUNCTION}\n```"
        success, spliced, err = ASTSurgicalSplicer.splice(
            original_source=SAMPLE_MODULE_CODE,
            target=self.target,
            replacement_code=fenced_code
        )
        self.assertTrue(success)
        self.assertIn("    discount = self.balance * 0.10", spliced)

    def test_syntax_error_rejection(self):
        broken_code = "def reconcile_balance(self: invalid syntax ((("
        success, spliced, err = ASTSurgicalSplicer.splice(
            original_source=SAMPLE_MODULE_CODE,
            target=self.target,
            replacement_code=broken_code
        )
        self.assertFalse(success)
        self.assertIsNotNone(err)
        self.assertIn("SyntaxError", err)
        self.assertEqual(spliced, SAMPLE_MODULE_CODE)


class TestSurgicalSplicerRegression(unittest.TestCase):
    """Focused regression tests for the surgical splicer."""

    NESTED_SOURCE = (
        "# Copyright 2026 NSU ECE Capstone\n"
        "# Author: ITMS Engineering Team\n"
        "\n"
        "import math\n"
        "\n"
        "CONSTANT = 42\n"
        "\n"
        "def unrelated_helper(x):\n"
        "    return x + 1\n"
        "\n"
        "class Outer:\n"
        "    class Inner:\n"
        "        def method(self, x):\n"
        "            # original body comment\n"
        "            def helper(v):\n"
        "                return v * 2\n"
        "            return helper(x) + 1\n"
        "\n"
        "    def sibling(self):\n"
        "        return 'sibling'\n"
        "\n"
        "class Other:\n"
        "    def method(self, x):\n"
        "        return 'other'\n"
    )

    def test_nested_function_stays_inside_the_target_body(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Outer.Inner.method")
        self.assertIsNotNone(target)
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE,
            target,
            "def helper(v):\n    return v * 3\nreturn helper(x) + 5\n",
        )
        self.assertTrue(ok, err)
        tree = ast.parse(spliced)
        module_level = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.assertNotIn("helper", module_level)
        method = tree.body[3].body[0].body[0]   # Outer.Inner.method
        self.assertEqual(method.name, "method")
        self.assertTrue(
            any(isinstance(n, ast.FunctionDef) and n.name == "helper" for n in method.body)
        )

    def test_body_only_replacement_keeps_the_header(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Outer.Inner.method")
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE, target, "return helper(x) + 7\n"
        )
        self.assertTrue(ok, err)
        self.assertIn("def method(self, x):", spliced)
        self.assertIn("return helper(x) + 7", spliced)
        self.assertNotIn("return helper(x) + 1", spliced)

    def test_full_node_replacement_replaces_only_the_target(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Outer.Inner.method")
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE,
            target,
            "def method(self, x):\n    return x * 9\n",
        )
        self.assertTrue(ok, err)
        self.assertIn("return x * 9", spliced)
        self.assertIn("def sibling(self):", spliced)
        self.assertIn("def unrelated_helper(x):", spliced)

    def test_unrelated_source_is_preserved_byte_for_byte(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Outer.Inner.method")
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE,
            target,
            "def method(self, x):\n    return x * 9\n",
        )
        self.assertTrue(ok, err)
        for preserved in (
            "# Copyright 2026 NSU ECE Capstone",
            "# Author: ITMS Engineering Team",
            "import math",
            "CONSTANT = 42",
            "def unrelated_helper(x):",
            "    def sibling(self):",
            "class Other:",
        ):
            self.assertIn(preserved, spliced)
        # Only the target's own line range changed (5 lines -> 2 lines).
        self.assertEqual(
            len(spliced.splitlines()),
            len(self.NESTED_SOURCE.splitlines()) - 5 + 2,
        )

    def test_class_method_replacement_keeps_the_class(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Other.method")
        self.assertIsNotNone(target)
        self.assertEqual(target.parent_class, "Other")
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE, target, "def method(self, x):\n    return 'patched'\n"
        )
        self.assertTrue(ok, err)
        self.assertIn("class Other:", spliced)
        self.assertIn("return 'patched'", spliced)
        self.assertIn("def sibling(self):", spliced)

    def test_syntax_error_in_replacement_is_rejected(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Outer.Inner.method")
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE, target, "def method(self, x)\n    return x\n"
        )
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertEqual(spliced, self.NESTED_SOURCE)

    def test_empty_replacement_is_rejected(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Outer.Inner.method")
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE, target, "   \n```\n```\n"
        )
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertEqual(spliced, self.NESTED_SOURCE)

    def test_ambiguous_target_is_rejected(self):
        # 'method' exists in both Outer.Inner and Other.
        self.assertIsNone(ASTNodeLocator.locate_target(self.NESTED_SOURCE, "method"))
        target, error = ASTNodeLocator.locate_target_ex(self.NESTED_SOURCE, "method")
        self.assertIsNone(target)
        self.assertIn("ambiguous", error.lower())

    def test_missing_target_is_rejected(self):
        self.assertIsNone(ASTNodeLocator.locate_target(self.NESTED_SOURCE, "nope"))
        target, error = ASTNodeLocator.locate_target_ex(self.NESTED_SOURCE, "nope")
        self.assertIsNone(target)
        self.assertIn("not found", error.lower())

    def test_stale_target_is_rejected(self):
        target = ASTNodeLocator.locate_target(self.NESTED_SOURCE, "Outer.Inner.method")
        stale = SurgicalTarget(
            name=target.name,
            qualified_name=target.qualified_name,
            node_type=target.node_type,
            start_line=target.start_line + 5,
            end_line=target.end_line + 5,
            body_start_line=target.body_start_line + 5,
            base_indentation=target.base_indentation,
            original_text=target.original_text,
            parent_class=target.parent_class,
        )
        ok, spliced, err = ASTSurgicalSplicer.splice(
            self.NESTED_SOURCE, stale, "def method(self, x):\n    return 1\n"
        )
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertEqual(spliced, self.NESTED_SOURCE)

    def test_source_with_syntax_error_is_handled_safely(self):
        broken = "def method(self, x)\n    return x\n"
        self.assertIsNone(ASTNodeLocator.locate_target(broken, "method"))
        target, error = ASTNodeLocator.locate_target_ex(broken, "method")
        self.assertIsNone(target)
        self.assertIn("not valid python", error.lower())


class TestTextDiffPatcher(unittest.TestCase):
    def test_exact_search_replace_success(self):
        search_block = "        total = self.balance\n        return total"
        replace_block = "        return float(self.balance * 0.90)"
        ok, patched, err = TextDiffPatcher.apply_search_replace(
            original_source=SAMPLE_MODULE_CODE,
            search_block=search_block,
            replace_block=replace_block
        )
        self.assertTrue(ok)
        self.assertIn("return float(self.balance * 0.90)", patched)

    def test_whitespace_drift_failure(self):
        # 4 spaces instead of 8 spaces
        search_block = "    total = self.balance\n    return total"
        replace_block = "    return float(self.balance * 0.90)"
        ok, patched, err = TextDiffPatcher.apply_search_replace(
            original_source=SAMPLE_MODULE_CODE,
            search_block=search_block,
            replace_block=replace_block
        )
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertIn("Whitespace", err)


class TestSurgicalEditOrchestrator(unittest.TestCase):
    def test_tri_way_benchmark_metrics(self):
        orchestrator = SurgicalEditOrchestrator()

        full_code = SAMPLE_MODULE_CODE.replace(
            "total = self.balance\n        return total",
            "discount = self.balance * 0.10\n        return float(self.balance - discount)"
        )
        search_b = "        total = self.balance\n        return total"
        replace_b = "        discount = self.balance * 0.10\n        return float(self.balance - discount)"

        report = orchestrator.run_benchmark(
            original_source=SAMPLE_MODULE_CODE,
            target_name="AccountLedger.reconcile_balance",
            edit_instruction="Apply 10% discount in reconcile_balance",
            replacement_node_code=REPLACEMENT_FUNCTION,
            simulated_full_file_code=full_code,
            simulated_search_block=search_b,
            simulated_replace_block=replace_b,
            test_suite_code="ledger = AccountLedger('acc1'); assert ledger.reconcile_balance('2026') == 900.0",
            simulate_diff_whitespace_error=True
        )

        # Token / cost deltas are derived from *local estimates*, and remain
        # informative because both strategies are costed with the same
        # estimator and the same pricing profile.
        self.assertGreater(report.output_tokens_saved_vs_full_pct, 60.0)
        self.assertGreater(report.cost_saved_vs_full_pct, 70.0)

        # ITMS succeeded while whitespace-drifted search/replace failed.
        self.assertTrue(report.patch_reliability_itms)
        self.assertFalse(report.patch_reliability_diff)
        self.assertEqual(report.itms_surgical_result.status, "passed")
        self.assertEqual(report.search_replace_result.status, "invalid_edit")

        # Honest provenance: this run made no provider request.
        self.assertFalse(report.is_measurement)
        self.assertEqual(report.measurement_mode, "simulated_local_processing")
        self.assertEqual(report.token_source, "local_estimate")
        for result in (
            report.full_file_result,
            report.search_replace_result,
            report.itms_surgical_result,
        ):
            self.assertEqual(result.token_source, "local_estimate")
            self.assertEqual(result.cost_source, "estimated_from_local_token_estimate")
            self.assertIn("no provider request", result.latency_source)
            self.assertGreaterEqual(result.latency_ms, 0.0)
            self.assertEqual(result.attempts, 1)
            self.assertIsNone(result.cached_input_tokens)

        # No latency "speedup" may be claimed from local processing times.
        self.assertIsNone(report.latency_speedup_factor)

    def test_unevaluated_strategies_are_not_reported_as_success(self):
        """Without an evaluator a strategy must be 'unevaluated', not 'passed'."""
        orchestrator = SurgicalEditOrchestrator()
        report = orchestrator.run_benchmark(
            original_source=SAMPLE_MODULE_CODE,
            target_name="AccountLedger.reconcile_balance",
            edit_instruction="Apply 10% discount in reconcile_balance",
            replacement_node_code=REPLACEMENT_FUNCTION,
            simulated_full_file_code=SAMPLE_MODULE_CODE,
            simulated_search_block="        total = self.balance\n        return total",
            simulated_replace_block="        return 0.0",
            test_suite_code="",   # no evaluator available
        )
        for result in (
            report.full_file_result,
            report.search_replace_result,
            report.itms_surgical_result,
        ):
            self.assertEqual(result.status, "unevaluated")
            self.assertFalse(result.success)
            self.assertEqual(result.unevaluated_attempts, 1)
            self.assertEqual(result.passed_attempts, 0)
        self.assertFalse(report.patch_reliability_itms)
        self.assertFalse(report.patch_reliability_diff)

    def test_failing_evaluation_is_recorded_as_failure(self):
        """A candidate that does not satisfy the evaluator is a failure."""
        orchestrator = SurgicalEditOrchestrator()
        report = orchestrator.run_benchmark(
            original_source=SAMPLE_MODULE_CODE,
            target_name="AccountLedger.reconcile_balance",
            edit_instruction="Apply 10% discount in reconcile_balance",
            replacement_node_code=REPLACEMENT_FUNCTION,
            simulated_full_file_code=SAMPLE_MODULE_CODE,
            simulated_search_block="        total = self.balance\n        return total",
            simulated_replace_block="        return 0.0",
            test_suite_code="ledger = AccountLedger('acc1'); assert ledger.reconcile_balance('2026') == 900.0",
        )
        # The unmodified full file does not satisfy the evaluator ...
        self.assertEqual(report.full_file_result.status, "failed")
        self.assertFalse(report.full_file_result.success)
        self.assertEqual(report.full_file_result.failed_attempts, 1)
        self.assertEqual(report.full_file_result.passed_attempts, 0)
        # ... while the correctly spliced ITMS candidate does.
        self.assertEqual(report.itms_surgical_result.status, "passed")
        self.assertTrue(report.itms_surgical_result.success)

    def test_wrong_replacement_is_not_reported_as_success(self):
        """A syntactically valid but semantically wrong edit is a failure."""
        orchestrator = SurgicalEditOrchestrator()
        wrong_replacement = (
            "def reconcile_balance(self, target_date: str) -> float:\n"
            "    return float(self.balance)\n"
        )
        report = orchestrator.run_benchmark(
            original_source=SAMPLE_MODULE_CODE,
            target_name="AccountLedger.reconcile_balance",
            edit_instruction="Apply 10% discount in reconcile_balance",
            replacement_node_code=wrong_replacement,
            simulated_full_file_code=SAMPLE_MODULE_CODE,
            simulated_search_block="        total = self.balance\n        return total",
            simulated_replace_block="        return 0.0",
            test_suite_code="ledger = AccountLedger('acc1'); assert ledger.reconcile_balance('2026') == 900.0",
        )
        self.assertEqual(report.itms_surgical_result.status, "failed")
        self.assertFalse(report.itms_surgical_result.success)
        self.assertFalse(report.patch_reliability_itms)


class TestSystemResilienceAndSecurity(unittest.TestCase):
    def test_locate_top_level_class(self):
        """Verifies top-level class can be located directly by unqualified name."""
        target = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "AccountLedger")
        self.assertIsNotNone(target)
        self.assertEqual(target.name, "AccountLedger")
        self.assertEqual(target.node_type, "ClassDef")
        self.assertIn("class AccountLedger:", target.original_text)

    def test_locate_multi_level_qualified_name(self):
        """Verifies multi-level qualified names (Outer.Inner.method) can be located."""
        nested_code = """class Outer:
    class Inner:
        def process(self, x: int) -> int:
            return x * 2
"""
        target = ASTNodeLocator.locate_target(nested_code, "Outer.Inner.process")
        self.assertIsNotNone(target)
        self.assertEqual(target.name, "process")
        self.assertEqual(target.qualified_name, "Outer.Inner.process")
        self.assertEqual(target.base_indentation, "        ")

    def test_body_only_splicing(self):
        """Verifies replacing only the function body without re-declaring the def statement."""
        target = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "AccountLedger.reconcile_balance")
        body_only_code = "discount = self.balance * 0.20\nreturn float(self.balance - discount)"
        success, spliced, err = ASTSurgicalSplicer.splice(
            original_source=SAMPLE_MODULE_CODE,
            target=target,
            replacement_code=body_only_code
        )
        self.assertTrue(success, f"Body-only splice failed: {err}")
        self.assertIn("def reconcile_balance(self, target_date: str) -> float:", spliced)
        self.assertIn("discount = self.balance * 0.20", spliced)

    def test_splice_multiple_atomic(self):
        """Verifies multi-target surgical splicing in reverse order without offset invalidation."""
        target1 = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "AccountLedger.reconcile_balance")
        target2 = ASTNodeLocator.locate_target(SAMPLE_MODULE_CODE, "AccountLedger.close_account")
        replacements = [
            (target1, "def reconcile_balance(self, target_date: str) -> float:\n    return 42.0"),
            (target2, "def close_account(self) -> bool:\n    return False")
        ]
        ok, spliced, err = ASTSurgicalSplicer.splice_multiple(SAMPLE_MODULE_CODE, replacements)
        self.assertTrue(ok, f"Multi-splice failed: {err}")
        self.assertIn("return 42.0", spliced)
        self.assertIn("return False", spliced)

    def test_sandbox_environment_sanitization(self):
        """Verifies execution harness scrubs sensitive credential tokens from subprocess environment."""
        os.environ["MOCK_API_KEY"] = "sk-test-super-secret-12345"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-token-67890"
        os.environ["SECRET_TOKEN"] = "token-xyz"
        
        env = ExecutionHarness.get_sanitized_sandbox_env()
        self.assertNotIn("MOCK_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertIn("PATH", env)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

        # Cleanup test environment variables
        del os.environ["MOCK_API_KEY"]
        del os.environ["ANTHROPIC_API_KEY"]
        del os.environ["SECRET_TOKEN"]

    def test_transitive_reachability_base_classes(self):
        """Verifies reachability analysis preserves base classes and annotations transitively."""
        hierarchy_code = """class BaseRecord:
    pass

class DetailRecord(BaseRecord):
    val: int

class Processor:
    def execute(self, record: DetailRecord) -> int:
        return 1
"""
        skeleton = skeletonize_code(
            source_code=hierarchy_code,
            target_function_name="Processor.execute",
            strip_docstrings=True,
            prune_unreachable=True
        )
        self.assertIn("class BaseRecord:", skeleton)
        self.assertIn("class DetailRecord(BaseRecord):", skeleton)
        self.assertIn("class Processor:", skeleton)

    def test_macro_benchmark_suite_execution(self):
        """
        Verifies the macro benchmark suite executes every canonical task and
        generates summary telemetry.

        The task set is the 20 approved Phase 0 repair tasks. The mock
        engine cannot repair them, so the pass rate is 0 % and the run is
        flagged as mock - which is exactly the state the contamination guard
        exists to catch.
        """
        report = MacroBenchmarkSuite.run_suite()
        self.assertEqual(report.total_tasks, 20)
        self.assertEqual(len(report.run_records), 20)
        self.assertEqual(len(report.task_results), 20)
        self.assertIsInstance(report.pass_rate_pct, float)
        self.assertGreaterEqual(report.pass_rate_pct, 0.0)
        self.assertLessEqual(report.pass_rate_pct, 100.0)
        self.assertTrue(report.contains_mock_results)
        self.assertGreater(report.cost_savings_vs_frontier_pct, 50.0)
        self.assertGreater(report.cost_savings_vs_naive_pct, 40.0)
        self.assertGreater(len(report.task_results), 0)


if __name__ == "__main__":
    unittest.main()
