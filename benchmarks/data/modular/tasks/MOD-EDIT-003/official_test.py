def check(candidate):
    """The candidate must return the area, and zero for a degenerate size."""
    assert candidate(0, 0, 2, 5) == 10
    assert candidate(0, 0, 0, 5) == 0
    assert candidate(1, 1, -2, 4) == 0
    assert candidate(2, 2, 3, 3) == 9
    assert candidate(0, 0, 0, 0) == 0