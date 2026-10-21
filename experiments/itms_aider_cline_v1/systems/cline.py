"""
The Cline system adapter.

This runs the **real** ``cline`` CLI as a subprocess.  Cline is an autonomous
coding agent: it reads the workspace, plans, and edits files through its own
tool set.  Nothing about Cline's behaviour is re-implemented here.

Invocation contract (non-interactive, one shot):

    cline --json --yolo \\
          --provider openrouter --model <model> \\
          --cwd <workspace> --timeout <seconds> \\
          <task instruction>

``--json`` makes Cline emit one JSON object per line; the final
``run_result`` object carries the telemetry we need:

    {"type":"run_result","finishReason":"...","durationMs":1234,
     "usage":{"inputTokens":N,"outputTokens":N,"cacheReadTokens":N,
              "cacheWriteTokens":N,"totalCost":0.0},
     "model":{"id":"...","provider":"..."}}

Telemetry is therefore *tool reported*.  Cline does not expose a request-level
random seed, so ``supports_seed`` is ``False`` for this system and the
experiment records repetition IDs instead of claiming seeded runs.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from .base import (
    FailureCategory,
    SystemAdapter,
    SystemOutcome,
    TaskBundle,
    UsageSource,
    sanitised_env,
    sha256_text,
    which,
)


class ClineSystemAdapter(SystemAdapter):
    name = "cline"
    supports_seed = False
    requires_credential = True

    def __init__(
        self,
        model_id: str,
        provider: str = "openrouter",
        timeout_seconds: float = 300.0,
        binary: str = "cline",
        api_key: Optional[str] = None,
        config_dir: Optional[str] = None,
        data_dir: Optional[str] = None,
        extra_cli_args: Optional[List[str]] = None,
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.binary = binary
        self._api_key = api_key
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.extra_cli_args = list(extra_cli_args or [])

    # -- configuration -----------------------------------------------------

    def _resolve_api_key(self) -> Optional[str]:
        if self._api_key:
            return self._api_key
        env_name = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepai": "DEEPSEEK_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }.get(self.provider, "OPENROUTER_API_KEY")
        value = os.environ.get(env_name)
        if value:
            self._api_key = value
            return value
        repo_config = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.py",
        )
        import importlib.util

        spec = importlib.util.spec_from_file_location("itms_app_config", repo_config)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._api_key = module.AppConfig.resolve_api_key(self.provider)
        return self._api_key

    def preflight(self) -> Dict[str, Any]:
        binary_path = which(self.binary)
        key = self._resolve_api_key()
        return {
            "system": self.name,
            "available": bool(binary_path) and bool(key),
            "binary_path": binary_path,
            "credential_present": bool(key),
            "detail": (
                "cline binary and credential present"
                if binary_path and key
                else "cline binary or credential missing"
            ),
            "supports_seed": self.supports_seed,
            "seed_note": (
                "cline exposes no request-level random seed; repetitions are "
                "identified by repetition_id only"
            ),
        }

    def describe_configuration(self) -> Dict[str, Any]:
        return {
            "system": self.name,
            "binary": self.binary,
            "model_id": self.model_id,
            "provider": self.provider,
            "timeout_seconds": self.timeout_seconds,
            "supports_seed": self.supports_seed,
            "seed_mechanism": None,
            "edit_mechanism": "Cline's autonomous tool use (read/edit/write files)",
            "telemetry_source": "cline --json run_result (tool reported)",
            "config_dir": self.config_dir or "(cline default)",
            "data_dir": self.data_dir or "(cline default)",
        }

    # -- execution ---------------------------------------------------------

    def run(
        self,
        task: TaskBundle,
        workspace: str,
        repetition_id: str,
        seed: Optional[int] = None,
    ) -> SystemOutcome:
        started = time.perf_counter()
        outcome = SystemOutcome(
            system=self.name,
            task_id=task.task_id,
            repetition_id=repetition_id,
            seed_requested=seed,
            seed_supported=False,
            seed_applied=None,
            telemetry_source="tool_reported",
        )

        binary_path = which(self.binary)
        if not binary_path:
            outcome.execution_completed = False
            outcome.failure_category = FailureCategory.TOOL_NOT_AVAILABLE
            outcome.failure_reason = f"'{self.binary}' executable not found on PATH"
            outcome.latency_ms = (time.perf_counter() - started) * 1000.0
            return outcome

        key = self._resolve_api_key()
        if not key:
            outcome.execution_completed = False
            outcome.failure_category = FailureCategory.CONFIGURATION_ERROR
            outcome.failure_reason = f"no credential configured for provider '{self.provider}'"
            outcome.latency_ms = (time.perf_counter() - started) * 1000.0
            return outcome

        cmd: List[str] = [
            binary_path,
            "--json",
            "--yolo",
            "--auto-approve", "true",
            "--provider", self.provider,
            "--model", self.model_id,
            "--cwd", workspace,
            "--timeout", str(int(self.timeout_seconds)),
        ]
        if self.config_dir:
            cmd += ["--config", self.config_dir]
        if self.data_dir:
            cmd += ["--data-dir", self.data_dir]
        cmd += self.extra_cli_args
        cmd.append(task.repair_instruction)

        env = sanitised_env(
            {
                "OPENROUTER_API_KEY": key if self.provider == "openrouter" else None,
                "OPENAI_API_KEY": key if self.provider == "openai" else None,
                "ANTHROPIC_API_KEY": key if self.provider == "anthropic" else None,
                "DEEPSEEK_API_KEY": key if self.provider == "deepseek" else None,
                "GEMINI_API_KEY": key if self.provider == "gemini" else None,
            }
        )

        stdout_path = os.path.join(workspace, "cline.stdout.jsonl")
        stderr_path = os.path.join(workspace, "cline.stderr.log")
        outcome.stdout_path = stdout_path
        outcome.stderr_path = stderr_path

        timed_out = False
        exit_code: Optional[int] = None
        stdout = ""
        stderr = ""
        try:
            proc = subprocess.run(
                cmd,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds + 60.0,  # CLI timeout + shutdown grace
                check=False,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        except OSError as exc:
            outcome.execution_completed = False
            outcome.latency_ms = (time.perf_counter() - started) * 1000.0
            outcome.failure_category = FailureCategory.TOOL_NOT_AVAILABLE
            outcome.failure_reason = f"failed to launch cline: {exc}"
            return outcome

        with open(stdout_path, "w", encoding="utf-8") as fh:
            fh.write(stdout)
        with open(stderr_path, "w", encoding="utf-8") as fh:
            fh.write(stderr)

        outcome.latency_ms = (time.perf_counter() - started) * 1000.0
        outcome.exit_code = exit_code
        outcome.timed_out = timed_out

        events, run_result, parse_error = _parse_cline_jsonl(stdout)
        outcome.raw_telemetry = {
            "event_count": len(events),
            "run_result": run_result,
            "parse_error": parse_error,
        }

        if run_result:
            usage = run_result.get("usage") or {}
            prompt_tokens = usage.get("inputTokens")
            completion_tokens = usage.get("outputTokens")
            if prompt_tokens is not None or completion_tokens is not None:
                outcome.prompt_tokens = prompt_tokens
                outcome.completion_tokens = completion_tokens
                outcome.total_tokens = usage.get("totalTokens") or (
                    (prompt_tokens or 0) + (completion_tokens or 0)
                )
                outcome.usage_source = UsageSource.TOOL_REPORTED
            total_cost = usage.get("totalCost")
            if total_cost is not None:
                outcome.cost_usd = float(total_cost)
                outcome.cost_source = UsageSource.TOOL_REPORTED
            model_info = run_result.get("model") or {}
            outcome.extra["cline_model"] = model_info
            outcome.extra["cline_finish_reason"] = run_result.get("finishReason")
            outcome.extra["cline_duration_ms"] = run_result.get("durationMs")

        if timed_out:
            outcome.execution_completed = False
            outcome.failure_category = FailureCategory.TIMEOUT
            outcome.failure_reason = (
                f"cline exceeded the {self.timeout_seconds}s wall-clock limit"
            )
            return outcome

        finish_reason = (run_result or {}).get("finishReason")
        error_events = [e for e in events if e.get("type") == "error"]
        if error_events:
            first = error_events[0]
            outcome.execution_completed = False
            outcome.failure_category = _classify_cline_failure(first)
            outcome.failure_reason = str(first.get("message", ""))[:500]
            return outcome

        if exit_code not in (0, None) and finish_reason not in ("stop", "success", "completed"):
            outcome.execution_completed = False
            outcome.failure_category = _classify_cline_failure({"message": stderr or stdout})
            outcome.failure_reason = (stderr or stdout).strip()[-500:] or (
                f"cline exited with code {exit_code}"
            )
            return outcome

        outcome.execution_completed = True
        target = os.path.join(workspace, task.workspace_filename)
        candidate = _read_text(target)
        if candidate is None:
            outcome.failure_category = FailureCategory.NO_CANDIDATE
            outcome.failure_reason = "cline produced no candidate file"
            return outcome

        outcome.candidate_source = candidate
        outcome.candidate_sha256 = sha256_text(candidate)
        outcome.patch_applied = candidate.strip() != task.mutated_source.strip()
        try:
            ast.parse(candidate)
            outcome.syntax_valid = True
        except SyntaxError:
            outcome.syntax_valid = False
            outcome.failure_category = FailureCategory.SYNTAX_ERROR
            outcome.failure_reason = "candidate did not parse"
        if outcome.failure_category == FailureCategory.NONE and not outcome.patch_applied:
            outcome.failure_category = FailureCategory.PATCH_NOT_APPLIED
            outcome.failure_reason = "cline left the file unchanged"
        return outcome


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _parse_cline_jsonl(stdout: str):
    events: List[Dict[str, Any]] = []
    run_result: Optional[Dict[str, Any]] = None
    parse_error: Optional[str] = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(obj)
        if obj.get("type") == "run_result":
            run_result = obj
        if obj.get("type") == "error" and parse_error is None:
            parse_error = str(obj.get("message", ""))[:500]
    return events, run_result, parse_error


def _classify_cline_failure(event: Dict[str, Any]) -> str:
    error_class = str(event.get("errorClass", "")).lower()
    message = str(event.get("message", "")).lower()
    if "auth" in error_class or "api key" in message or "401" in message \
            or "unauthorized" in message:
        return FailureCategory.AUTH_ERROR
    if "quota" in error_class or "402" in message or "credits" in message:
        return FailureCategory.QUOTA_EXCEEDED
    if "config" in error_class:
        return FailureCategory.CONFIGURATION_ERROR
    if "timeout" in error_class or "timeout" in message:
        return FailureCategory.TIMEOUT
    if "network" in error_class or "fetch failed" in message or "econnrefused" in message:
        return FailureCategory.NETWORK_ERROR
    return FailureCategory.PROVIDER_ERROR
