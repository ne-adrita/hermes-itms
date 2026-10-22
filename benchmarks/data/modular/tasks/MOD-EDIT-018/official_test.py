def check(candidate):
    """The candidate must return the used share of the allowance."""
    assert candidate(1.0, 3.0) == 0.3333
    assert candidate(75.0, 100.0) == 0.75
    assert candidate(0.0, 10.0) == 0.0
    assert candidate(5.0, 0.0) == 0.0
    assert candidate(1.0, 8.0) == 0.125