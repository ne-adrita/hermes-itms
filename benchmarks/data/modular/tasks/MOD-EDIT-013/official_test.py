def check(candidate):
    """The candidate must divide the cost of goods sold by the inventory."""
    assert candidate(500.0, 250.0) == 2.0
    assert candidate(100.0, 0.0) == 0.0
    assert candidate(0.0, 50.0) == 0.0
    assert candidate(750.0, 375.0) == 2.0
    assert candidate(225.0, 150.0) == 1.5