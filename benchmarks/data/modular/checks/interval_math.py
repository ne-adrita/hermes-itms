"""Official checks for the ``interval_math`` task family.

Every check is self-contained and asserts exact values, so a partially
correct repair fails.  The values asserted here are deliberately different
from the examples in the docstrings of the module, so that a model cannot
satisfy a check by copying an example.
"""

from __future__ import annotations

from typing import Any


def check_overlap_length(candidate: Any) -> None:
    """The candidate must return the length of the overlap of two intervals."""
    assert candidate(1, 4, 2, 6) == 2
    assert candidate(0, 10, 10, 20) == 0
    assert candidate(0, 5, 10, 20) == 0
    assert candidate(3, 9, 1, 5) == 2
    assert candidate(0, 2, 0, 2) == 2
    assert candidate(7, 7, 7, 7) == 0


def check_merge_intervals(candidate: Any) -> None:
    """The candidate must merge overlapping and touching intervals."""
    assert candidate([(1, 4), (2, 6)]) == [(1, 6)]
    assert candidate([(0, 1), (5, 6), (2, 3)]) == [(0, 1), (2, 3), (5, 6)]
    assert candidate([]) == []
    assert candidate([(0, 10), (3, 4)]) == [(0, 10)]
    assert candidate([(1, 2), (2, 3)]) == [(1, 3)]
    assert candidate([(4, 5), (1, 2), (0, 1)]) == [(0, 2), (4, 5)]


def check_total_covered_length(candidate: Any) -> None:
    """The candidate must return the length of the union of the intervals."""
    assert candidate([(0, 5), (5, 10)]) == 10
    assert candidate([(0, 10), (2, 4)]) == 10
    assert candidate([]) == 0
    assert candidate([(1, 2), (8, 9)]) == 2
    assert candidate([(0, 3), (1, 4), (2, 5)]) == 5
    assert candidate([(0, 1), (1, 2), (2, 3)]) == 3


def check_gap_between(candidate: Any) -> None:
    """The candidate must return the idle time between two intervals."""
    assert candidate(0, 5, 9, 12) == 4
    assert candidate(0, 5, 5, 9) == 0
    assert candidate(0, 5, 2, 9) == 0
    assert candidate(3, 4, 10, 20) == 6
    assert candidate(0, 5, 0, 1) == 0
    assert candidate(1, 2, 9, 10) == 7
