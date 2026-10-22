def check(candidate):
    """The candidate must report containment with a half-open rectangle."""
    assert candidate(1, 1, 0, 0, 3, 3) is True
    assert candidate(3, 1, 0, 0, 3, 3) is False
    assert candidate(-1, 1, 0, 0, 3, 3) is False
    assert candidate(0, 0, 0, 0, 3, 3) is True
    assert candidate(1, 1, 0, 0, 0, 3) is False
    assert candidate(2, 2, 0, 0, 3, 3) is True