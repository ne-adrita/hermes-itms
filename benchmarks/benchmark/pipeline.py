"""
The single-attempt pipeline.

One attempt = one (task, method, repetition) triple.  The pipeline is the only
place where a provider call happens, and it always follows the same order:

1.  load the approved model-facing task,
2.  create an isolated temporary workspace,
3.  build the method-specific prompt,
4.  request exactly one provider response,
5.  parse the response with the method's strict output contract,
6.  apply the edit inside the workspace,
7.  verify syntax,
8.  run the official test against the *candidate* (never the reference),
9.  record the outcome,
10. clean up the workspace (also on exceptions).

No convenience imports are injected into the candidate, inherited ``PYTHONPATH``
is scrubbed and user site-packages are disabled by the evaluation sandbox.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.adapters.base import AdapterResult, EditingAdapter, FailureReason  # noqa: E402
from benchmarks.benchmark.provider import BenchmarkProvider, ProviderError, redact_secrets  # noqa: E402
from benchmarks.benchmark.records import AttemptRecord, AttemptStatus, FailureCategory  # noqa: E402

__all__ = ["AttemptPipeline", "AttemptOutcome"]


@dataclass
class AttemptOutcome:
    """The result of running one attempt.

    Besides the attempt record, the outcome carries the material needed for
    an auditable artefact trail: the raw provider response, the exact
    candidate source that was submitted to the official evaluator, the
    evaluation outcome, and the provider error (if the attempt failed before
    a response existed).
    """

    record: AttemptRecord
    adapter_result: Optional[AdapterResult] = None
    raw_response: Optional[str] = None
    candidate_source: Optional[str] = None
    evaluation: Optional[Dict[str, Any]] = None
    provider_error: Optional[Dict[str, Any]] = None
    #: The exact prompt that was sent to the provider.  It is persisted next to
    #: the response so the "identical instruction" claim is auditable.
    prompt: Optional[str] = None


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _estimate_prompt_tokens(prompt: Optional[str]) -> Optional[int]:
    """
    Local token estimate of a prompt.

    This is an **estimate** and is stored in its own field; it is never mixed
    into a provider-reported token count.  Returns None when the local
    tokenizer is unavailable, so an unmeasured value is never written as 0.
    """
    if not prompt:
        return None
    try:
        from prompt_manager import get_token_count

        return int(get_token_count(prompt))
    except Exception:
        return None


class AttemptPipeline:
    """Runs one attempt of one editing method against one task."""

    def __init__(
        self,
        provider: BenchmarkProvider,
        adapter: EditingAdapter,
        mode: str,
        run_id: str,
        evaluation_timeout: float = 10.0,
        system_instruction: Optional[str] = None,
    ):
        self.provider = provider
        self.adapter = adapter
        self.mode = mode
        self.run_id = run_id
        self.evaluation_timeout = float(evaluation_timeout)
        self.system_instruction = system_instruction

    # -- main entry point ------------------------------------------------
    def run(self, task, rep: int, seed: Optional[int] = None) -> AttemptOutcome:
        attempt_id = uuid.uuid4().hex
        record = AttemptRecord(
            attempt_id=attempt_id,
            run_id=self.run_id,
            timestamp_utc=_utc_now(),
            task_id=task.task_id,
            humaneval_task_id=task.humaneval_task_id,
            method=self.adapter.name,
            rep=rep,
            mode=self.mode,
            provider=self.provider.name,
            requested_model_id=getattr(self.provider, "model_id", None),
            is_mock=False,
            seed_requested=seed,
            max_output_tokens=getattr(self.provider, "max_output_tokens", None),
        )

        started = time.perf_counter()
        workspace = tempfile.mkdtemp(prefix="itms_bench_")
        try:
            prompt = self.adapter.build_prompt(task)
            record.prompt_token_estimate = _estimate_prompt_tokens(prompt)

            try:
                provider_result, seed_supported = self._call_provider(prompt, seed)
            except ProviderError as exc:
                record.status = AttemptStatus.PROVIDER_ERROR
                record.failure_category = self._provider_category(exc.category)
                record.error_message = redact_secrets(str(exc))
                record.timed_out = exc.category == "timeout"
                # The retry count performed before the failure is preserved.
                record.retry_count = int(getattr(exc, "retry_count", 0) or 0)
                record.latency_ms = (time.perf_counter() - started) * 1000.0
                return AttemptOutcome(
                    record=record,
                    provider_error={
                        "category": exc.category,
                        "status_code": exc.status_code,
                        "retry_count": record.retry_count,
                        "message": record.error_message,
                    },
                    prompt=prompt,
                )

            record.is_mock = bool(provider_result.is_mock)
            record.provider = provider_result.provider
            record.requested_model_id = provider_result.requested_model_id
            record.served_model_id = provider_result.served_model_id
            record.finish_reason = provider_result.finish_reason
            record.retry_count = provider_result.retry_count
            record.provider_latency_ms = provider_result.latency_ms
            record.prompt_tokens = provider_result.prompt_tokens
            record.completion_tokens = provider_result.completion_tokens
            record.total_tokens = provider_result.total_tokens
            record.usage_source = provider_result.usage_source
            record.cost_usd = provider_result.cost_usd
            record.cost_source = provider_result.cost_source
            record.estimated_prompt_tokens = provider_result.estimated_prompt_tokens
            record.estimated_completion_tokens = provider_result.estimated_completion_tokens
            record.raw_usage = dict(provider_result.raw_usage or {})
            # -- reproducibility metadata ---------------------------------
            record.seed_supported = provider_result.seed_supported
            record.seed_applied = provider_result.seed_applied
            record.seed_honour_verified = provider_result.seed_honour_verified

            raw_response = provider_result.content
            adapter_result = self.adapter.parse_and_apply(task, rep, provider_result.content)
            record.parse_status = adapter_result.parse_status
            record.parse_error = redact_secrets(adapter_result.parse_error)

            if not adapter_result.applied:
                if provider_result.finish_reason == "length":
                    # The provider stopped the generation because it hit the
                    # output limit. This is a truncation, not a malformed
                    # response, and is recorded distinctly.
                    record.status = AttemptStatus.OUTPUT_PARSE_ERROR
                    record.failure_category = FailureCategory.TRUNCATED_OUTPUT
                    record.truncated_output = True
                elif adapter_result.failure_reason == FailureReason.SYNTAX_ERROR:
                    # The model produced code, but it does not parse. This is a
                    # failed repair, not a malformed response.
                    record.status = AttemptStatus.REPAIR_FAILED
                    record.failure_category = FailureCategory.SYNTAX_ERROR
                    record.syntax_valid = False
                else:
                    record.status = AttemptStatus.OUTPUT_PARSE_ERROR
                    record.failure_category = FailureCategory.OUTPUT_PARSE_ERROR
                record.failure_reason = adapter_result.failure_reason
                record.latency_ms = (time.perf_counter() - started) * 1000.0
                return AttemptOutcome(
                    record=record,
                    adapter_result=adapter_result,
                    raw_response=raw_response,
                    prompt=prompt,
                )

            candidate_path = os.path.join(workspace, "candidate.py")
            with open(candidate_path, "w", encoding="utf-8") as fh:
                fh.write(adapter_result.candidate_source)

            syntax_error = self.adapter._syntax_error(adapter_result.candidate_source)
            record.syntax_valid = syntax_error is None
            if syntax_error is not None:
                record.status = AttemptStatus.REPAIR_FAILED
                record.failure_category = FailureCategory.SYNTAX_ERROR
                record.failure_reason = FailureReason.SYNTAX_ERROR
                record.parse_error = redact_secrets(syntax_error)
                record.latency_ms = (time.perf_counter() - started) * 1000.0
                return AttemptOutcome(
                    record=record,
                    adapter_result=adapter_result,
                    raw_response=raw_response,
                    candidate_source=adapter_result.candidate_source,
                    evaluation={"attempted": False, "reason": "candidate did not parse"},
                    prompt=prompt,
                )

            test_result = self._run_official_test(task, adapter_result.candidate_source)
            record.test_passed = test_result["passed"]
            record.test_error_type = test_result["error_type"]
            record.timed_out = bool(test_result["timed_out"])
            if test_result["passed"]:
                record.status = AttemptStatus.REPAIR_SUCCESS
                record.failure_category = FailureCategory.NONE
                record.failure_reason = None
            else:
                record.status = AttemptStatus.REPAIR_FAILED
                if test_result["timed_out"]:
                    record.failure_category = FailureCategory.TIMEOUT
                else:
                    record.failure_category = FailureCategory.TEST_FAILED
                record.failure_reason = FailureCategory.TEST_FAILED

            record.latency_ms = (time.perf_counter() - started) * 1000.0
            return AttemptOutcome(
                record=record,
                adapter_result=adapter_result,
                raw_response=raw_response,
                candidate_source=adapter_result.candidate_source,
                evaluation={
                    "attempted": True,
                    "passed": test_result["passed"],
                    "error_type": test_result["error_type"],
                    "timed_out": bool(test_result["timed_out"]),
                    "duration_ms": test_result["duration_ms"],
                    "candidate_sha256": _sha256_text(adapter_result.candidate_source),
                    "stderr_tail": redact_secrets(test_result.get("stderr_tail")),
                },
                prompt=prompt,
            )

        except Exception as exc:  # pragma: no cover - defensive
            record.status = AttemptStatus.INCOMPLETE
            record.failure_category = FailureCategory.INTERNAL_ERROR
            record.error_message = redact_secrets(f"{type(exc).__name__}: {exc}")
            record.latency_ms = (time.perf_counter() - started) * 1000.0
            return AttemptOutcome(record=record)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _provider_accepts_seed(provider) -> bool:
        """
        Whether the provider's ``complete`` can carry a request-level seed.

        The benchmark provider interface grew a ``seed`` parameter; a
        third-party implementation that predates it must keep working.  The
        capability is therefore *detected*, and an attempt that could not
        carry a seed is recorded as such instead of silently pretending.
        """
        import inspect

        try:
            parameters = inspect.signature(provider.complete).parameters
        except (TypeError, ValueError):
            return False
        if "seed" in parameters:
            return True
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    def _call_provider(self, prompt: str, seed: Optional[int]):
        """
        Calls the provider, passing the seed only when it can carry it.

        Returns ``(result, seed_supported)``.
        """
        seed_supported = self._provider_accepts_seed(self.provider)
        if seed_supported and seed is not None:
            result = self.provider.complete(
                prompt, self.system_instruction, seed=seed
            )
        else:
            result = self.provider.complete(prompt, self.system_instruction)
        if getattr(result, "seed_applied", None) is None:
            # The provider did not state whether a seed was applied; the
            # honest value is "not applied".
            result.seed_requested = seed
            result.seed_supported = False
            result.seed_applied = False
        return result, seed_supported

    @staticmethod
    def _provider_category(category: str) -> str:
        mapping = {
            "auth_error": FailureCategory.AUTH_ERROR,
            "quota_exceeded": FailureCategory.QUOTA_EXCEEDED,
            "timeout": FailureCategory.TIMEOUT,
            "network_error": FailureCategory.NETWORK_ERROR,
            "provider_error": FailureCategory.PROVIDER_ERROR,
            "configuration_error": FailureCategory.CONFIGURATION_ERROR,
            "invalid_response": FailureCategory.INVALID_RESPONSE,
        }
        return mapping.get(category, FailureCategory.PROVIDER_ERROR)

    def _run_official_test(self, task, candidate_source: str) -> Dict[str, Any]:
        """Evaluate the candidate against the official test in isolation."""
        from benchmarks.phase0.runner import run_official_test

        outcome = run_official_test(
            candidate_source,
            task.official_test,
            task.target_function,
            timeout=self.evaluation_timeout,
        )
        return {
            "passed": outcome.passed,
            "error_type": outcome.error_type,
            "timed_out": outcome.timed_out,
            "duration_ms": outcome.duration_ms,
            # Redact first, truncate second: truncating first could cut a
            # credential in half and leave an unmatchable fragment.
            "stderr_tail": redact_secrets(outcome.stderr or "")[-500:],
        }
