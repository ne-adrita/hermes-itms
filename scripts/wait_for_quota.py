#!/usr/bin/env python3
"""
Waits for the OpenRouter free-model daily allowance to refill.

The 100-run macro benchmark cannot start while the free-model allowance is
exhausted: every request would be answered with a quota error, and the
harness would faithfully record 100 failures. This script watches the
allowance and tells the operator when the run may start.

It performs **one metadata request per poll** (``GET /v1/key``). It makes no
model request and consumes no inference budget.

The credential is used only for the request header. It is never printed, never
logged and never returned; any error text is passed through a redaction step
before it reaches the output.

Usage::

    python scripts/wait_for_quota.py                  # check once
    python scripts/wait_for_quota.py --poll-seconds 1800 --max-wait-seconds 86400

Exit codes:
    0  the allowance is sufficient (remaining >= the required amount),
    1  the allowance is still short (or the wait timed out),
    2  the allowance could not be read at all.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import httpx

#: The endpoint that reports the key's own limits and usage.
KEY_URL = "https://openrouter.ai/api/v1/key"

#: The number of free-model requests the 100-run benchmark needs (the worst
#: case is 4 requests per run: Tier 1, two Tier 1 self-repair retries and a
#: Tier 2 escalation).
REQUIRED_REMAINING = 400

#: Default poll interval and maximum wait.
DEFAULT_POLL_SECONDS = 1800
DEFAULT_MAX_WAIT_SECONDS = 86400

#: The text that replaces the credential if it ever appears in output.
REDACTION = "[REDACTED]"


def _api_key() -> Optional[str]:
    """
    Returns the configured OpenRouter credential, or ``None``.

    The value is only ever used for the request header.  It is never
    printed, logged or returned to a caller that displays it.
    """
    from config import AppConfig

    key = AppConfig.resolve_api_key("openrouter")
    if not key or not str(key).strip():
        return None
    return str(key).strip()


def read_quota() -> Dict[str, Any]:
    """
    Reads the key's free-model allowance.

    Returns the ``data`` object of the response.  The credential is not part
    of the return value.
    """
    key = _api_key()
    if not key:
        raise RuntimeError(
            "no OpenRouter credential is configured; the quota cannot be read"
        )
    response = httpx.get(
        KEY_URL,
        headers={"Authorization": f"Bearer {key}"},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("the quota response carried no 'data' object")
    return data


def quota_state(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts the free-model daily allowance from a ``data`` object."""
    block = data.get("free_model_daily_requests") or {}
    try:
        remaining = int(block.get("remaining") or 0)
    except (TypeError, ValueError):
        remaining = 0
    return {
        "remaining": remaining,
        "limit": block.get("limit"),
        "used": block.get("used"),
    }


def format_status(state: Dict[str, Any], required: int = REQUIRED_REMAINING) -> str:
    """The one-line status of an allowance."""
    if state["remaining"] >= required:
        return f"QUOTA OK: {state['remaining']}"
    return (
        f"QUOTA LOW: {state['remaining']}/{state['limit']}, "
        f"used {state['used']}"
    )


def _redact(text: str) -> str:
    """Replaces the credential in a message, so it can never be printed."""
    key = _api_key()
    if key and key in text:
        return text.replace(key, REDACTION)
    return text


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: Optional[list] = None) -> int:
    """
    Polls the allowance until it is sufficient or the wait times out.

    ``--poll-seconds 0`` performs a single check and returns.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Wait for the OpenRouter free-model daily allowance to reach the "
            "number of requests the benchmark needs. Makes one metadata "
            "request per poll and no model request at all."
        )
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
        help=(
            "seconds between polls; 0 checks once and returns "
            f"(default: {DEFAULT_POLL_SECONDS})"
        ),
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=DEFAULT_MAX_WAIT_SECONDS,
        help=(
            "give up after this many seconds "
            f"(default: {DEFAULT_MAX_WAIT_SECONDS})"
        ),
    )
    parser.add_argument(
        "--required-remaining",
        type=int,
        default=REQUIRED_REMAINING,
        help=(
            "the number of remaining free-model requests the run needs "
            f"(default: {REQUIRED_REMAINING})"
        ),
    )
    args = parser.parse_args(argv)

    deadline = time.monotonic() + max(0, int(args.max_wait_seconds))

    while True:
        try:
            state = quota_state(read_quota())
            status = format_status(state, args.required_remaining)
        except Exception as exc:
            state = None
            status = _redact(f"QUOTA ERROR: {type(exc).__name__}: {exc}")
        print(f"{_stamp()} {status}", flush=True)

        if state is not None and state["remaining"] >= args.required_remaining:
            return 0
        if args.poll_seconds <= 0:
            return 1
        now = time.monotonic()
        if now >= deadline:
            print(
                f"{_stamp()} QUOTA TIMEOUT: waited "
                f"{args.max_wait_seconds}s without reaching "
                f"{args.required_remaining} remaining requests",
                flush=True,
            )
            return 1
        time.sleep(min(args.poll_seconds, max(0.0, deadline - now)))


if __name__ == "__main__":
    raise SystemExit(main())
