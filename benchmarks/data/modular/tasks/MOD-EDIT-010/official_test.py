def check(candidate):
    """The candidate must return the longest word, first one on a tie."""
    assert candidate("aa bb ccc") == "ccc"
    assert candidate("tie tie") == "tie"
    assert candidate("!!!") is None
    assert candidate("a bb") == "bb"
    assert candidate("q") == "q"
    assert candidate("") is None
    assert candidate("Alpha beta") == "alpha"