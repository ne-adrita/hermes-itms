def check(candidate):
    """The candidate must return the length of the union of the intervals."""
    assert candidate([(0, 5), (5, 10)]) == 10
    assert candidate([(0, 10), (2, 4)]) == 10
    assert candidate([]) == 0
    assert candidate([(1, 2), (8, 9)]) == 2
    assert candidate([(0, 3), (1, 4), (2, 5)]) == 5
    assert candidate([(0, 1), (1, 2), (2, 3)]) == 3