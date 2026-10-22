"""
Deterministic end-to-end test of the real ITMS repair pipeline.

No paid API call is made: a mock dispatcher returns a fixed candidate and the
pipeline under test is the production one
(``RelayOrchestrator`` -> ``ASTNodeLocator`` -> ``ASTSurgicalSplicer`` ->
``ExecutionHarness``).

The fixture is a real benchmark task from the Phase 0 dataset
(``benchmarks/data/tasks/HE-EDIT-009``): the mutated source contains a
``//`` integer division bug, the gold solution repairs it, and the official
test is the evaluator.
"""

import ast
import json
import os
import unittest

from model_dispatcher import ModelDispatcher, ModelResponse
from relay_orchestrator import RelayOrchestrator

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
TASK_ID = "HE-EDIT-009"
TASK_DIR = os.path.join(REPO_ROOT, "benchmarks", "data", "tasks", TASK_ID)
DATA_DIR = os.path.join(REPO_ROOT, "benchmarks", "data")
TARGET_FUNCTION = "greatest_common_divisor"


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _fixture():
    mutated = _read(os.path.join(TASK_DIR, "mutated_source.py"))
    official_test = _read(os.path.join(TASK_DIR, "official_test.py"))
    gold = json.load(
        open(os.path.join(DATA_DIR, "gold_solutions.json"), encoding="utf-8")
    )["gold_solutions"][TASK_ID]
    test_suite = official_test + f"\ncheck({TARGET_FUNCTION})\n"
    return mutated, gold, test_suite


class _FixedDispatcher(ModelDispatcher):
    """Returns a fixed candidate for every call, in a markdown fence."""

    def __init__(self, content: str):
        super().__init__(force_mock=True)
        self.content = content
        self.calls = 0

    def generate(self, prompt, model_name, is_tier1=True, **kwargs):
        self.calls += 1
        return ModelResponse(
            content=self.content,
            raw_tokens_in=120,
            cached_tokens_in=0,
            output_tokens=60,
            reasoning_tokens=0,
            model_name=model_name,
            provider="Deterministic Mock",
            latency_ms=1.0,
            is_mock=True,
        )


class TestITMSEndToEndRepair(unittest.TestCase):
    """The full generation -> splice -> validate -> evaluate path."""

    def setUp(self):
        self.mutated, self.gold, self.test_suite = _fixture()

    def _run(self, content: str):
        dispatcher = _FixedDispatcher(content)
        orchestrator = RelayOrchestrator(dispatcher=dispatcher)
        result = orchestrator.run_cascade(
            task_description="Repair the integer division bug.",
            raw_context_code=self.mutated,
            target_function_name=TARGET_FUNCTION,
            test_suite_code=self.test_suite,
        )
        return result, dispatcher

    def test_successful_repair_passes_syntax_validation_and_the_evaluator(self):
        result, dispatcher = self._run(f"```python\n{self.gold}\n```")

        # The candidate really went through the AST locator and the splicer.
        self.assertEqual(result.splice_status, "spliced")
        self.assertEqual(result.candidate_status, "spliced")
        self.assertTrue(result.evaluated)
        self.assertGreaterEqual(result.attempts, 1)

        # The delivered artefact is the complete spliced source file.
        self.assertIn(f"def {TARGET_FUNCTION}", result.verified_code)
        self.assertNotIn("a // b", result.verified_code)
        self.assertIn("a % b", result.verified_code)

        # It is valid Python ...
        ast.parse(result.verified_code)

        # ... and the evaluator actually ran and passed.
        self.assertTrue(result.final_success)
        self.assertEqual(result.outcome_status, "passed")
        self.assertIsNotNone(result.tier1_result)
        self.assertTrue(result.tier1_result.success)
        self.assertTrue(result.tier1_result.syntax_valid)
        self.assertFalse(result.tier1_result.timed_out)

    def test_buggy_candidate_is_not_reported_as_a_successful_repair(self):
        """A candidate that does not fix the bug must never be a pass."""
        result, _ = self._run(f"```python\n{self.mutated}\n```")

        self.assertFalse(result.final_success)
        self.assertNotEqual(result.outcome_status, "passed")
        self.assertEqual(result.outcome_status, "failed")
        self.assertTrue(result.escalated_to_tier2)
        self.assertFalse(result.tier2_result.success)

    def test_invalid_edit_is_not_reported_as_a_successful_repair(self):
        """A syntactically invalid candidate must be rejected, not passed."""
        result, _ = self._run(
            "```python\ndef greatest_common_divisor(a: int, b: int) -> int\n"
            "    while b:\n        a, b = b, a % b\n    return a\n```"
        )
        self.assertFalse(result.final_success)
        self.assertNotEqual(result.outcome_status, "passed")
        # The rejected splice is recorded, not hidden.
        self.assertIn(result.splice_status, ("spliced", "splice_failed", "no_candidate"))
        self.assertTrue(any("REJECTED" in line for line in result.pipeline_log))

    def test_missing_candidate_is_not_reported_as_a_successful_repair(self):
        """An empty model response must never be reported as a repair."""
        result, dispatcher = self._run("")

        self.assertFalse(result.final_success)
        self.assertNotEqual(result.outcome_status, "passed")
        self.assertIn(
            result.outcome_status,
            ("missing_candidate", "failed", "unevaluated"),
        )
        # The pipeline still bounded its attempts (no unbounded retry loop).
        self.assertLessEqual(dispatcher.calls, 6)

    def test_no_provider_request_is_made(self):
        """The whole path runs on the mock dispatcher only."""
        result, dispatcher = self._run(f"```python\n{self.gold}\n```")
        self.assertTrue(dispatcher.calls >= 1)
        self.assertTrue(result.tier1_response.is_mock)

    def test_timeout_is_not_reported_as_a_successful_repair(self):
        """A candidate that never terminates must fail with a timeout status."""
        from sandbox import ExecutionHarness

        hanging = (
            "```python\ndef greatest_common_divisor(a: int, b: int) -> int:\n"
            "    while True:\n        pass\n```"
        )
        dispatcher = _FixedDispatcher(hanging)
        orchestrator = RelayOrchestrator(
            harness=ExecutionHarness(default_timeout=0.5),
            dispatcher=dispatcher,
        )
        result = orchestrator.run_cascade(
            task_description="Repair the integer division bug.",
            raw_context_code=self.mutated,
            target_function_name=TARGET_FUNCTION,
            test_suite_code=self.test_suite,
        )
        self.assertFalse(result.final_success)
        self.assertNotEqual(result.outcome_status, "passed")
        self.assertEqual(result.outcome_status, "timeout")
        self.assertTrue(result.tier1_result.timed_out)


if __name__ == "__main__":
    unittest.main()
