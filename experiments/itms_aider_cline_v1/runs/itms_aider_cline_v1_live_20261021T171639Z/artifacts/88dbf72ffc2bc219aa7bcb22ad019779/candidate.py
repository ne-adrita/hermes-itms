

def largest_divisor(n: int) -> int:
    """ For a given number n, find the largest number that divides n evenly, smaller than n
    >>> largest_divisor(15)
    5
    """
    if n < 0:
        n = -n
    if n == 0:
        raise ValueError("largest_divisor() requires a positive integer")
    # Any divisor of n that is smaller than n is at most n // 2, so scanning
    # downwards from there and returning the first exact divisor yields the
    # largest number that divides n evenly and is smaller than n.
    for i in range(n // 2, 0, -1):
        if n % i == 0:
            return i
    return 1
