"""
A *justified* output-token limit for the three editing strategies.

Why the limit must be derived rather than guessed
--------------------------------------------------
The three strategies are not symmetric in the number of tokens they must
*emit*:

*   ``full_file_regeneration`` must re-emit the **whole file**;
*   ``search_replace_patch`` must emit a patch that contains the replaced
    region (bounded by the file, but typically much smaller);
*   ``itms_ast_splice`` must emit **only the target function**.

The largest output any strategy can be obliged to produce is therefore the
whole file.  A limit that is smaller than the largest file truncates the
baseline strategy for a reason that has nothing to do with editing quality:
the 120-attempt live run recorded 6 ``truncated_output`` attempts, 5 of them
in ``itms_ast_splice``, and that difference was a *harness budget* artefact
rather than a property of the method.

The limit used here is therefore derived from the task set:

    limit = ceil(safety_factor * max_file_tokens) + reasoning_allowance

rounded up to a multiple of ``ROUNDING`` and clamped to
``[MIN_LIMIT, MAX_LIMIT]``.  The constants are declared in this module with
their rationale, the resolved value is recorded in the run metadata **and** on
every attempt, and the same value is used by every strategy (there is a single
provider instance per run), so the comparison is not confounded by an
unequal budget.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "SAFETY_FACTOR",
    "REASONING_ALLOWANCE",
    "ROUNDING",
    "MIN_LIMIT",
    "MAX_LIMIT",
    "justified_output_token_limit",
    "resolve_output_token_limit",
    "output_limit_justification",
]

#: Head-room for the model's own output formatting, fences and any
#: re-emission of the target region.  2.0 means the limit is at least twice
#: the largest file, so a full-file rewrite of the biggest task always fits.
SAFETY_FACTOR = 2.0

#: Allowance for provider-side reasoning tokens that are billed as output.
REASONING_ALLOWANCE = 1024

#: The limit is rounded up to a multiple of this many tokens so the value is
#: stable and easy to compare across runs.
ROUNDING = 256

#: Lower bound: below this a single-file rewrite of the largest task could not
#: complete, and the run would measure the harness rather than the methods.
MIN_LIMIT = 2048

#: Upper bound: a limit above this is not a budget, it is an absence of one.
MAX_LIMIT = 32768


def _file_token_counts(tasks: Iterable[Any]) -> List[int]:
    """Local token estimate of each task's mutated source file."""
    from prompt_manager import get_token_count

    counts: List[int] = []
    for task in tasks:
        source = getattr(task, "mutated_source", None)
        if not source:
            continue
        try:
            counts.append(int(get_token_count(source)))
        except Exception:
            continue
    return counts


def justified_output_token_limit(
    tasks: Sequence[Any],
    safety_factor: float = SAFETY_FACTOR,
    reasoning_allowance: int = REASONING_ALLOWANCE,
    rounding: int = ROUNDING,
    minimum: int = MIN_LIMIT,
    maximum: int = MAX_LIMIT,
) -> int:
    """
    Returns the output-token limit justified by the task set.

    The formula is documented in the module docstring.  It never depends on a
    model result, so it cannot be tuned after seeing comparative outcomes.
    """
    counts = _file_token_counts(tasks)
    if not counts:
        return int(minimum)
    largest = max(counts)
    raw = math.ceil(safety_factor * largest) + int(reasoning_allowance)
    stepped = int(math.ceil(raw / rounding) * rounding)
    return int(max(minimum, min(maximum, stepped)))


def output_limit_justification(
    tasks: Sequence[Any],
    resolved_limit: int,
    safety_factor: float = SAFETY_FACTOR,
    reasoning_allowance: int = REASONING_ALLOWANCE,
) -> Dict[str, Any]:
    """Returns the machine-readable justification for a resolved limit."""
    counts = _file_token_counts(tasks)
    largest = max(counts) if counts else 0
    return {
        "resolved_limit": int(resolved_limit),
        "formula": "ceil(safety_factor * max_file_tokens) + reasoning_allowance",
        "safety_factor": safety_factor,
        "reasoning_allowance": int(reasoning_allowance),
        "max_file_tokens": largest,
        "min_file_tokens": min(counts) if counts else 0,
        "tasks_considered": len(counts),
        "applied_to_every_strategy": True,
        "rationale": (
            "The limit is derived from the largest file the baseline strategy "
            "must re-emit, plus an allowance for provider-side reasoning, so a "
            "truncation reflects the task, not the strategy. It is identical "
            "for all three strategies and is recorded per attempt."
        ),
    }


def resolve_output_token_limit(
    tasks: Sequence[Any],
    explicit: Optional[int] = None,
    safety_factor: float = SAFETY_FACTOR,
    reasoning_allowance: int = REASONING_ALLOWANCE,
) -> Dict[str, Any]:
    """
    Resolves the output-token limit.

    ``explicit`` (the operator's ``--max-output-tokens``) always wins, but the
    justification is computed either way so the run metadata states *why* the
    limit is what it is.  ``explicit <= 0`` means "auto".
    """
    if explicit is not None and int(explicit) > 0:
        limit = int(explicit)
        source = "operator_explicit"
    else:
        limit = justified_output_token_limit(
            tasks,
            safety_factor=safety_factor,
            reasoning_allowance=reasoning_allowance,
        )
        source = "justified_from_task_set"
    justification = output_limit_justification(
        tasks, limit, safety_factor=safety_factor, reasoning_allowance=reasoning_allowance
    )
    justification["limit_source"] = source
    return justification
