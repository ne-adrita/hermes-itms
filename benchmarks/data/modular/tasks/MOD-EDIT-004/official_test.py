def check(candidate):
    """The candidate must return the sum of the absolute coordinate gaps."""
    assert candidate(0, 0, 1, 1) == 2
    assert candidate(5, 5, 1, 1) == 8
    assert candidate(2, 3, 2, 3) == 0
    assert candidate(-1, -1, 1, 1) == 4
    assert candidate(10, 0, 0, 7) == 17