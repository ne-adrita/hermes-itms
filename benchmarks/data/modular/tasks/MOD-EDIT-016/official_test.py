def check(candidate):
    """The candidate must allow a request only when the bucket can pay."""
    assert candidate(10.0, 3.0) is True
    assert candidate(2.0, 3.0) is False
    assert candidate(5.0, 0.0) is True
    assert candidate(0.0, 1.0) is False
    assert candidate(4.0, 4.0) is True