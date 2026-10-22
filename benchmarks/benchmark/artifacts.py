"""
Per-attempt artefact persistence for the Phase 1 benchmark.

For every attempt the store writes a small, self-describing directory:

    <run-output>/artifacts/<attempt_id>/attempt.json        # always
    <run-output>/artifacts/<attempt_id>/raw_response.json   # only if a response exists
    <run-output>/artifacts/<attempt_id>/candidate.py        # only if a candidate exists

``attempt.json`` ties the artefacts together: it carries the attempt
identity (attempt id, task, method, repetition, requested and served model
ids), the digests of the persisted files, and the official-evaluation
outcome associated with the candidate digest.

Redaction policy
----------------
*   The **candidate** is stored byte-for-byte, because its digest must match
    the bytes that were handed to the official evaluator. It is scanned for
    credential-shaped strings; if one is found (it should never be, since the
    model is never given a credential) the stored copy is redacted, the
    attempt is flagged ``candidate_redacted`` and both digests are recorded.
*   The **raw response** is free-form model text and is always passed through
    :func:`redact_secrets` before it is written. The digest of the original
    (pre-redaction) text is kept as provenance.
*   Structured metadata is written with credential-like keys removed.
*   No environment variable, authorization header or API key is ever
    written.

The store never overwrites an existing attempt directory: a re-used output
directory gets a run-scoped ``artifacts-<run_id>`` root instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from typing import Any, Dict, Optional

from benchmarks.benchmark.provider import contains_credential, redact_secrets

__all__ = [
    "ArtifactStore",
    "ArtifactPersistenceError",
    "sha256_bytes",
    "sha256_text",
    "resolve_artifact_root",
]


class ArtifactPersistenceError(RuntimeError):
    """Raised when artefacts for an attempt could not be written."""


_CREDENTIAL_LIKE_KEYS = re.compile(
    r"(?i)(api[_-]?key|authorization|auth[_-]?token|access[_-]?token|bearer|"
    r"secret|password|passwd|credential|cookie|set-cookie|session[_-]?id|"
    r"x-api-key|openai_api_key|openrouter_api_key)"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def _scrub_mapping(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Drop credential-like keys and redact credential-shaped values."""
    cleaned: Dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if _CREDENTIAL_LIKE_KEYS.search(str(key)):
            continue
        if isinstance(value, str):
            cleaned[key] = redact_secrets(value)
        elif isinstance(value, dict):
            cleaned[key] = _scrub_mapping(value)
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[key] = value
        else:
            cleaned[key] = redact_secrets(str(value))
    return cleaned


def resolve_artifact_root(output_dir: str, run_id: str) -> str:
    """Return the artefact root that ``run_id`` uses inside ``output_dir``.

    The plain ``<output_dir>/artifacts`` directory is used while it is free.
    As soon as a previous run has left files in it, a run-scoped
    ``<output_dir>/artifacts-<run_id>`` root is used instead, so two runs
    never share an artefact and an earlier run is never overwritten.

    This is the single place that decides the root.  The store and the run
    metadata both call it, so ``run_metadata.json`` / ``summary.json`` always
    point at the directory that actually holds the artefacts.
    """
    base = os.path.abspath(output_dir)
    plain = os.path.join(base, "artifacts")
    if os.path.isdir(plain) and os.listdir(plain):
        # A previous run already owns the plain directory: use a
        # run-scoped root so two runs never share an artefact.
        return os.path.join(base, f"artifacts-{run_id}")
    return plain


