def check(candidate):
    """The candidate must return the share of vowels among the letters."""
    assert candidate("hello") == 0.4
    assert candidate("xyz") == 0.0
    assert candidate("") == 0.0
    assert candidate("aeiou") == 1.0
    assert candidate("aebcdfg") == 0.2857
    assert candidate("AEIOU bcd") == 0.625