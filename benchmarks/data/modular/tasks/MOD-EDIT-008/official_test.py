def check(candidate):
    """The candidate must return the idle time between two intervals."""
    assert candidate(0, 5, 9, 12) == 4
    assert candidate(0, 5, 5, 9) == 0
    assert candidate(0, 5, 2, 9) == 0
    assert candidate(3, 4, 10, 20) == 6
    assert candidate(0, 5, 0, 1) == 0
    assert candidate(1, 2, 9, 10) == 7