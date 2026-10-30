"""Tests for the Docker sandbox engine (GAP-4).

These tests skip whenever Docker is unavailable (no daemon, missing client, or
unpullable image), so they never break in environments that only have the
subprocess harness.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from sandbox import ExecutionResult
from sandbox_docker import DockerSandboxEngine


def _docker_available() -> bool:
    try:
        import docker  # type: ignore

        client = docker.from_env()
        client.ping()
        try:
            client.images.get("python:3-slim")
        except Exception:
            try:
                client.images.pull("python:3-slim")
            except Exception:
                return False
        return True
    except Exception:
        return False


requires_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker daemon or python:3-slim image unavailable"
)


# ---------------------------------------------------------------------------
# Availability / graceful degradation
# ---------------------------------------------------------------------------

def test_docker_engine_instantiates_without_docker(monkeypatch):
    """Constructing the engine must not crash when Docker is absent."""
    import sandbox_docker as sd

    monkeypatch.setattr(sd.DockerSandboxEngine, "_probe_docker", lambda self: False)
    engine = DockerSandboxEngine()
    assert engine.available() is False


def test_docker_engine_describe_shape():
    engine = DockerSandboxEngine()
    desc = engine.describe()
    assert desc["engine"] == "docker"
    assert desc["network_disabled"] is True
    assert desc["mem_limit"] == "256m"
    assert desc["read_only_root_filesystem"] is True
    assert desc["tmpfs"] == {"/tmp": "rw"}
    assert desc["pids_limit"] == 64
    assert desc["nano_cpus"] == 1_000_000_000
    assert desc["cap_drop"] == ["ALL"]


# ---------------------------------------------------------------------------
# Execution (skipped without Docker)
# ---------------------------------------------------------------------------

@requires_docker
def test_docker_executes_passing_code():
    engine = DockerSandboxEngine()
    assert engine.available() is True
    result = engine.execute_test(
        candidate_code="def add(a, b):\n    return a + b\n",
        context_code="",
        test_suite_code="assert add(2, 3) == 5\n",
        target_function_name="add",
    )
    assert isinstance(result, ExecutionResult)
    assert result.success is True
    assert result.exit_code == 0
    assert result.syntax_valid is True
    assert result.timed_out is False


@requires_docker
def test_docker_catches_failing_assertion():
    engine = DockerSandboxEngine()
    result = engine.execute_test(
        candidate_code="def add(a, b):\n    return a - b\n",
        context_code="",
        test_suite_code="assert add(2, 3) == 5\n",
        target_function_name="add",
    )
    assert result.success is False
    assert result.exit_code != 0
    assert result.error_type is not None


@requires_docker
def test_docker_enforces_timeout():
    engine = DockerSandboxEngine()
    result = engine.execute_test(
        candidate_code="",
        context_code="",
        test_suite_code="import time; time.sleep(30)\n",
        target_function_name=None,
        timeout=1.0,
    )
    assert result.timed_out is True
    assert result.success is False


@requires_docker
def test_docker_rejects_syntax_errors():
    engine = DockerSandboxEngine()
    result = engine.execute_test(
        candidate_code="def broken(:\n    pass\n",
        context_code="",
        test_suite_code="",
        target_function_name="broken",
    )
    assert result.success is False
    assert result.syntax_valid is False


# ---------------------------------------------------------------------------
# Transparent subprocess fallback parity
# ---------------------------------------------------------------------------

def test_fallback_matches_subprocess_harness():
    """When Docker is unavailable the engine must behave like the harness."""
    import sandbox_docker as sd

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sd.DockerSandboxEngine, "_probe_docker", lambda self: False)
    engine = DockerSandboxEngine()
    result = engine.execute_test(
        candidate_code="def add(a, b):\n    return a + b\n",
        context_code="",
        test_suite_code="assert add(1, 2) == 3\n",
        target_function_name="add",
    )
    monkeypatch.undo()
    assert result.success is True
