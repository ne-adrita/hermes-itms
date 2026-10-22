def check(candidate):
    """The candidate must return the smallest rectangle around the points."""
    assert candidate([(2, 3), (7, 1)]) == (2, 1, 5, 2)
    assert candidate([]) is None
    assert candidate([(0, 0)]) == (0, 0, 0, 0)
    assert candidate([(1, 5), (2, 3), (9, 4)]) == (1, 3, 8, 2)
    assert candidate([(-4, 2), (-4, 6)]) == (-4, 2, 0, 4)