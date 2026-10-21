"""
The ITMS system adapter.

ITMS is this project's own editing agent: it reduces the file to a
*skeleton* (non-target function bodies replaced by ``...``), asks the model
for the replacement of the target function only, and applies the answer with
an AST-level surgical splice rather than by rewriting the file as text.

The existing, already-tested implementation lives in
``benchmarks/benchmark/adapters/itms_splice.py`` and is reused verbatim.
Only the provider call is re-pointed at the shared model configuration so
that all three systems talk to the *same* model.
"""

from __future__ import annotations

import ast
import os
import time
from typing import Any, Dict, Optional

from .base import (
    FailureCategory,
    SystemAdapter,
    SystemOutcome,
    TaskBundle,
    UsageSource,
    resolve_provider_key,
    sha256_text,
)


class ItmsSystemAdapter(SystemAdapter):
    name = "itms"
    supports_seed = True
    requires_credential = True

    def __init__(
        self,
        model_id: str,
        provider: str = "openrouter",
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
        request_timeout: float = 120.0,
        api_key: Optional[str] = None,
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.request_timeout = request_timeout
        self._api_key = api_key
        self._dispatcher = None

    # -- configuration -----------------------------------------------------

    def _resolve_api_key(self) -> Optional[str]:
        if self._api_key:
            return self._api_key
        self._api_key = resolve_provider_key(self.provider)
        return self._api_key

    def preflight(self) -> Dict[str, Any]:
        key = self._resolve_api_key()
        return {
            "system": self.name,
            "available": bool(key),
            "detail": (
                "credential present for provider '%s'" % self.provider
                if key
                else "no credential configured for provider '%s'" % self.provider
            ),
            "model_id": self.model_id,
            "provider": self.provider,
            "supports_seed": self.supports_seed,
        }

    def describe_configuration(self) -> Dict[str, Any]:
        return {
            "system": self.name,
            "model_id": self.model_id,
            "provider": self.provider,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "request_timeout_seconds": self.request_timeout,
            "supports_seed": self.supports_seed,
            "seed_mechanism": (
                "request-level 'seed' field on the chat-completions payload "
                "(model_dispatcher.ModelDispatcher.generate(seed=...))"
            ),
            "edit_mechanism": "AST surgical splice of the target function",
        }

    # -- execution ---------------------------------------------------------

    def _get_dispatcher(self):
        if self._dispatcher is None:
            from model_dispatcher import ModelDispatcher

            self._dispatcher = ModelDispatcher(strict_live=True)
        return self._dispatcher

    def run(
        self,
        task: TaskBundle,
        workspace: str,
        repetition_id: str,
        seed: Optional[int] = None,
    ) -> SystemOutcome:
        from benchmarks.benchmark.adapters import get_adapter
        from benchmarks.benchmark.tasks import BenchmarkTask

        started = time.perf_counter()
        outcome = SystemOutcome(
            system=self.name,
            task_id=task.task_id,
            repetition_id=repetition_id,
            seed_requested=seed,
            seed_supported=True,
            seed_applied=seed,
            telemetry_source="provider_reported",
        )

        bench_task = BenchmarkTask(
            task_id=task.task_id,
            humaneval_task_id=task.humaneval_task_id,
            target_function=task.target_function,
            problem_prompt=task.problem_prompt,
            repair_instruction=task.repair_instruction,
            mutated_source=task.mutated_source,
            mutated_source_path=task.mutated_source_path,
            official_test="",
            official_test_path=task.official_test_path,
            mutated_source_sha256=sha256_text(task.mutated_source),
        )

        adapter = get_adapter("itms_ast_splice")
        prompt = adapter.build_prompt(bench_task)

        # Fail closed before any dispatch: without a credential the attempt
        # must be recorded as a configuration failure, not as a provider
        # error, and no request may be attempted.
        if not self._resolve_api_key():
            outcome.latency_ms = (time.perf_counter() - started) * 1000.0
            outcome.execution_completed = False
            outcome.failure_category = FailureCategory.CONFIGURATION_ERROR
            outcome.failure_reason = (
                f"no credential configured for provider '{self.provider}'"
            )
            return outcome

        try:
            dispatcher = self._get_dispatcher()
            response = dispatcher.generate_live(
                prompt=prompt,
                model_name=self.model_id,
                system_instruction=None,
                max_output_tokens=self.max_output_tokens,
                temperature=self.temperature,
                seed=seed,
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            outcome.latency_ms = (time.perf_counter() - started) * 1000.0
            outcome.execution_completed = False
            outcome.execution_error = f"{type(exc).__name__}: {exc}"
            outcome.failure_category = _classify_exception(exc)
            outcome.failure_reason = str(exc)[:500]
            return outcome

        outcome.execution_completed = True
        outcome.latency_ms = (time.perf_counter() - started) * 1000.0
        outcome.provider = getattr(response, "provider", None)
        outcome.served_model_id = getattr(response, "served_model_id", None)

        content = getattr(response, "content", None) or ""
        outcome.raw_telemetry = {
            "finish_reason": getattr(response, "finish_reason", None),
            "served_model_id": outcome.served_model_id,
            "provider": outcome.provider,
            "retry_count": getattr(response, "retry_count", None),
            "usage_source": getattr(response, "usage_source", None),
            "raw_tokens_in": getattr(response, "raw_tokens_in", None),
            "cached_tokens_in": getattr(response, "cached_tokens_in", None),
            "output_tokens": getattr(response, "output_tokens", None),
            "reasoning_tokens": getattr(response, "reasoning_tokens", None),
        }
        _apply_usage(outcome, response)
        if getattr(response, "cost_usd", None) is not None:
            outcome.cost_usd = float(response.cost_usd)
            outcome.cost_source = getattr(response, "cost_source", None) or UsageSource.PROVIDER_REPORTED

        parsed = adapter.parse_and_apply(bench_task, rep=1, response_text=content)
        outcome.failure_reason = parsed.parse_error
        if parsed.candidate_source is None:
            outcome.failure_category = FailureCategory.NO_CANDIDATE
            outcome.failure_reason = parsed.parse_error or "adapter produced no candidate"
            return outcome

        outcome.candidate_source = parsed.candidate_source
        outcome.candidate_sha256 = sha256_text(parsed.candidate_source)
        outcome.patch_applied = bool(parsed.applied)
        outcome.syntax_valid = _syntax_ok(parsed.candidate_source)
        if outcome.syntax_valid is False:
            outcome.failure_category = FailureCategory.SYNTAX_ERROR
        elif not parsed.applied:
            outcome.failure_category = FailureCategory.PATCH_NOT_APPLIED
        outcome.extra["adapter_failure_reason"] = parsed.failure_reason
        outcome.extra["adapter_detail"] = parsed.detail
        return outcome


def _syntax_ok(source: str) -> Optional[bool]:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def _apply_usage(outcome: SystemOutcome, response: Any) -> None:
    """Copy the provider-reported usage off a ``ModelResponse``.

    Only the *provider-reported* fields are used.  The dispatcher's local
    tiktoken estimates live in separate fields and are never substituted for
    a provider-reported number.
    """
    prompt_tokens = getattr(response, "provider_prompt_tokens", None)
    completion_tokens = getattr(response, "provider_completion_tokens", None)
    total_tokens = getattr(response, "provider_total_tokens", None)
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return
    outcome.prompt_tokens = prompt_tokens
    outcome.completion_tokens = completion_tokens
    if total_tokens is not None:
        outcome.total_tokens = total_tokens
    elif prompt_tokens is not None or completion_tokens is not None:
        outcome.total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    outcome.cache_read_tokens = getattr(response, "cached_tokens_in", None) or None
    usage_source = getattr(response, "usage_source", None)
    if usage_source == "provider_reported":
        outcome.usage_source = UsageSource.PROVIDER_REPORTED
    elif usage_source in ("provider_partial", "local_estimate", "local_estimate_not_reported"):
        outcome.usage_source = UsageSource.LOCAL_ESTIMATE
    else:
        outcome.usage_source = UsageSource.PROVIDER_REPORTED


def _classify_exception(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "quota" in name or "quota" in text or "402" in text:
        return FailureCategory.QUOTA_EXCEEDED
    if "auth" in text or "api key" in text or "unauthorized" in text or "401" in text:
        return FailureCategory.AUTH_ERROR
    if "config" in name or "no api key" in text:
        return FailureCategory.CONFIGURATION_ERROR
    if "timeout" in name or "timeout" in text:
        return FailureCategory.TIMEOUT
    if "connect" in text or "network" in text:
        return FailureCategory.NETWORK_ERROR
    return FailureCategory.PROVIDER_ERROR
