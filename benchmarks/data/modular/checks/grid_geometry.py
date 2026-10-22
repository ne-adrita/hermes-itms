"""Official checks for the ``grid_geometry`` task family.

Every check is self-contained and asserts exact values, so a partially
correct repair fails.  The values asserted here are deliberately different
from the examples in the docstrings of the module, so that a model cannot
satisfy a check by copying an example.
"""

from __future__ import annotations

from typing import Any


def check_manhattan_distance(candidate: Any) -> None:
    """The candidate must return the sum of the absolute coordinate gaps."""
    assert candidate(0, 0, 1, 1) == 2
    assert candidate(5, 5, 1, 1) == 8
    assert candidate(2, 3, 2, 3) == 0
    assert candidate(-1, -1, 1, 1) == 4
    assert candidate(10, 0, 0, 7) == 17


def check_rectangle_area(candidate: Any) -> None:
    """The candidate must return the area, and zero for a degenerate size."""
    assert candidate(0, 0, 2, 5) == 10
    assert candidate(0, 0, 0, 5) == 0
    assert candidate(1, 1, -2, 4) == 0
    assert candidate(2, 2, 3, 3) == 9
    assert candidate(0, 0, 0, 0) == 0


def check_point_in_rectangle(candidate: Any) -> None:
    """The candidate must report containment with a half-open rectangle."""
    assert candidate(1, 1, 0, 0, 3, 3) is True
    assert candidate(3, 1, 0, 0, 3, 3) is False
    assert candidate(-1, 1, 0, 0, 3, 3) is False
    assert candidate(0, 0, 0, 0, 3, 3) is True
    assert candidate(1, 1, 0, 0, 0, 3) is False
    assert candidate(2, 2, 0, 0, 3, 3) is True


def check_bounding_box(candidate: Any) -> None:
    """The candidate must return the smallest rectangle around the points."""
    assert candidate([(2, 3), (7, 1)]) == (2, 1, 5, 2)
    assert candidate([]) is None
    assert candidate([(0, 0)]) == (0, 0, 0, 0)
    assert candidate([(1, 5), (2, 3), (9, 4)]) == (1, 3, 8, 2)
    assert candidate([(-4, 2), (-4, 6)]) == (-4, 2, 0, 4)
