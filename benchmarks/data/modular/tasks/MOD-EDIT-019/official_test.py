def check(candidate):
    """The candidate must count the words of a text case-insensitively."""
    assert candidate("The cat the CAT") == {"the": 2, "cat": 2}
    assert candidate("") == {}
    assert candidate("a, b; a") == {"a": 2, "b": 1}
    assert candidate("Hello, World!") == {"hello": 1, "world": 1}
    assert candidate("don't stop") == {"don't": 1, "stop": 1}
    assert candidate("42 apples 42 pears") == {"42": 2, "apples": 1, "pears": 1}