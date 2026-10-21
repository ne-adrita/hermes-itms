

def largest_divisor(n: int) -> int:
    """ For a given number n, find the largest number that divides n evenly, smaller than n
    >>> largest_divisor(15)
    5
    """
    if n < 2:
        return 1
    factor = 2
    while factor * factor <= n:
        if n % factor == 0:
            return n // factor
        factor += 1
    return 1
