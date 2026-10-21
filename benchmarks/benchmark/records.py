"""
Attempt records and durable exports for the Phase 1 benchmark.

Every attempt — successful, failed, malformed or provider-error — produces one
:class:`AttemptRecord`.  Records are written incrementally (JSON Lines, one
line per attempt, flushed and fsynced) so an interrupted run never destroys
the attempts that already completed.

Unknown measurements are stored as ``None``.  An unmeasured value is never
written as ``0``, and an estimate is never labelled as a provider measurement.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "AttemptStatus",
    "FailureCategory",
    "AttemptRecord",
    "JsonlWriter",
    "CsvWriter",
    "write_json",
]


class AttemptStatus:
    """Outcome of one attempt."""

    REPAIR_SUCCESS = "repair_success"
    REPAIR_FAILED = "repair_failed"
    OUTPUT_PARSE_ERROR = "output_parse_error"
    PROVIDER_ERROR = "provider_error"
    INCOMPLETE = "incomplete"


class FailureCategory:
    """Normalised failure categories (kept deliberately small and stable)."""

    NONE = "none"
    SYNTAX_ERROR = "syntax_error"
    TEST_FAILED = "test_failed"
    OUTPUT_PARSE_ERROR = "output_parse_error"
    TRUNCATED_OUTPUT = "truncated_output"
    TIMEOUT = "timeout"
    AUTH_ERROR = "auth_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    CONFIGURATION_ERROR = "configuration_error"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL_ERROR = "internal_error"


CSV_COLUMNS = [
    "attempt_id",
    "run_id",
    "timestamp_utc",
    "task_id",
    "humaneval_task_id",
    "method",
    "rep",
    "mode",
    "provider",
    "requested_model_id",
    "served_model_id",
    "status",
    "failure_category",
    "failure_reason",
    "syntax_valid",
    "test_passed",
    "test_error_type",
    "parse_status",
    "parse_error",
    "finish_reason",
    "truncated_output",
    "timed_out",
    "retry_count",
    "latency_ms",
    "provider_latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "usage_source",
    "estimated_prompt_tokens",
    "estimated_completion_tokens",
    "cost_usd",
    "cost_source",
    "is_mock",
    "error_message",
    "raw_usage",
    "artifacts_path",
    "artifacts_status",
    "candidate_state",
    "candidate_path",
    "candidate_sha256",
    "candidate_file_sha256",
    "candidate_redacted",
    "raw_response_path",
    "raw_response_sha256",
    "evaluation_attempted",
]


@dataclass
class AttemptRecord:
    """One recorded attempt."""

    attempt_id: str
    run_id: str
    timestamp_utc: str
    task_id: str
    humaneval_task_id: str
    method: str
    rep: int
    mode: str
    provider: str
    requested_model_id: Optional[str]
    served_model_id: Optional[str] = None
    status: str = AttemptStatus.INCOMPLETE
    failure_category: str = FailureCategory.NONE
    failure_reason: Optional[str] = None
    syntax_valid: Optional[bool] = None
    test_passed: Optional[bool] = None
    test_error_type: Optional[str] = None
    parse_status: Optional[str] = None
    parse_error: Optional[str] = None
    finish_reason: Optional[str] = None
    timed_out: bool = False
    retry_count: int = 0
    latency_ms: Optional[float] = None
    provider_latency_ms: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    usage_source: str = "unavailable"
    #: Local token estimates. These are never provider-reported and are kept
    #: out of every provider-reported total.
    estimated_prompt_tokens: Optional[int] = None
    estimated_completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None
    is_mock: bool = False
    truncated_output: bool = False
    error_message: Optional[str] = None
    raw_usage: Dict[str, Any] = field(default_factory=dict)
    # -- artefact trail -------------------------------------------------
    #: Directory holding this attempt's artefacts (relative to the run dir).
    artifacts_path: Optional[str] = None
    #: "ok" (artefacts written), "failed" (persistence error) or
    #: "not_persisted" (the runner never got to it).
    artifacts_status: str = "not_persisted"
    #: "none" when the attempt produced no candidate, "produced" otherwise.
    candidate_state: str = "none"
    candidate_path: Optional[str] = None
    #: SHA-256 of the exact bytes submitted to the official evaluator. This
    #: is the provenance anchor and never changes.
    candidate_sha256: Optional[str] = None
    #: SHA-256 of the bytes actually stored in ``candidate_path``. It equals
    #: ``candidate_sha256`` unless the stored copy had to be redacted, in
    #: which case the two differ and ``candidate_redacted`` is set.
    candidate_file_sha256: Optional[str] = None
    candidate_redacted: bool = False
    raw_response_path: Optional[str] = None
    raw_response_sha256: Optional[str] = None
    #: Whether the official evaluator was actually run for this attempt.
    evaluation_attempted: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_csv_row(self) -> Dict[str, Any]:
        row = self.to_dict()
        row["raw_usage"] = json.dumps(row.get("raw_usage") or {}, ensure_ascii=False)
        return {key: row.get(key) for key in CSV_COLUMNS}


def write_json(path: str, payload: Any) -> str:
    """Write ``payload`` as pretty JSON, creating parent directories."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")
    return path


class JsonlWriter:
    """Append-only JSON Lines writer with immediate durability."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def append(self, record: AttemptRecord) -> None:
        self._fh.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class CsvWriter:
    """Append-only CSV writer with a stable column order."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        exists = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        self._fh = open(self.path, "a", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_COLUMNS)
        if not exists:
            self._writer.writeheader()
            self._fh.flush()

    def append(self, record: AttemptRecord) -> None:
        self._writer.writerow(record.to_csv_row())
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "CsvWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
