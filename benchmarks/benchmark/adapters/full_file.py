"""
Treatment A — full-file regeneration.

Contract
--------
*   The model receives the mutated source file and the task instructions.
*   It must return the **complete corrected file** inside exactly one fenced
    Python code block.
*   The returned block becomes the candidate module verbatim.

Strictness
----------
*   no code block            -> ``no_code_block``
*   more than one code block -> ``multiple_code_blocks``
*   empty response           -> ``empty_response``
*   block does not define the target function -> ``target_function_missing``
*   block does not parse     -> ``syntax_error``
"""

from __future__ import annotations

import ast
import re
from typing import List

from benchmarks.benchmark.adapters.base import (
    AdapterResult,
    EditingAdapter,
    FailureReason,
    ParseStatus,
)

__all__ = ["FullFileRegenerationAdapter"]

_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)


class FullFileRegenerationAdapter(EditingAdapter):

    name = "full_file_regeneration"

    def build_prompt(self, task) -> str:
        return (
            "You are repairing a Python module.\n"
            "\n"
            "TASK INSTRUCTIONS\n"
            "---------------\n"
            f"{task.repair_instruction}\n"
            "\n"
            "PROBLEM STATEMENT\n"
            "-----------------\n"
            f"{task.problem_prompt}\n"
            "\n"
            "CURRENT FILE (contains the defect)\n"
            "----------------------------------\n"
            "----- BEGIN FILE -----\n"
            f"{task.mutated_source.rstrip()}\n"
            "----- END FILE -----\n"
            "\n"
            "OUTPUT FORMAT: single fenced python block\n"
            "---------------------------------------\n"
            "Return the complete corrected file. Reply with exactly one fenced\n"
            "python code block that contains the whole module, including its\n"
            "imports, the target function and its docstring. Do not add any\n"
            "commentary, explanation or extra code blocks.\n"
        )

    def parse_and_apply(self, task, rep: int, response_text: str) -> AdapterResult:
        result = AdapterResult(method=self.name, task_id=task.task_id, rep=rep)
        if not response_text or not response_text.strip():
            result.failure_reason = FailureReason.EMPTY_RESPONSE
            result.parse_error = "the model returned an empty response"
            return result

        blocks: List[str] = [m.group(2) for m in _FENCE_RE.finditer(response_text)]
        if not blocks:
            result.failure_reason = FailureReason.NO_CODE_BLOCK
            result.parse_error = "no fenced python code block found in the response"
            return result
        if len(blocks) > 1:
            result.failure_reason = FailureReason.MULTIPLE_CODE_BLOCKS
            result.parse_error = (
                f"expected exactly one fenced code block, found {len(blocks)}"
            )
            return result

        candidate = blocks[0].strip("\n")
        if not candidate.strip():
            result.failure_reason = FailureReason.EMPTY_RESPONSE
            result.parse_error = "the returned code block was empty"
            return result

        result.candidate_source = candidate + "\n"
        result.parse_status = ParseStatus.OK

        syntax_error = self._syntax_error(result.candidate_source)
        if syntax_error:
            result.failure_reason = FailureReason.SYNTAX_ERROR
            result.parse_error = syntax_error
            return result

        if not self._defines_function(result.candidate_source, task.target_function):
            result.failure_reason = FailureReason.TARGET_FUNCTION_MISSING
            result.parse_error = (
                f"the returned file does not define '{task.target_function}'"
            )
            return result

        result.applied = True
        result.detail = {
            "returned_lines": len(result.candidate_source.splitlines()),
            "target_function": task.target_function,
        }
        return result
