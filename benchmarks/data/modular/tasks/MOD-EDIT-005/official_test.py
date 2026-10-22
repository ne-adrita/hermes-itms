def check(candidate):
    """The candidate must refill the bucket without exceeding its capacity."""
    assert candidate(1.0, 4.0, 50.0) == 4.0
    assert candidate(100.0, 4.0, 50.0) == 50.0
    assert candidate(0.0, 4.0, 50.0) == 0.0
    assert candidate(-1.0, 4.0, 50.0) == 0.0
    assert candidate(2.0, 0.0, 50.0) == 0.0
    assert candidate(3.0, 2.0, 50.0) == 6.0