class ArtifactStore:
    """Writes the per-attempt artefacts of one run."""

    def __init__(self, output_dir: str, run_id: str):
        self.output_dir = os.path.abspath(output_dir)
        self.run_id = run_id
        self.root = self._resolve_root()

    def _resolve_root(self) -> str:
        return resolve_artifact_root(self.output_dir, self.run_id)

    # -- helpers --------------------------------------------------------
    def _relative(self, path: str) -> str:
        return os.path.relpath(path, self.output_dir).replace(os.sep, "/")

    def _write(self, path: str, data: bytes) -> str:
        """Write a file atomically and return the digest of the written bytes.

        The data is written to a temporary file in the same directory and then
        renamed, so a failure can never leave a half-written artefact behind.
        """
        tmp_path = f"{path}.tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp_path, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except OSError as exc:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            raise ArtifactPersistenceError(
                f"could not write artefact {self._relative(path)}: {exc}"
            ) from exc
        return sha256_bytes(data)

    def attempt_dir(self, attempt_id: str) -> str:
        """Return the absolute directory that holds an attempt's artefacts."""
        return os.path.join(self.root, attempt_id)

    def relative_attempt_dir(self, attempt_id: str) -> str:
        """Return the attempt directory relative to the run output directory."""
        return self._relative(self.attempt_dir(attempt_id))

    # -- main entry point ------------------------------------------------
    def persist(self, outcome) -> Dict[str, Any]:
        """Persist the artefacts of one attempt and return the record fields.

        ``outcome`` is an :class:`benchmarks.benchmark.pipeline.AttemptOutcome`.
        The returned mapping is meant to be merged into the attempt record.
        """
        record = outcome.record
        attempt_dir = os.path.join(self.root, record.attempt_id)
        if os.path.exists(attempt_dir):
            raise ArtifactPersistenceError(
                f"refusing to overwrite an existing artefact directory: "
                f"{self._relative(attempt_dir)}"
            )
        try:
            os.makedirs(attempt_dir, exist_ok=False)
        except OSError as exc:
            raise ArtifactPersistenceError(
                f"could not create artefact directory {self._relative(attempt_dir)}: {exc}"
            ) from exc

        info: Dict[str, Any] = {
            "artifacts_path": self._relative(attempt_dir),
            "artifacts_status": "ok",
            "candidate_state": "none",
            "candidate_path": None,
            "candidate_sha256": None,
            "candidate_file_sha256": None,
            "candidate_redacted": False,
            "raw_response_path": None,
            "raw_response_sha256": None,
            "prompt_path": None,
            "prompt_sha256": None,
            "prompt_file_sha256": None,
        }

        # -- raw response -------------------------------------------------
        raw_response = getattr(outcome, "raw_response", None)
        if raw_response is not None:
            original_digest = sha256_text(raw_response)
            redacted_text = redact_secrets(raw_response) or ""
            payload = {
                "attempt_id": record.attempt_id,
                "run_id": record.run_id,
                "task_id": record.task_id,
                "humaneval_task_id": record.humaneval_task_id,
                "method": record.method,
                "rep": record.rep,
                "mode": record.mode,
                "provider": record.provider,
                "requested_model_id": record.requested_model_id,
                "served_model_id": record.served_model_id,
                "finish_reason": record.finish_reason,
                "usage_source": record.usage_source,
                "is_mock": record.is_mock,
                "redaction_applied": redacted_text != raw_response,
                "response_original_sha256": original_digest,
                "response_bytes": len(raw_response.encode("utf-8")),
                "response_text": redacted_text,
            }
            path = os.path.join(attempt_dir, "raw_response.json")
            digest = self._write(
                path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            )
            info["raw_response_path"] = self._relative(path)
            info["raw_response_sha256"] = digest

        # -- candidate ----------------------------------------------------
        candidate = getattr(outcome, "candidate_source", None)
        if candidate is not None:
            evaluated_bytes = candidate.encode("utf-8")
            evaluated_digest = sha256_bytes(evaluated_bytes)
            # The trigger is the very same rule set that performs the
            # redaction, so detection and redaction cannot drift apart.
            credential_shaped = contains_credential(candidate)
            if credential_shaped:
                stored_bytes = (redact_secrets(candidate) or "").encode("utf-8")
            else:
                stored_bytes = evaluated_bytes
            path = os.path.join(attempt_dir, "candidate.py")
            file_digest = self._write(path, stored_bytes)
            info["candidate_state"] = "produced"
            info["candidate_path"] = self._relative(path)
            # The digest always identifies the bytes that were evaluated.
            info["candidate_sha256"] = evaluated_digest
            info["candidate_redacted"] = stored_bytes != evaluated_bytes
            info["candidate_file_sha256"] = file_digest

        # -- prompt -------------------------------------------------------
        # The exact request that was sent is stored next to the response, so
        # the "every strategy received the same instruction" claim can be
        # audited instead of asserted.
        prompt = getattr(outcome, "prompt", None)
        if prompt is not None:
            original_digest = sha256_text(prompt)
            redacted_prompt = redact_secrets(prompt) or ""
            prompt_meta = _scrub_mapping(
                {
                    "attempt_id": record.attempt_id,
                    "run_id": record.run_id,
                    "task_id": record.task_id,
                    "humaneval_task_id": record.humaneval_task_id,
                    "method": record.method,
                    "rep": record.rep,
                    "mode": record.mode,
                    "provider": record.provider,
                    "requested_model_id": record.requested_model_id,
                    "seed_requested": record.seed_requested,
                    "max_output_tokens": record.max_output_tokens,
                    "prompt_original_sha256": original_digest,
                    "prompt_bytes": len(prompt.encode("utf-8")),
                    "prompt_token_estimate": record.prompt_token_estimate,
                    "redaction_applied": redacted_prompt != prompt,
                }
            )
            prompt_path = os.path.join(attempt_dir, "prompt.txt")
            prompt_digest = self._write(prompt_path, redacted_prompt.encode("utf-8"))
            meta_path = os.path.join(attempt_dir, "prompt.json")
            self._write(
                meta_path,
                (json.dumps(prompt_meta, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
            )
            info["prompt_path"] = self._relative(prompt_path)
            info["prompt_sha256"] = original_digest
            info["prompt_file_sha256"] = prompt_digest

        # -- attempt manifest ---------------------------------------------
        evaluation = getattr(outcome, "evaluation", None)
        provider_error = getattr(outcome, "provider_error", None)
        attempt_payload = _scrub_mapping(
            {
                "attempt_id": record.attempt_id,
                "run_id": record.run_id,
                "timestamp_utc": record.timestamp_utc,
                "task_id": record.task_id,
                "humaneval_task_id": record.humaneval_task_id,
                "method": record.method,
                "rep": record.rep,
                "mode": record.mode,
                "provider": record.provider,
                "requested_model_id": record.requested_model_id,
                "served_model_id": record.served_model_id,
                "status": record.status,
                "failure_category": record.failure_category,
                "failure_reason": record.failure_reason,
                "is_mock": record.is_mock,
                "finish_reason": record.finish_reason,
                "usage_source": record.usage_source,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "total_tokens": record.total_tokens,
                "estimated_prompt_tokens": record.estimated_prompt_tokens,
                "estimated_completion_tokens": record.estimated_completion_tokens,
                "cost_usd": record.cost_usd,
                "cost_source": record.cost_source,
                "retry_count": record.retry_count,
                "timed_out": record.timed_out,
                "parse_status": record.parse_status,
                "parse_error": record.parse_error,
                "error_message": record.error_message,
                "candidate_state": info["candidate_state"],
                "candidate_path": info["candidate_path"],
                "candidate_sha256": info["candidate_sha256"],
                "candidate_file_sha256": info.get("candidate_file_sha256"),
                "candidate_redacted": info["candidate_redacted"],
                "raw_response_path": info["raw_response_path"],
                "raw_response_sha256": info["raw_response_sha256"],
                "prompt_path": info.get("prompt_path"),
                "prompt_sha256": info.get("prompt_sha256"),
                "prompt_file_sha256": info.get("prompt_file_sha256"),
                "prompt_token_estimate": record.prompt_token_estimate,
                "seed_requested": record.seed_requested,
                "seed_supported": record.seed_supported,
                "seed_applied": record.seed_applied,
                "seed_honour_verified": record.seed_honour_verified,
                "max_output_tokens": record.max_output_tokens,
                "evaluation": evaluation,
                "provider_error": provider_error,
                "notes": [
                    "The candidate digest identifies the exact bytes submitted to "
                    "the official evaluator.",
                    "The raw response is stored after secret redaction; the digest "
                    "of the original text is kept as provenance.",
                    "The prompt is stored after secret redaction; the digest of the "
                    "original prompt bytes is kept as provenance.",
                    "seed_honour_verified is false unless a repeated-request probe "
                    "proved that the provider honours the seed.",
                ],
            }
        )
        self._write(
            os.path.join(attempt_dir, "attempt.json"),
            (json.dumps(attempt_payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        return info
