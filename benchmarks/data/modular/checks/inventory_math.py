"""Official checks for the ``inventory_math`` task family.

Every check is self-contained and asserts exact values, so a partially
correct repair fails.  The values asserted here are deliberately different
from the examples in the docstrings of the module, so that a model cannot
satisfy a check by copying an example.
"""

from __future__ import annotations

from typing import Any


def check_reorder_point(candidate: Any) -> None:
    """The candidate must combine the lead time demand with the safety stock."""
    assert candidate(4.0, 2.0, 10.0) == 18.0
    assert candidate(4.0, 0.0, 10.0) == 10.0
    assert candidate(0.0, 5.0, 7.0) == 7.0
    assert candidate(12.5, 4.0, 0.0) == 50.0
    assert candidate(2.0, 3.0, 1.0) == 7.0
    assert candidate(1.0, -2.0, 4.0) == 4.0


def check_economic_order_quantity(candidate: Any) -> None:
    """The candidate must return the rounded-down economic order quantity."""
    assert candidate(1000.0, 10.0, 2.5) == 89
    assert candidate(500.0, 20.0, 1.0) == 141
    assert candidate(100.0, 5.0, 0.0) == 0
    assert candidate(0.0, 5.0, 1.0) == 0
    assert candidate(2400.0, 30.0, 6.0) == 154


def check_stock_valuation(candidate: Any) -> None:
    """The candidate must value only the sellable part of the stock."""
    assert candidate(50.0, 4.0, 20.0) == 160.0
    assert candidate(200.0, 1.5, 0.0) == 300.0
    assert candidate(10.0, 3.0, 50.0) == 15.0
    assert candidate(0.0, 2.0, 10.0) == 0.0
    assert candidate(30.0, 2.0, 25.0) == 45.0


def check_turnover_ratio(candidate: Any) -> None:
    """The candidate must divide the cost of goods sold by the inventory."""
    assert candidate(500.0, 250.0) == 2.0
    assert candidate(100.0, 0.0) == 0.0
    assert candidate(0.0, 50.0) == 0.0
    assert candidate(750.0, 375.0) == 2.0
    assert candidate(225.0, 150.0) == 1.5
