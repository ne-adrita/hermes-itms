"""
Treatment C — ITMS AST splice.

This adapter uses the project's **existing** ITMS implementation:

*   :func:`skeletonizer.skeletonize_code` builds the context skeleton that is
    shown to the model,
*   :class:`surgical_splicer.ASTNodeLocator` locates the target node,
*   :class:`surgical_splicer.ASTSurgicalSplicer` splices the model's
    replacement into the file.

Nothing here re-implements the splice: the benchmark measures the real ITMS
behaviour, including its known limitations.

Known limitation (documented, **not** fixed in Phase 1)
-------------------------------------------------------
``ASTSurgicalSplicer.splice()`` mis-handles a *body-only* replacement that
itself declares a nested function: the nested ``def`` is hoisted to module
level and the original body is overwritten.  The spliced file stays
syntactically valid but is semantically wrong, and ``splice()`` still reports
success.  See ``BENCHMARK_READINESS_AUDIT.md`` §3.  The limitation is
preserved on purpose so the benchmark measures the current implementation.
"""

from __future__ import annotations

from typing import Optional

from benchmarks.benchmark.adapters.base import (
    AdapterResult,
    EditingAdapter,
    FailureReason,
    ParseStatus,
)

__all__ = ["ItmsAstSpliceAdapter"]


class ItmsAstSpliceAdapter(EditingAdapter):

    name = "itms_ast_splice"

    def __init__(self, strip_docstrings: bool = True, prune_unreachable: bool = True):
        self.strip_docstrings = strip_docstrings
        self.prune_unreachable = prune_unreachable

    # -- prompt ---------------------------------------------------------
    def build_prompt(self, task) -> str:
        from skeletonizer import skeletonize_code

        skeleton = skeletonize_code(
            source_code=task.mutated_source,
            target_function_name=task.target_function,
            strip_docstrings=self.strip_docstrings,
            prune_unreachable=self.prune_unreachable,
            task_description=task.repair_instruction,
        )
        return (
            "You are repairing a Python module with a surgical AST edit.\n"
            "\n"
            "TASK INSTRUCTIONS\n"
            "---------------\n"
            f"{task.repair_instruction}\n"
            "\n"
            "PROBLEM STATEMENT\n"
            "-----------------\n"
            f"{task.problem_prompt}\n"
            "\n"
            "CONTEXT SKELETON\n"
            "-----------------\n"
            "----- BEGIN FILE -----\n"
            f"{skeleton.rstrip()}\n"
            "----- END FILE -----\n"
            "\n"
            f"TARGET NODE: {task.target_function}\n"
            "\n"
            "OUTPUT FORMAT: single fenced python block\n"
            "---------------------------------------\n"
            "Return ONLY the replacement for the target function, inside a\n"
            "single fenced python code block. Either return the complete\n"
            "function definition (starting with 'def <name>(...)') or only its\n"
            "indented body. Do not modify any other part of the file and do\n"
            "not add commentary.\n"
        )

    # -- application ----------------------------------------------------
    def parse_and_apply(self, task, rep: int, response_text: str) -> AdapterResult:
        from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer

        result = AdapterResult(method=self.name, task_id=task.task_id, rep=rep)
        if not response_text or not response_text.strip():
            result.failure_reason = FailureReason.EMPTY_RESPONSE
            result.parse_error = "the model returned an empty response"
            return result

        replacement = ASTSurgicalSplicer.clean_replacement_code(response_text)
        if not replacement.strip():
            result.failure_reason = FailureReason.NO_CODE_BLOCK
            result.parse_error = "no replacement code could be extracted from the response"
            return result

        target = ASTNodeLocator.locate_target(task.mutated_source, task.target_function)
        if target is None:
            result.failure_reason = FailureReason.SPLICE_FAILED
            result.parse_error = (
                f"the target '{task.target_function}' could not be located in the file"
            )
            return result

        ok, spliced, error = ASTSurgicalSplicer.splice(
            original_source=task.mutated_source,
            target=target,
            replacement_code=replacement,
        )
        result.detail = {
            "replacement_is_body_only": not replacement.lstrip().startswith(("def ", "async def ", "class ", "@")),
            "target_qualified_name": target.qualified_name,
        }
        if not ok:
            result.failure_reason = FailureReason.SPLICE_FAILED
            result.parse_error = error or "the splice failed"
            return result

        result.candidate_source = spliced
        result.parse_status = ParseStatus.OK

        syntax_error = self._syntax_error(spliced)
        if syntax_error:
            result.failure_reason = FailureReason.SYNTAX_ERROR
            result.parse_error = syntax_error
            return result

        if not self._defines_function(spliced, task.target_function):
            result.failure_reason = FailureReason.TARGET_FUNCTION_MISSING
            result.parse_error = (
                f"the spliced file does not define '{task.target_function}'"
            )
            return result

        result.applied = True
        result.detail["spliced_lines"] = len(spliced.splitlines())
        return result
