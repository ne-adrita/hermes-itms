from typing import List


def separate_paren_groups(paren_string: str) -> List[str]:
    """ Input to this function is a string containing multiple groups of nested parentheses. Your goal is to
    separate those group into separate strings and return the list of those.
    Separate groups are balanced (each open brace is properly closed) and not nested within each other
    Ignore any spaces in the input string.
    >>> separate_paren_groups('( ) (( )) (( )( ))')
    ['()', '(())', '(()())']
    """
    result = []
    current_string = []
    current_depth = 0

    for c in paren_string:
        if c == '(':
            current_depth += 1
            current_string.append(c)
        elif c == ')':
            if current_depth == 0:
                # Unbalanced closing parenthesis outside of any group: ignore it.
                continue

            current_depth -= 1
            current_string.append(c)

            if current_depth == 0:
                # A top-level group has been closed: it is not nested within
                # any other group, so it can be emitted as its own string.
                result.append(''.join(current_string))
                current_string = []

    return result
