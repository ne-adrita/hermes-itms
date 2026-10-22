"""Official checks for the ``text_metrics`` task family.

Every check is self-contained: it only uses the standard library and the
callable it receives.  The builder copies the function whose name starts
with ``check_`` into the ``official_test.py`` of a task, renaming it to
``check``.

The checks assert *exact* values.  A partially correct repair therefore
fails the check, which is what makes the check discriminating rather than
merely "does not crash".

The values asserted here are deliberately different from the examples that
appear in the docstrings of the module, so that a model cannot satisfy the
check by copying an example.
"""

from __future__ import annotations

from typing import Any


def check_word_frequencies(candidate: Any) -> None:
    """The candidate must count the words of a text case-insensitively."""
    assert candidate("The cat the CAT") == {"the": 2, "cat": 2}
    assert candidate("") == {}
    assert candidate("a, b; a") == {"a": 2, "b": 1}
    assert candidate("Hello, World!") == {"hello": 1, "world": 1}
    assert candidate("don't stop") == {"don't": 1, "stop": 1}
    assert candidate("42 apples 42 pears") == {"42": 2, "apples": 1, "pears": 1}


def check_longest_word(candidate: Any) -> None:
    """The candidate must return the longest word, first one on a tie."""
    assert candidate("aa bb ccc") == "ccc"
    assert candidate("tie tie") == "tie"
    assert candidate("!!!") is None
    assert candidate("a bb") == "bb"
    assert candidate("q") == "q"
    assert candidate("") is None
    assert candidate("Alpha beta") == "alpha"


def check_vowel_ratio(candidate: Any) -> None:
    """The candidate must return the share of vowels among the letters."""
    assert candidate("hello") == 0.4
    assert candidate("xyz") == 0.0
    assert candidate("") == 0.0
    assert candidate("aeiou") == 1.0
    assert candidate("aebcdfg") == 0.2857
    assert candidate("AEIOU bcd") == 0.625


def check_split_on_whitespace_trimmed(candidate: Any) -> None:
    """The candidate must return the trimmed tokens that are long enough."""
    assert candidate("one two  three") == ["one", "two", "three"]
    assert candidate("") == []
    assert candidate("!!! ???") == []
    assert candidate("a bb ccc") == ["bb", "ccc"]
    assert candidate("red, blue; green") == ["red", "blue", "green"]
