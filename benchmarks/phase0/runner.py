"""
Phase 0 isolated execution runner.

Runs a candidate Python module against an official HumanEval ``check``
function inside a throw-away directory:

*   the working directory is a fresh temporary directory,
*   ``PYTHONPATH`` is scrubbed so that modules from the project directory can
    never be imported accidentally,
*   user site-packages are disabled,
*   no convenience imports are injected,
*   sockets are blocked at the interpreter level (sitecustomize) so that a
    test cannot reach the network,
*   the process is killed after a wall-clock timeout.

Nothing here talks to a model or to an API.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["ExecutionOutcome", "run_official_test"]

_SITECUSTOMIZE = '''\
"""Injected by the Phase 0 validator: block all outbound network access."""

import socket


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise OSError("network access is disabled in the Phase 0 validation sandbox")


def _blocked(*args, **kwargs):
    raise OSError("network access is disabled in the Phase 0 validation sandbox")


socket.socket = _BlockedSocket
socket.create_connection = _blocked
socket.socketpair = _blocked
try:
    import urllib.request as _urllib_request

    def _blocked_urlopen(*args, **kwargs):
        raise OSError("network access is disabled in the Phase 0 validation sandbox")

    _urllib_request.urlopen = _blocked_urlopen
except Exception:
    pass
'''


@dataclass
class ExecutionOutcome:
    """Result of running one candidate module against the official test."""

    passed: bool
    exit_code: int
    timed_out: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "passed": self.passed,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "duration_ms": round(self.duration_ms, 3),
        }


def _sanitized_env() -> Dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
    }
    return env


def _summarise_failure(stderr: str) -> tuple:
    """Extract (error_type, error_message) from a captured traceback."""
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    error_type = None
    error_message = None
    for ln in reversed(lines):
        if ln.startswith("AssertionError"):
            error_type = "AssertionError"
            error_message = ln.strip()
            break
        parts = ln.split(":", 1)
        if len(parts) == 2 and parts[0].strip() and parts[0].strip()[-1].isupper():
            error_type = parts[0].strip().split(".")[-1]
            error_message = parts[1].strip()
            break
    if error_type is None and lines:
        error_type = "ExecutionError"
        error_message = lines[-1].strip()
    return error_type, error_message


def run_official_test(
    source_code: str,
    test_code: str,
    entry_point: str,
    timeout: float = 20.0,
    workdir: Optional[str] = None,
) -> ExecutionOutcome:
    """Execute ``check(entry_point)`` against ``source_code`` in isolation."""
    import time

    tmp_root = tempfile.mkdtemp(prefix="itms_phase0_", dir=workdir)
    try:
        module_dir = os.path.join(tmp_root, "work")
        os.makedirs(module_dir, exist_ok=True)

        with open(os.path.join(module_dir, "sitecustomize.py"), "w", encoding="utf-8") as fh:
            fh.write(_SITECUSTOMIZE)

        with open(os.path.join(module_dir, "candidate.py"), "w", encoding="utf-8") as fh:
            fh.write(source_code)

        harness = (
            source_code
            + "\n\n"
            + test_code
            + "\n\n"
            + f"check({entry_point})\n"
        )
        with open(os.path.join(module_dir, "run_checks.py"), "w", encoding="utf-8") as fh:
            fh.write(harness)

        env = _sanitized_env()
        env["PYTHONPATH"] = module_dir

        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-B", "-s", "run_checks.py"],
                cwd=module_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ExecutionOutcome(
                passed=False,
                exit_code=-1,
                timed_out=True,
                error_type="TimeoutExpired",
                error_message=f"execution exceeded {timeout:.1f}s",
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                duration_ms=elapsed,
            )
        elapsed = (time.perf_counter() - start) * 1000.0

        if proc.returncode == 0:
            return ExecutionOutcome(
                passed=True,
                exit_code=0,
                timed_out=False,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=elapsed,
            )

        error_type, error_message = _summarise_failure(proc.stderr)
        return ExecutionOutcome(
            passed=False,
            exit_code=proc.returncode,
            timed_out=False,
            error_type=error_type,
            error_message=error_message,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=elapsed,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
