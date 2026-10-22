def check(candidate):
    """The candidate must value only the sellable part of the stock."""
    assert candidate(50.0, 4.0, 20.0) == 160.0
    assert candidate(200.0, 1.5, 0.0) == 300.0
    assert candidate(10.0, 3.0, 50.0) == 15.0
    assert candidate(0.0, 2.0, 10.0) == 0.0
    assert candidate(30.0, 2.0, 25.0) == 45.0