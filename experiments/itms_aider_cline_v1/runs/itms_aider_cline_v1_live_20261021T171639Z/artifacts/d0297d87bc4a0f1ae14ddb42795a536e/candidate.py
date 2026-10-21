
FIX = """
Add more test cases.
"""

def vowels_count(s):
    """Write a function vowels_count which takes a string representing
    a word as input and returns the number of vowels in the string.
    Vowels in this case are 'a', 'e', 'i', 'o', 'u'. Here, 'y' is also a
    vowel, but only when it is at the end of the given word.

    Example:
    >>> vowels_count("abcde")
    2
    >>> vowels_count("ACEDY")
    3
    """
    vowels = "aeiouAEIOU"
    n_vowels = sum(c in vowels for c in s)
    if s and s[-1] in ('y', 'Y'):
        n_vowels += 1
    return n_vowels


def test_vowels_count():
    # Basic counting of the five standard vowels, in both cases.
    assert vowels_count("abcde") == 2
    assert vowels_count("AEIOU") == 5
    assert vowels_count("aAeEiIoOuU") == 10
    assert vowels_count("beautiful") == 5
    assert vowels_count("HELLO") == 2

    # No vowels at all.
    assert vowels_count("bcdfg") == 0
    assert vowels_count("rhythm") == 0
    assert vowels_count("") == 0

    # 'y'/'Y' counts as a vowel only as the last character of the word.
    assert vowels_count("y") == 1
    assert vowels_count("Y") == 1
    assert vowels_count("ACEDY") == 3
    assert vowels_count("python") == 1
    assert vowels_count("my") == 1
    assert vowels_count("yy") == 1
    assert vowels_count("yYy") == 1
    assert vowels_count("xyz") == 0
    assert vowels_count("xyzzy") == 1
    assert vowels_count("myth") == 0
