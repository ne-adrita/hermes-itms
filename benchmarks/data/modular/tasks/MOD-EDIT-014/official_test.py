def check(candidate):
    """The candidate must combine the lead time demand with the safety stock."""
    assert candidate(4.0, 2.0, 10.0) == 18.0
    assert candidate(4.0, 0.0, 10.0) == 10.0
    assert candidate(0.0, 5.0, 7.0) == 7.0
    assert candidate(12.5, 4.0, 0.0) == 50.0
    assert candidate(2.0, 3.0, 1.0) == 7.0
    assert candidate(1.0, -2.0, 4.0) == 4.0