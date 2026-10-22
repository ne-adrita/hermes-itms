def check(candidate):
    """The candidate must merge overlapping and touching intervals."""
    assert candidate([(1, 4), (2, 6)]) == [(1, 6)]
    assert candidate([(0, 1), (5, 6), (2, 3)]) == [(0, 1), (2, 3), (5, 6)]
    assert candidate([]) == []
    assert candidate([(0, 10), (3, 4)]) == [(0, 10)]
    assert candidate([(1, 2), (2, 3)]) == [(1, 3)]
    assert candidate([(4, 5), (1, 2), (0, 1)]) == [(0, 2), (4, 5)]