"""Official checks for the ``rate_limiting`` task family.

Every check is self-contained and asserts exact values, so a partially
correct repair fails.  The values asserted here are deliberately different
from the examples in the docstrings of the module, so that a model cannot
satisfy a check by copying an example.
"""

from __future__ import annotations

from typing import Any


def check_tokens_to_refill(candidate: Any) -> None:
    """The candidate must refill the bucket without exceeding its capacity."""
    assert candidate(1.0, 4.0, 50.0) == 4.0
    assert candidate(100.0, 4.0, 50.0) == 50.0
    assert candidate(0.0, 4.0, 50.0) == 0.0
    assert candidate(-1.0, 4.0, 50.0) == 0.0
    assert candidate(2.0, 0.0, 50.0) == 0.0
    assert candidate(3.0, 2.0, 50.0) == 6.0


def check_is_request_allowed(candidate: Any) -> None:
    """The candidate must allow a request only when the bucket can pay."""
    assert candidate(10.0, 3.0) is True
    assert candidate(2.0, 3.0) is False
    assert candidate(5.0, 0.0) is True
    assert candidate(0.0, 1.0) is False
    assert candidate(4.0, 4.0) is True


def check_retry_after_seconds(candidate: Any) -> None:
    """The candidate must report the whole seconds a client has to wait."""
    assert candidate(-1.0, 2.0) == 1
    assert candidate(-3.0, 1.5) == 2
    assert candidate(0.0, 0.0) == -1
    assert candidate(5.0, 1.0) == 1
    assert candidate(-10.0, 4.0) == 3


def check_window_utilisation(candidate: Any) -> None:
    """The candidate must return the used share of the allowance."""
    assert candidate(1.0, 3.0) == 0.3333
    assert candidate(75.0, 100.0) == 0.75
    assert candidate(0.0, 10.0) == 0.0
    assert candidate(5.0, 0.0) == 0.0
    assert candidate(1.0, 8.0) == 0.125
