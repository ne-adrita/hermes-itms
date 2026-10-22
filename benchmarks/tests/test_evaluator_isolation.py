"""
Offline tests for the isolation of the official-evaluation subprocess.

The tests run the *real* official evaluator
(:func:`benchmarks.phase0.runner.run_official_test`) with the Seatbelt
isolation enabled and verify that:

*   a correct solution still passes (the isolation must not break the
    evaluator),
*   the evaluated code cannot read the project's credential file,
*   the evaluated code cannot open a network connection,
*   the evaluated code cannot write into the project directory,
*   the isolation level is reported truthfully.

No network request is made by any of these tests: the evaluated code is
local, and the connection attempt is expected to fail.
"""

from __future__ import annotations

import os

import pytest

from benchmarks.phase0.runner import isolation_description, run_official_test
from benchmarks.phase0.sandbox_profile import (
    ISOLATION_SEATBELT,
    sandbox_available,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CREDENTIAL_FILE = os.path.join(REPO_ROOT, ".env")


@pytest.fixture()
def seatbelt(monkeypatch):
    if not sandbox_available():
        pytest.skip("no Seatbelt sandbox on this host")
    monkeypatch.setenv("ITMS_EVAL_ISOLATION", "seatbelt")
    return True


def _probe_source(body: str) -> str:
    return f"PROBE = None\n{body}\n"


def test_isolation_level_is_reported(seatbelt):
    described = isolation_description()
    assert described["level"] == ISOLATION_SEATBELT
    assert described["seatbelt_available"] is True
    assert any("Seatbelt" in item for item in described["enforced"])
    assert described["not_enforced"]


def test_a_correct_solution_still_passes_under_isolation(seatbelt):
    with open(
        os.path.join(REPO_ROOT, "benchmarks", "data", "tasks", "HE-EDIT-006", "original_source.py"),
        encoding="utf-8",
    ) as handle:
        source = handle.read()
    with open(
        os.path.join(REPO_ROOT, "benchmarks", "data", "tasks", "HE-EDIT-006", "official_test.py"),
        encoding="utf-8",
    ) as handle:
        test = handle.read()
    outcome = run_official_test(source, test, "below_zero", timeout=30.0)
    assert outcome.passed is True, outcome.stderr


def test_the_evaluated_code_cannot_read_the_credential_file(seatbelt):
    probe = f"""
import os
def check(candidate):
    try:
        with open({CREDENTIAL_FILE!r}, 'rb') as handle:
            handle.read(1)
        print('CREDENTIAL_READABLE')
    except Exception as exc:
        print('CREDENTIAL_BLOCKED', type(exc).__name__)
    return True
"""
    outcome = run_official_test("value = 1\n", probe, "value", timeout=30.0)
    assert "CREDENTIAL_BLOCKED" in outcome.stdout
    assert "CREDENTIAL_READABLE" not in outcome.stdout


def test_the_evaluated_code_cannot_write_into_the_project(seatbelt):
    target = os.path.join(REPO_ROOT, "isolation_probe_should_not_exist.txt")
    probe = f"""
import os
def check(candidate):
    try:
        with open({target!r}, 'w') as handle:
            handle.write('x')
        print('WRITE_ALLOWED')
        os.unlink({target!r})
    except Exception as exc:
        print('WRITE_BLOCKED', type(exc).__name__)
    return True
"""
    outcome = run_official_test("value = 1\n", probe, "value", timeout=30.0)
    assert "WRITE_BLOCKED" in outcome.stdout
    assert not os.path.exists(target)


def test_the_evaluated_code_cannot_reach_the_network(seatbelt):
    probe = """
import socket
def check(candidate):
    try:
        sock = socket.socket()
        sock.settimeout(2)
        sock.connect(('1.1.1.1', 80))
        print('NETWORK_CONNECTED')
    except Exception as exc:
        print('NETWORK_BLOCKED', type(exc).__name__)
    return True
"""
    outcome = run_official_test("value = 1\n", probe, "value", timeout=30.0)
    assert "NETWORK_BLOCKED" in outcome.stdout
    assert "NETWORK_CONNECTED" not in outcome.stdout


def test_without_the_request_the_evaluator_still_runs(seatbelt, monkeypatch):
    """The isolation is opt-in, and the plain path is unchanged."""
    monkeypatch.setenv("ITMS_EVAL_ISOLATION", "off")
    described = isolation_description()
    assert described["level"] == "none"
    with open(
        os.path.join(REPO_ROOT, "benchmarks", "data", "tasks", "HE-EDIT-006", "original_source.py"),
        encoding="utf-8",
    ) as handle:
        source = handle.read()
    with open(
        os.path.join(REPO_ROOT, "benchmarks", "data", "tasks", "HE-EDIT-006", "official_test.py"),
        encoding="utf-8",
    ) as handle:
        test = handle.read()
    outcome = run_official_test(source, test, "below_zero", timeout=30.0)
    assert outcome.passed is True
