"""
The Aider system adapter.

This runs the **real** ``aider`` CLI as a subprocess.  No Aider behaviour is
re-implemented here, and no "Aider-style" prompt template is substituted for a
real Aider run: if the binary is missing or the credential is absent, the
attempt is recorded as a failed *execution*, not as a result.

Invocation contract (non-interactive, one shot):

    aider --message <task instruction> \\
          --yes-always --exit \\
          --no-git --no-auto-commits --no-dirty-commits \\
          --no-pretty --no-stream --no-analytics --no-check-update \\
          --model openrouter/<model> \\
          --model-settings-file <seed/temperature yaml> \\
          candidate.py

with the process CWD set to the attempt's isolated workspace, so that Aider
edits only that workspace's ``candidate.py``.

Telemetry: Aider prints a summary line of the form
``Tokens: 1,234 sent, 567 received.`` and ``Cost: $0.01 message, $0.02
session.``  These are parsed from stdout.  Aider's numbers are *tool
reported* and are never merged with provider-reported numbers from the other
systems.

Seed: Aider accepts a request-level ``seed`` through its model-settings file
(``extra_params``), which is forwarded to the model API.  Whether the upstream
provider honours the seed is provider-dependent and is recorded honestly as
``seed_supported=True`` / ``seed_applied=<n>`` only when the flag was
actually placed into the request configuration.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    FailureCategory,
    SystemAdapter,
    SystemOutcome,
    TaskBundle,
    UsageSource,
    resolve_provider_key,
    sanitised_env,
    sha256_text,
    which,
)

_TOKEN_RE = re.compile(
    r"Tokens:\s*(?P<sent>[\d,\.kKmM]+)\s*sent"
    r"(?:,\s*(?P<cache_write>[\d,\.kKmM]+)\s*cache write)?"
    r"(?:,\s*(?P<cache_hit>[\d,\.kKmM]+)\s*cache hit)?"
    r",\s*(?P<received>[\d,\.kKmM]+)\s*received"
)
_COST_RE = re.compile(
    r"Cost:\s*\$(?P<message>[\d\.]+)\s*message,\s*\$(?P<session>[\d\.]+)\s*session"
)
_MODEL_RE = re.compile(r"^Model:\s*(?P<model>\S+)\s+with\s+", re.MULTILINE)


def _parse_count(text: str) -> Optional[int]:
    text = text.strip().replace(",", "")
    if not text:
        return None
    try:
        if text[-1] in "kK":
            return int(float(text[:-1]) * 1000)
        if text[-1] in "mM":
            return int(float(text[:-1]) * 1_000_000)
        return int(float(text))
    except ValueError:
        return None


class AiderSystemAdapter(SystemAdapter):
    name = "aider"
    supports_seed = True
    requires_credential = True

    def __init__(
        self,
        model_id: str,
        provider: str = "openrouter",
        temperature: float = 0.0,
        timeout_seconds: float = 300.0,
        binary: str = "aider",
        api_key: Optional[str] = None,
        extra_cli_args: Optional[List[str]] = None,
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.binary = binary
        self._api_key = api_key
        self.extra_cli_args = list(extra_cli_args or [])

    # -- configuration -----------------------------------------------------

    def _resolve_api_key(self) -> Optional[str]:
        if self._api_key:
            return self._api_key
        env_name = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }.get(self.provider, "OPENROUTER_API_KEY")
        self._api_key = resolve_provider_key(self.provider, fallback_env=env_name)
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
                "aider binary and credential present"
                if binary_path and key
                else "aider binary or credential missing"
            ),
            "supports_seed": self.supports_seed,
        }

    def describe_configuration(self) -> Dict[str, Any]:
        return {
            "system": self.name,
            "binary": self.binary,
            "model_id": self.model_id,
            "provider": self.provider,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "supports_seed": self.supports_seed,
            "seed_mechanism": (
                "aider model-settings file with 'extra_params.seed', forwarded "
                "to the model API by litellm"
            ),
            "edit_mechanism": "Aider's own whole-file / diff editing strategies",
            "telemetry_source": "aider stdout summary (tool reported)",
        }

    # -- execution ---------------------------------------------------------

    def _model_string(self) -> str:
        if self.provider == "openrouter":
            return f"openrouter/{self.model_id}"
        return self.model_id

    def _write_model_settings(self, workspace: str, seed: Optional[int]) -> Optional[str]:
        """Write the YAML model-settings file that carries seed + temperature."""
        payload: Dict[str, Any] = {"temperature": self.temperature}
        if seed is not None:
            payload["seed"] = int(seed)
        path = os.path.join(workspace, ".aider.model.settings.yml")
        lines = ["- name: aider/extra_params", "  extra_params:"]
        for key, value in payload.items():
            lines.append(f"    {key}: {value}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

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
            seed_supported=True,
            seed_applied=seed,
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

        settings_path = self._write_model_settings(workspace, seed)
        target = os.path.join(workspace, task.workspace_filename)

        cmd: List[str] = [
            binary_path,
            "--message", task.repair_instruction,
            "--yes-always",
            "--exit",
            "--no-git",
            "--no-auto-commits",
            "--no-dirty-commits",
            "--no-pretty",
            "--no-stream",
            "--no-analytics",
            "--no-check-update",
            "--no-show-release-notes",
            "--no-show-model-warnings",
            "--model", self._model_string(),
        ]
        if settings_path:
            cmd += ["--model-settings-file", settings_path]
        cmd += self.extra_cli_args
        cmd.append(task.workspace_filename)

        env = sanitised_env(
            {
                "OPENROUTER_API_KEY": key if self.provider == "openrouter" else None,
                "OPENAI_API_KEY": key if self.provider == "openai" else None,
                "ANTHROPIC_API_KEY": key if self.provider == "anthropic" else None,
                "DEEPSEEK_API_KEY": key if self.provider == "deepseek" else None,
                "GEMINI_API_KEY": key if self.provider == "gemini" else None,
                "OPENAI_API_BASE": os.environ.get("OPENAI_API_BASE")
                if self.provider == "openai"
                else None,
            }
        )

        stdout_path = os.path.join(workspace, "aider.stdout.log")
        stderr_path = os.path.join(workspace, "aider.stderr.log")
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
                timeout=self.timeout_seconds,
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
            outcome.failure_reason = f"failed to launch aider: {exc}"
            return outcome

        with open(stdout_path, "w", encoding="utf-8") as fh:
            fh.write(stdout)
        with open(stderr_path, "w", encoding="utf-8") as fh:
            fh.write(stderr)

        outcome.latency_ms = (time.perf_counter() - started) * 1000.0
        outcome.exit_code = exit_code
        outcome.timed_out = timed_out

        tokens, cost = _parse_aider_telemetry(stdout)
        if tokens.get("sent") is not None:
            outcome.prompt_tokens = tokens.get("sent")
            outcome.completion_tokens = tokens.get("received")
            outcome.total_tokens = (tokens.get("sent") or 0) + (tokens.get("received") or 0)
            outcome.usage_source = UsageSource.TOOL_REPORTED
        outcome.cache_read_tokens = tokens.get("cache_hit")
        outcome.cache_write_tokens = tokens.get("cache_write")
        if cost.get("session") is not None:
            outcome.cost_usd = cost.get("session")
            outcome.cost_source = UsageSource.TOOL_REPORTED
        model_match = _MODEL_RE.search(stdout)
        if model_match:
            outcome.served_model_id = model_match.group("model")
        outcome.provider = self.provider
        outcome.raw_telemetry = {"tokens": tokens, "cost": cost}

        if timed_out:
            outcome.execution_completed = False
            outcome.failure_category = FailureCategory.TIMEOUT
            outcome.failure_reason = (
                f"aider exceeded the {self.timeout_seconds}s wall-clock limit"
            )
            return outcome

        if exit_code not in (0, None):
            outcome.execution_completed = False
            outcome.failure_category = _classify_aider_failure(stdout + "\n" + stderr)
            outcome.failure_reason = (stderr or stdout).strip()[-500:] or (
                f"aider exited with code {exit_code}"
            )
            return outcome

        outcome.execution_completed = True
        candidate = _read_text(target)
        if candidate is None:
            outcome.failure_category = FailureCategory.NO_CANDIDATE
            outcome.failure_reason = "aider produced no candidate file"
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
            outcome.failure_reason = "aider left the file unchanged"
        return outcome


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _parse_aider_telemetry(stdout: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tokens: Dict[str, Any] = {}
    cost: Dict[str, Any] = {}
    match = _TOKEN_RE.search(stdout)
    if match:
        tokens["sent"] = _parse_count(match.group("sent"))
        tokens["received"] = _parse_count(match.group("received"))
        if match.group("cache_write"):
            tokens["cache_write"] = _parse_count(match.group("cache_write"))
        if match.group("cache_hit"):
            tokens["cache_hit"] = _parse_count(match.group("cache_hit"))
    cost_match = _COST_RE.search(stdout)
    if cost_match:
        try:
            cost["message"] = float(cost_match.group("message"))
        except ValueError:
            pass
        try:
            cost["session"] = float(cost_match.group("session"))
        except ValueError:
            pass
    return tokens, cost


def _classify_aider_failure(text: str) -> str:
    lowered = text.lower()
    if "quota" in lowered or "insufficient credits" in lowered or "402" in lowered:
        return FailureCategory.QUOTA_EXCEEDED
    if "401" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
        return FailureCategory.AUTH_ERROR
    if "timeout" in lowered or "timed out" in lowered:
        return FailureCategory.TIMEOUT
    if "connection" in lowered or "network" in lowered or "could not connect" in lowered:
        return FailureCategory.NETWORK_ERROR
    return FailureCategory.PROVIDER_ERROR
