"""
Common interface for the three editing-method adapters.

An adapter owns two things:

1.  the **prompt contract** — how the task is presented to the model for this
    editing treatment, and
2.  the **output contract** — how the model's response is parsed and applied to
    the task's mutated source.

The adapter returns a :class:`AdapterResult`.  It never runs the official test
itself; the pipeline does that, so every method is evaluated by exactly the
same mechanism.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = ["AdapterResult", "EditingAdapter", "ParseStatus", "FailureReason"]


class ParseStatus:
    OK = "ok"
    ERROR = "error"


class FailureReason:
    """Normalised failure categories produced by the adapters."""

    NONE = None
    NO_CODE_BLOCK = "no_code_block"
    MULTIPLE_CODE_BLOCKS = "multiple_code_blocks"
    EMPTY_RESPONSE = "empty_response"
    MALFORMED_PATCH = "malformed_patch"
    MULTIPLE_PATCH_BLOCKS = "multiple_patch_blocks"
    SEARCH_BLOCK_NOT_FOUND = "search_block_not_found"
    AMBIGUOUS_SEARCH_BLOCK = "ambiguous_search_block"
    TARGET_FUNCTION_MISSING = "target_function_missing"
    SYNTAX_ERROR = "syntax_error"
    SPLICE_FAILED = "splice_failed"
    UNEXPECTED_EXTRA_EDITS = "unexpected_extra_edits"


@dataclass
class AdapterResult:
    """Outcome of applying one model response to one task."""

    method: str
    task_id: str
    rep: int
    candidate_source: Optional[str] = None
    parse_status: str = ParseStatus.ERROR
    parse_error: Optional[str] = None
    applied: bool = False
    failure_reason: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "task_id": self.task_id,
            "rep": self.rep,
            "parse_status": self.parse_status,
            "parse_error": self.parse_error,
            "applied": self.applied,
            "failure_reason": self.failure_reason,
            "detail": dict(self.detail),
            "candidate_source_sha256": (
                hash(self.candidate_source) if self.candidate_source else None
            ),
        }


class EditingAdapter:
    """Base class for the three editing treatments."""

    name: str = "abstract"

    def build_prompt(self, task) -> str:
        """Build the method-specific prompt for ``task``."""
        raise NotImplementedError

    def parse_and_apply(self, task, rep: int, response_text: str) -> AdapterResult:
        """Parse the model response and apply it to the task's source."""
        raise NotImplementedError

    # -- shared helpers -------------------------------------------------
    @staticmethod
    def _syntax_error(source: Optional[str]) -> Optional[str]:
        if source is None:
            return "candidate source is missing"
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return f"SyntaxError at line {exc.lineno}: {exc.msg}"
        return None

    @staticmethod
    def _defines_function(source: str, target: str) -> bool:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
                return True
        return False
