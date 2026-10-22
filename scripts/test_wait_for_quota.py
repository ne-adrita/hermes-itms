"""
Offline tests for the quota poller.

The HTTP call is stubbed in every test, so the suite never contacts
OpenRouter and never consumes a request of any kind.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import wait_for_quota  # noqa: E402
# Imported for its side effect: ``config`` loads the .env file into the
# environment when it is first imported.  That has to happen *before* a test
# removes a variable, otherwise the dotenv load would put it straight back.
from config import AppConfig  # noqa: E402,F401

#: A credential that must never appear in any output.
FAKE_KEY = "sk-or-v1-FAKE-TEST-CREDENTIAL-0123456789"


class _StubResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _StubHTTP:
    """A stand-in for the ``httpx.get`` the poller uses.

    It records the request so a test can prove that the credential *was*
    used for the request header, and it never touches the network.
    """

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({
            "url": url,
            "headers": dict(headers or {}),
            "timeout": timeout,
        })
        return _StubResponse(self.payload, self.status_code)


def _payload(remaining, limit=50, used=680):
    return {
        "data": {
            "label": "sk-or-v1-aaa...bbb",
            "is_free_tier": True,
            "free_model_daily_requests": {
                "used": used,
                "limit": limit,
                "remaining": remaining,
            },
        }
    }


class _EnvGuard:
    """Temporarily replaces an environment variable."""

    def __init__(self, **values):
        self.values = values
        self._saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self._saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc_info):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def _run(argv):
    """Runs the poller and returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = wait_for_quota.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestQuotaPoller(unittest.TestCase):
    """The poller must report the allowance and never leak the credential."""

    def test_exit_0_when_the_remaining_allowance_is_sufficient(self):
        """A sufficient allowance exits 0 and says so."""
        stub = _StubHTTP(_payload(remaining=512))
        with _EnvGuard(OPENROUTER_API_KEY=FAKE_KEY, OPENAI_API_KEY=None), \
                mock.patch.object(wait_for_quota.httpx, "get", stub.get):
            code, out, _err = _run(["--poll-seconds", "0"])
        self.assertEqual(code, 0)
        self.assertIn("QUOTA OK: 512", out)

    def test_exit_1_when_the_allowance_is_short_and_polling_is_off(self):
        """A short allowance with polling off exits 1 and reports the numbers."""
        stub = _StubHTTP(_payload(remaining=0, limit=50, used=680))
        with _EnvGuard(OPENROUTER_API_KEY=FAKE_KEY, OPENAI_API_KEY=None), \
                mock.patch.object(wait_for_quota.httpx, "get", stub.get):
            code, out, _err = _run(["--poll-seconds", "0"])
        self.assertEqual(code, 1)
        self.assertIn("QUOTA LOW: 0/50, used 680", out)

    def test_the_credential_is_never_printed(self):
        """
        The credential may be used for the request header and for nothing
        else: it must not appear in any output line.
        """
        stub = _StubHTTP(_payload(remaining=0))
        with _EnvGuard(OPENROUTER_API_KEY=FAKE_KEY, OPENAI_API_KEY=None), \
                mock.patch.object(wait_for_quota.httpx, "get", stub.get):
            code, out, err = _run(["--poll-seconds", "0"])
        self.assertEqual(code, 1)
        combined = out + err
        self.assertNotIn(FAKE_KEY, combined)
        self.assertNotIn("FAKE-TEST-CREDENTIAL", combined)
        # The key was genuinely used for the request header.
        self.assertTrue(stub.calls, "the poller made no request")
        self.assertEqual(
            stub.calls[0]["headers"].get("Authorization"), f"Bearer {FAKE_KEY}"
        )
        self.assertEqual(stub.calls[0]["url"], wait_for_quota.KEY_URL)

    def test_an_error_message_cannot_leak_the_credential(self):
        """Even an error line is redacted before it is printed."""
        stub = _StubHTTP({}, status_code=500)
        with _EnvGuard(OPENROUTER_API_KEY=FAKE_KEY, OPENAI_API_KEY=None), \
                mock.patch.object(wait_for_quota.httpx, "get", stub.get):
            code, out, err = _run(["--poll-seconds", "0"])
        self.assertEqual(code, 1)
        self.assertNotIn(FAKE_KEY, out + err)

    def test_a_missing_credential_is_reported_without_a_request(self):
        """Without a credential the poller must not attempt a request."""
        stub = _StubHTTP(_payload(remaining=999))
        with _EnvGuard(OPENROUTER_API_KEY=None, OPENAI_API_KEY=None), \
                mock.patch.object(wait_for_quota.httpx, "get", stub.get):
            code, out, _err = _run(["--poll-seconds", "0"])
        self.assertEqual(code, 1)
        self.assertIn("QUOTA ERROR", out)
        self.assertEqual(stub.calls, [])

    def test_every_poll_is_logged_with_a_timestamp(self):
        """Each poll leaves one timestamped line on stdout."""
        stub = _StubHTTP(_payload(remaining=0))
        with _EnvGuard(OPENROUTER_API_KEY=FAKE_KEY, OPENAI_API_KEY=None), \
                mock.patch.object(wait_for_quota.httpx, "get", stub.get):
            _code, out, _err = _run(["--poll-seconds", "0"])
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ")


if __name__ == "__main__":
    unittest.main()
