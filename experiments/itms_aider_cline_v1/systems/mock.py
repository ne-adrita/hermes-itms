"""
Mock system adapters used by the offline dry run.

These adapters perform **no** network traffic and launch **no** external
process.  They exist so the runner, the evaluator, the artifact writer and the
record schema can be exercised end to end without spending API budget.

The mock ITMS adapter produces a *correct* repair for a known subset of
tasks and a deliberately wrong repair for the rest, so the dry run exercises
both the pass and the fail branches of the evaluator.  The mock Aider and
mock Cline adapters emulate only the *shape* of the real tools' telemetry --
they are clearly labelled ``is_mock`` and can never be reported as a
measurement of the real systems.
"""

from __future__ import annotations

import ast
import os
import time
from typing import Any, Dict, List, Optional

from .base import (
    FailureCategory,
    SystemAdapter,
    SystemOutcome,
    TaskBundle,
    UsageSource,
    sha256_text,
)


class MockSystemAdapter(SystemAdapter):
    """A deterministic, offline stand-in for a real system."""

    name = "mock"
    supports_seed = False
    requires_credential = False

    def __init__(self, behaviour: str = "mixed", token_scale: int = 100) -> None:
        #: "correct"  -> always produce a passing repair
        #: "wrong"    -> always produce a failing repair
        #: "mixed"    -> alternate deterministically by task id
        #: "broken"   -> produce syntactically invalid output
        self.behaviour = behaviour
        self.token_scale = token_scale

    def preflight(self) -> Dict[str, Any]:
        return {
            "system": self.name,
            "available": True,
            "detail": "offline mock; no network, no subprocess",
            "supports_seed": self.supports_seed,
        }

    def describe_configuration(self) -> Dict[str, Any]:
        return {
            "system": self.name,
            "behaviour": self.behaviour,
            "is_mock": True,
            "supports_seed": self.supports_seed,
            "telemetry_source": "synthetic (mock)",
        }

    def run(
        self,
        task: TaskBundle,
        workspace: str,
        repetition_id: str,
        seed: Optional[int] = None,
    ) -> SystemOutcome:
        started = time.perf_counter()
        outcome = SystemOutcome(
            system=self.name,
            task_id=task.task_id,
            repetition_id=repetition_id,
            seed_requested=seed,
            seed_supported=False,
            seed_applied=None,
            telemetry_source="synthetic (mock)",
        )

        index = sum(ord(ch) for ch in task.task_id)
        behaviour = self.behaviour
        if behaviour == "mixed":
            behaviour = ("correct", "wrong", "correct", "broken")[index % 4]

        target_path = os.path.join(workspace, task.workspace_filename)
        source = task.mutated_source

        if behaviour == "correct":
            source = _correct_source(task, source)
        elif behaviour == "wrong":
            source = source  # unchanged -> test fails
        else:  # broken
            source = source + "\ndef broken(:\n    pass\n"

        with open(target_path, "w", encoding="utf-8") as fh:
            fh.write(source)

        outcome.execution_completed = True
        outcome.latency_ms = (time.perf_counter() - started) * 1000.0 + 12.5
        outcome.candidate_source = source
        outcome.candidate_sha256 = sha256_text(source)
        outcome.patch_applied = behaviour != "wrong"
        outcome.prompt_tokens = self.token_scale * 10
        outcome.completion_tokens = self.token_scale
        outcome.total_tokens = self.token_scale * 11
        outcome.usage_source = UsageSource.LOCAL_ESTIMATE
        outcome.cost_usd = 0.0
        outcome.cost_source = UsageSource.LOCAL_ESTIMATE
        outcome.extra["is_mock"] = True
        outcome.extra["mock_behaviour"] = behaviour

        try:
            ast.parse(source)
            outcome.syntax_valid = True
        except SyntaxError:
            outcome.syntax_valid = False
            outcome.failure_category = FailureCategory.SYNTAX_ERROR
            outcome.failure_reason = "mock produced an unparsable candidate"
        if outcome.failure_category == FailureCategory.NONE and behaviour == "wrong":
            outcome.failure_category = FailureCategory.NONE
            outcome.failure_reason = "mock left the defect in place"
        return outcome


def _correct_source(task: TaskBundle, mutated: str) -> str:
    """Best-effort repair used only by the mock adapter.

    It re-applies the inverse of the recorded mutation when it recognises the
    mutated line; otherwise it returns the source unchanged.  It is *not* a
    general repair and is never used by a real system.
    """
    inverses = {
        "==": "!=",
        "!=": "==",
        ">": "<=",
        "<=": ">",
        ">=": "<",
        "<": ">=",
        "and": "or",
        "or": "and",
    }
    original_path = task.mutated_source_path.replace("mutated_source.py", "original_source.py")
    if os.path.exists(original_path):
        try:
            with open(original_path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            pass
    del inverses
    return mutated
