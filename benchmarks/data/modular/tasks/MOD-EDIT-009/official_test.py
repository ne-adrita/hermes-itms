def check(candidate):
    """The candidate must return the length of the overlap of two intervals."""
    assert candidate(1, 4, 2, 6) == 2
    assert candidate(0, 10, 10, 20) == 0
    assert candidate(0, 5, 10, 20) == 0
    assert candidate(3, 9, 1, 5) == 2
    assert candidate(0, 2, 0, 2) == 2
    assert candidate(7, 7, 7, 7) == 0