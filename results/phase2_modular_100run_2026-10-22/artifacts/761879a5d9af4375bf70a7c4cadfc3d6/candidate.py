"""Rate limiting arithmetic for token-bucket style traffic control.

The module contains the formulas that a request limiter needs in order to
decide whether a request may pass, how much the bucket has to be refilled
and how long a rejected client has to wait.

A token bucket holds at most ``capacity`` tokens, refills at a constant
rate and spends a fixed number of tokens per request.  The functions of this
module are the pure arithmetic behind that policy; the :class:`TokenBucket`
class keeps the state of one bucket.

Nothing in the module reads a clock.  The caller passes the elapsed time in,
so every function is deterministic and easy to test.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "TokenBucket",
    "tokens_to_refill",
    "is_request_allowed",
    "retry_after_seconds",
    "window_utilisation",
    "clamp_bucket",
    "burst_allowance",
    "average_rate",
]

def tokens_to_refill(
    elapsed_seconds: float,
    rate_per_second: float,
    capacity: float,
) -> float:
    """Return how many tokens the bucket regains during ``elapsed_seconds``.

    The bucket regains ``elapsed_seconds * rate_per_second`` tokens, but it
    never holds more than ``capacity`` tokens.  A negative elapsed time
    regains nothing, and a non-positive rate never refills the bucket.

    >>> tokens_to_refill(2.0, 5.0, 100.0)
    10.0
    """
    if elapsed_seconds <= 0 or rate_per_second <= 0:
        return 0
    gained = elapsed_seconds * rate_per_second
    if gained > capacity:
        return capacity
    return gained


def is_request_allowed(bucket_tokens: float, request_cost: float) -> bool:
    """Return whether a request of ``request_cost`` tokens may pass.

    A request is allowed when the bucket holds at least as many tokens as
    the request costs.  A request that costs nothing is always allowed.

    >>> is_request_allowed(5.0, 2.0)
    True
    """
    if request_cost <= 0:
        return True
    return bucket_tokens >= request_cost


def retry_after_seconds(remaining_tokens: float, rate_per_second: float) -> int:
    """Return how many whole seconds a rejected client has to wait.

    The delay is the time the bucket needs to regain the missing tokens,
    rounded up to a whole second.  A non-positive rate means the bucket
    never refills, so the delay is unbounded and is reported as ``-1``.  The
    delay is never smaller than one second.

    >>> retry_after_seconds(0.0, 2.0)
    1
    """
    if rate_per_second <= 0:
        return -1
    missing = -remaining_tokens
    if missing <= 0:
        missing = 0
    seconds = int(math.ceil(missing / rate_per_second))
    if seconds < 1:
        return 1
    return seconds


def window_utilisation(used: float, limit: float) -> float:
    """Return how much of a window's allowance has been used.

    The result is the share of the limit that has been consumed and is
    rounded to four decimal places.  A non-positive limit has no
    utilisation, so the result is ``0.0``.

    >>> window_utilisation(25.0, 100.0)
    0.25
    """
    if limit <= 0:
        return 0.0
    return round(used / limit, 4)


def clamp_bucket(tokens: float, capacity: float) -> float:
    """Return the number of tokens a bucket holds after clamping.

    The result never exceeds the capacity and is never negative.

    >>> clamp_bucket(150.0, 100.0)
    100.0
    """
    if tokens < 0:
        return 0
    if tokens > capacity:
        return capacity
    return tokens


def burst_allowance(capacity: float, steady_rate: float, burst_seconds: float) -> float:
    """Return how many tokens a bucket may spend in one burst.

    The allowance is the number of tokens the bucket accumulates during
    ``burst_seconds`` at the steady rate, but never more than the capacity
    of the bucket.

    >>> burst_allowance(100.0, 10.0, 3.0)
    30.0
    """
    if burst_seconds <= 0 or steady_rate <= 0:
        return 0
    allowance = steady_rate * burst_seconds
    if allowance > capacity:
        return capacity
    return allowance


def average_rate(count: int, window_seconds: float) -> float:
    """Return the mean number of events per second in a window.

    A non-positive window yields ``0.0``.

    >>> average_rate(30, 60)
    0.5
    """
    if window_seconds <= 0:
        return 0.0
    return count / window_seconds


class TokenBucket:
    """The state of one token bucket."""

    def __init__(self, capacity: float, rate_per_second: float) -> None:
        self.capacity = capacity
        self.rate_per_second = rate_per_second
        self.tokens = capacity
        self.last_refill = 0.0

    def refill(self, now: float) -> float:
        """Regain the tokens that accrued since the last refill.

        ``now`` is a monotonic timestamp.  The method returns the number of
        tokens that were added.
        """
        elapsed = now - self.last_refill
        gained = tokens_to_refill(elapsed, self.rate_per_second, self.capacity)
        self.tokens = clamp_bucket(self.tokens + gained, self.capacity)
        self.last_refill = now
        return gained

    def consume(self, request_cost: float) -> bool:
        """Spend ``request_cost`` tokens and report whether it was allowed."""
        if not is_request_allowed(self.tokens, request_cost):
            return False
        self.tokens = self.tokens - request_cost
        return True

    def available(self) -> float:
        """Return the number of tokens currently in the bucket."""
        return self.tokens

    def retry_after(self) -> int:
        """Return the delay a rejected client has to wait."""
        return retry_after_seconds(self.tokens, self.rate_per_second)

    def history(self, window_seconds: float) -> List[float]:
        """Return the token levels of the last ``window_seconds``."""
        levels: List[float] = []
        steps = 4
        for index in range(steps + 1):
            levels.append(self.tokens)
        return levels

    def summary(self) -> Dict[str, object]:
        """Return a dictionary with the state of the bucket."""
        return {
            "capacity": self.capacity,
            "tokens": self.tokens,
            "rate_per_second": self.rate_per_second,
        }

    def __repr__(self) -> str:
        return f"TokenBucket(capacity={self.capacity}, tokens={self.tokens})"
