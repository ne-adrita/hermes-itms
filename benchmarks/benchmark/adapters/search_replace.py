"""
Treatment B — search/replace patch.

Contract
--------
*   The model receives the mutated source file and the task instructions.
*   It must return **exactly one** search/replace block::

        <<<<<<< SEARCH
        <exact lines copied verbatim from the current file>
        =======
        <replacement lines>
        >>>>>>> REPLACE

Strictness
----------
The following are rejected rather than "best effort" applied:

*   no block / malformed delimiters        -> ``malformed_patch``
*   more than one block                     -> ``multiple_patch_blocks``
*   the SEARCH text is not present         -> ``search_block_not_found``
*   the SEARCH text occurs more than once  -> ``ambiguous_search_block``
*   the patched file does not parse         -> ``syntax_error``
*   the patched file loses the target function -> ``target_function_missing``

The patch itself is applied with the project's existing
:class:`surgical_splicer.TextDiffPatcher`, so the benchmark measures the real
patch-application behaviour (including its whitespace/ambiguity failure modes)
rather than a re-implementation.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from benchmarks.benchmark.adapters.base import (
    AdapterResult,
    EditingAdapter,
    FailureReason,
    ParseStatus,
)

__all__ = ["SearchReplacePatchAdapter"]

_BLOCK_RE = re.compile(
    r"<<<<<<<\s*SEARCH\s*\r?\n(.*?)\r?\n?=======\s*\r?\n(.*?)\r?\n?>>>>>>>\s*REPLACE",
    re.DOTALL,
)


class SearchReplacePatchAdapter(EditingAdapter):

    name = "search_replace_patch"

    def build_prompt(self, task) -> str:
        return (
            "You are repairing a Python module with a search/replace patch.\n"
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
            "OUTPUT FORMAT: search/replace block\n"
            "-----------------------------------\n"
            "Reply with exactly one block of this form:\n"
            "\n"
            "<<<<<<< SEARCH\n"
            "<the exact lines you want to replace, copied verbatim>\n"
            "=======\n"
            "<the replacement lines>\n"
            ">>>>>>> REPLACE\n"
            "\n"
            "The SEARCH section must appear exactly once in the file. Do not\n"
            "add commentary and do not return more than one block.\n"
        )

    # -- parsing --------------------------------------------------------
    @staticmethod
    def _extract_blocks(response_text: str) -> List[Tuple[str, str]]:
        return [(m.group(1), m.group(2)) for m in _BLOCK_RE.finditer(response_text)]

    def parse_and_apply(self, task, rep: int, response_text: str) -> AdapterResult:
        result = AdapterResult(method=self.name, task_id=task.task_id, rep=rep)
        if not response_text or not response_text.strip():
            result.failure_reason = FailureReason.EMPTY_RESPONSE
            result.parse_error = "the model returned an empty response"
            return result

        blocks = self._extract_blocks(response_text)
        if not blocks:
            result.failure_reason = FailureReason.MALFORMED_PATCH
            result.parse_error = (
                "no '<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE' block found"
            )
            return result
        if len(blocks) > 1:
            result.failure_reason = FailureReason.MULTIPLE_PATCH_BLOCKS
            result.parse_error = (
                f"expected exactly one search/replace block, found {len(blocks)}"
            )
            return result

        search_block, replace_block = blocks[0]
        search_block = search_block.rstrip("\n")
        replace_block = replace_block.rstrip("\n")

        occurrences = task.mutated_source.count(search_block)
        if occurrences == 0:
            result.failure_reason = FailureReason.SEARCH_BLOCK_NOT_FOUND
            result.parse_error = "the SEARCH text does not occur in the current file"
            return result
        if occurrences > 1:
            result.failure_reason = FailureReason.AMBIGUOUS_SEARCH_BLOCK
            result.parse_error = (
                f"the SEARCH text occurs {occurrences} times; the patch is ambiguous"
            )
            return result

        from surgical_splicer import TextDiffPatcher

        ok, patched, error = TextDiffPatcher.apply_search_replace(
            original_source=task.mutated_source,
            search_block=search_block,
            replace_block=replace_block,
        )
        result.detail = {"search_occurrences": occurrences}
        if not ok:
            result.failure_reason = FailureReason.MALFORMED_PATCH
            result.parse_error = error or "the patch could not be applied"
            return result

        result.candidate_source = patched
        result.parse_status = ParseStatus.OK

        syntax_error = self._syntax_error(patched)
        if syntax_error:
            result.failure_reason = FailureReason.SYNTAX_ERROR
            result.parse_error = syntax_error
            return result

        if not self._defines_function(patched, task.target_function):
            result.failure_reason = FailureReason.TARGET_FUNCTION_MISSING
            result.parse_error = (
                f"the patched file does not define '{task.target_function}'"
            )
            return result

        result.applied = True
        result.detail["patched_lines"] = len(patched.splitlines())
        return result
