"""
Docker-backed execution engine (GAP-4).

`DockerSandboxEngine` exposes the same ``ExecutionResult`` interface as
``sandbox.ExecutionHarness`` so it can be dropped into
``RelayOrchestrator`` (or used standalone) behind a ``--sandbox {process,docker}``
selection.  It enforces container-level isolation that the subprocess harness
cannot:

* ``network_disabled=True``            -- network air-gapping (--net=none)
* ``mem_limit="256m"``                  -- memory cap
* ``read_only=True``                    -- read-only root filesystem
* ``tmpfs={"/tmp": "rw"}``              -- writable scratch only
* ``pids_limit=64``                     -- process limit
* ``nano_cpus=1_000_000_000``           -- 1 CPU
* ``cap_drop=["ALL"]``                  -- drop all capabilities
* ``timeout``                           -- wall-clock kill

If the Docker daemon, the ``docker`` Python client, or the image is unavailable,
the engine degrades **transparently** to the existing subprocess harness
(``ExecutionHarness``) so callers never crash and never lose functionality.
"""

from __future__ import annotations

import ast
import os
import time
import tempfile
import textwrap
from typing import Any, Dict, Optional

from sandbox import ExecutionHarness, ExecutionResult


class DockerSandboxEngine:
    """
    Container-based deterministic execution gate.

    The engine is intentionally defensive: every Docker interaction is wrapped
    so that any failure (daemon down, client missing, image missing, pull
    failure, run failure) falls back to the subprocess harness rather than
    raising to the caller.
    """

    DEFAULT_IMAGE = "python:3-slim"

    def __init__(
        self,
        default_timeout: float = 3.0,
        image: Optional[str] = None,
    ):
        self.default_timeout = float(default_timeout)
        self.image = image or self.DEFAULT_IMAGE
        self._client: Any = None
        self._docker_available = self._probe_docker()
        self._image_ready = False
        if self._docker_available:
            self._ensure_image()

    # ------------------------------------------------------------------
    # Docker capability detection
    # ------------------------------------------------------------------
    def _probe_docker(self) -> bool:
        """Return True when a reachable Docker daemon and client exist."""
        try:
            import docker  # type: ignore

            self._client = docker.from_env()
            self._client.ping()
            return True
        except Exception:
            self._client = None
            return False

    def _ensure_image(self) -> None:
        """Ensure the configured image is present locally (pull if needed)."""
        assert self._client is not None
        try:
            self._client.images.get(self.image)
            self._image_ready = True
            return
        except Exception:
            pass
        try:
            self._client.images.pull(self.image)
            self._image_ready = True
        except Exception:
            self._image_ready = False

    def available(self) -> bool:
        """True when Docker is reachable and the image is ready."""
        return self._docker_available and self._image_ready

    def describe(self) -> Dict[str, Any]:
        """Human/machine-readable isolation description (never leaks secrets)."""
        return {
            "engine": "docker",
            "image": self.image,
            "available": self.available(),
            "network_disabled": True,
            "mem_limit": "256m",
            "read_only_root_filesystem": True,
            "tmpfs": {"/tmp": "rw"},
            "pids_limit": 64,
            "nano_cpus": 1_000_000_000,
            "cap_drop": ["ALL"],
        }

    # ------------------------------------------------------------------
    # Public interface (mirrors ExecutionHarness.execute_test)
    # ------------------------------------------------------------------
    def execute_test(
        self,
        candidate_code: str,
        context_code: str = "",
        test_suite_code: str = "",
        target_function_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ExecutionResult:
        timeout_limit = timeout if timeout is not None else self.default_timeout
        t0 = time.perf_counter()

        # Transparent fallback when Docker cannot be used.
        if not self.available():
            harness = ExecutionHarness(default_timeout=timeout_limit)
            return harness.execute_test(
                candidate_code=candidate_code,
                context_code=context_code,
                test_suite_code=test_suite_code,
                target_function_name=target_function_name,
                timeout=timeout_limit,
            )

        full_script = self._assemble_script(
            candidate_code, context_code, test_suite_code, target_function_name
        )

        # Fast static syntax pre-check before paying for a container start.
        try:
            ast.parse(full_script)
        except SyntaxError as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            msg = f"SyntaxError at line {e.lineno}: {e.msg}"
            return ExecutionResult(
                success=False,
                exit_code=1,
                error_type="SyntaxError",
                error_message=msg,
                failing_assertion=msg,
                stdout="",
                stderr=msg,
                duration_ms=elapsed_ms,
                timed_out=False,
                syntax_valid=False,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                success=False,
                exit_code=1,
                error_type="ParseError",
                error_message=str(e),
                failing_assertion="ParseError",
                stdout="",
                stderr=str(e),
                duration_ms=elapsed_ms,
                timed_out=False,
                syntax_valid=False,
            )

        container: Any = None
        host_script: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(full_script)
                host_script = tmp.name

            container = self._client.containers.run(  # type: ignore[union-attr]
                self.image,
                command=["python", "/app/run.py"],
                volumes={host_script: {"bind": "/app/run.py", "mode": "ro"}},
                network_disabled=True,
                mem_limit="256m",
                pids_limit=64,
                nano_cpus=1_000_000_000,
                cap_drop=["ALL"],
                read_only=True,
                tmpfs={"/tmp": "rw"},
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                },
                detach=True,
                stdout=True,
                stderr=True,
            )

            timed_out = False
            try:
                wait_result = container.wait(timeout=timeout_limit)
                exit_code = int(wait_result.get("StatusCode", 1))
            except Exception:
                # Wall-clock timeout: kill the container.
                timed_out = True
                exit_code = -1
                try:
                    container.kill()
                except Exception:
                    pass
                try:
                    container.wait(timeout=5)
                except Exception:
                    pass

            output_text = self._collect_logs(container)
            stdout_text = output_text
            stderr_text = ""

            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if exit_code == 0 and not timed_out:
                return ExecutionResult(
                    success=True,
                    exit_code=0,
                    error_type=None,
                    error_message=None,
                    failing_assertion=None,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    duration_ms=elapsed_ms,
                    timed_out=False,
                    syntax_valid=True,
                )

            error_type = "ExecutionError"
            error_message = (stderr_text or stdout_text or "").strip() or "Non-zero exit"
            failing_assertion = error_message
            return ExecutionResult(
                success=False,
                exit_code=exit_code,
                error_type=error_type,
                error_message=error_message,
                failing_assertion=failing_assertion,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_ms=elapsed_ms,
                timed_out=timed_out,
                syntax_valid=True,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                success=False,
                exit_code=1,
                error_type=type(e).__name__,
                error_message=str(e),
                failing_assertion="DockerSandboxEngine error",
                stdout="",
                stderr=str(e),
                duration_ms=elapsed_ms,
                timed_out=False,
                syntax_valid=True,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if host_script is not None and os.path.exists(host_script):
                try:
                    os.unlink(host_script)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _collect_logs(container: Any) -> str:
        try:
            raw = container.logs(stdout=True, stderr=True)
        except Exception:
            return ""
        if raw is None:
            return ""
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        # docker-py >= 7 may return a generator of chunks.
        chunks = []
        try:
            for chunk in raw:
                if isinstance(chunk, bytes):
                    chunks.append(chunk)
                else:
                    chunks.append(str(chunk).encode("utf-8", errors="replace"))
        except Exception:
            pass
        return b"".join(chunks).decode("utf-8", errors="replace") if chunks else ""

    @staticmethod
    def _assemble_script(
        candidate_code: str,
        context_code: str,
        test_suite_code: str,
        target_function_name: Optional[str],
    ) -> str:
        imports = (
            "import sys, os, math, json, re, time, collections, itertools, "
            "heapq, hashlib, functools, copy, string, datetime, decimal\n"
            "from typing import List, Dict, Tuple, Set, Optional, Any, Union, "
            "Callable, Iterable, Sequence, Mapping, Iterator\n"
        )
        parts = [imports]
        if context_code.strip():
            parts.append(context_code.strip())
        if candidate_code.strip():
            parts.append(textwrap.dedent(candidate_code).strip())
        if test_suite_code.strip():
            parts.append(test_suite_code.strip())
        return "\n\n".join(parts)
