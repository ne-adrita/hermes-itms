def check(candidate):
    """The candidate must return the rounded-down economic order quantity."""
    assert candidate(1000.0, 10.0, 2.5) == 89
    assert candidate(500.0, 20.0, 1.0) == 141
    assert candidate(100.0, 5.0, 0.0) == 0
    assert candidate(0.0, 5.0, 1.0) == 0
    assert candidate(2400.0, 30.0, 6.0) == 154