

def largest_divisor(n: int) -> int:
    """ For a given number n, find the largest number that divides n evenly, smaller than n
    >>> largest_divisor(15)
    5
    """
    import math

    if n <= 1:
        return n

    value = abs(n)

    def is_prime(m: int) -> bool:
        """Deterministic Miller-Rabin primality test (valid for m < 3.3e24)."""
        if m < 2:
            return False
        for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
            if m % p == 0:
                return m == p
        d, r = m - 1, 0
        while d % 2 == 0:
            d //= 2
            r += 1
        for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
            x = pow(a, d, m)
            if x == 1 or x == m - 1:
                continue
            for _ in range(r - 1):
                x = x * x % m
                if x == m - 1:
                    break
            else:
                return False
        return True

    def pollard_rho(m: int, c: int):
        """Return a non-trivial factor of composite m, or None."""
        x = y = 2
        divisor = 1
        while divisor == 1:
            x = (x * x + c) % m
            y = (y * y + c) % m
            y = (y * y + c) % m
            divisor = math.gcd(abs(x - y), m)
        if divisor == m:
            return None
        return divisor

    def smallest_prime_factor(m: int) -> int:
        """Return the smallest prime factor of m (m > 1)."""
        if m % 2 == 0:
            return 2
        if m % 3 == 0:
            return 3
        if is_prime(m):
            return m
        # Trial division over the candidates 6k +/- 1, switching to
        # Pollard's rho for very large composites so the search stays fast.
        limit = 10 ** 6
        tried_rho = False
        d = 5
        while d * d <= m:
            if d > limit and not tried_rho:
                tried_rho = True
                for c in range(1, 64):
                    factor = pollard_rho(m, c)
                    if factor is not None:
                        return min(smallest_prime_factor(factor),
                                   smallest_prime_factor(m // factor))
            if m % d == 0:
                return d
            if m % (d + 2) == 0:
                return d + 2
            d += 6
        return m

    # The largest divisor of n that is smaller than n is n divided by the
    # smallest prime factor of n (a prime n therefore has 1 as the answer).
    return value // smallest_prime_factor(value)
