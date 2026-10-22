def check(candidate):
    """The candidate must return the trimmed tokens that are long enough."""
    assert candidate("one two  three") == ["one", "two", "three"]
    assert candidate("") == []
    assert candidate("!!! ???") == []
    assert candidate("a bb ccc") == ["bb", "ccc"]
    assert candidate("red, blue; green") == ["red", "blue", "green"]