def check(candidate):
    """The candidate must report the whole seconds a client has to wait."""
    assert candidate(-1.0, 2.0) == 1
    assert candidate(-3.0, 1.5) == 2
    assert candidate(0.0, 0.0) == -1
    assert candidate(5.0, 1.0) == 1
    assert candidate(-10.0, 4.0) == 3