"""
Shared interface for the three real code-editing *systems* under comparison.

A "system" here is a complete editing agent, not a prompt template:

* ``itms``   -- this project's ITMS pipeline (skeletonise -> LLM -> AST splice)
* ``aider``  -- the real ``aider`` CLI, run as a subprocess
* ``cline``  -- the real ``cline`` CLI, run as a subprocess

Every system receives the *same* task instruction and the *same* starting file,
works in its own isolated workspace, and is graded by the *same* hidden
official test through the *same* evaluator.  The only thing that differs is
how the system edits the file, which is the point of the experiment.

The module deliberately contains no network calls.  Live traffic happens only
inside the subprocess / provider invocations performed by the adapters, and
only when the runner is executed in ``--mode live``.
"""

from __future__ import annotations

import abc
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = [
    "TaskBundle",
    "SystemOutcome",
    "SystemAdapter",
    "FailureCategory",
    "UsageSource",
]


class FailureCategory:
    """Normalised failure categories, shared with the Phase 1 record taxonomy."""

    NONE = "none"
    # --- execution failures (the system never produced a candidate) ---------
    TOOL_NOT_AVAILABLE = "tool_not_available"
    CONFIGURATION_ERROR = "configuration_error"
    AUTH_ERROR = "auth_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    INTERNAL_ERROR = "internal_error"
    # --- editing failures (a candidate exists but is not usable) ------------
    NO_CANDIDATE = "no_candidate"
    PATCH_NOT_APPLIED = "patch_not_applied"
    SYNTAX_ERROR = "syntax_error"
    TARGET_FUNCTION_MISSING = "target_function_missing"
    # --- evaluation failures (candidate ran, hidden test failed) -------------
    TEST_FAILED = "test_failed"


class UsageSource:
    """Where a token / cost number came from.  Never mixed."""

    PROVIDER_REPORTED = "provider_reported"     # the model API returned usage
    TOOL_REPORTED = "tool_reported"             # the CLI's own summary/JSON
    LOCAL_ESTIMATE = "local_estimate"           # estimated from byte counts
    MISSING = "missing"                         # not reported at all -> None


@dataclass(frozen=True)
class TaskBundle:
    """One task, in the shape every system adapter consumes."""

    task_id: str
    humaneval_task_id: str
    target_function: str
    problem_prompt: str
    repair_instruction: str
    mutated_source: str
    mutated_source_path: str
    official_test_path: str

    @property
    def workspace_filename(self) -> str:
        """The name of the single file every system is allowed to edit."""
        return "candidate.py"


@dataclass
class SystemOutcome:
    """Everything one system produced for one (task, repetition) pair.

    ``execution_completed`` separates *tool* failures from *evaluator*
    failures.  A system that ran but produced an unparsable answer has
    ``execution_completed=True`` and a non-``none`` ``failure_category``; a
    system that never ran has ``execution_completed=False`` and no candidate.
    """

    system: str
    task_id: str
    repetition_id: str
    seed_requested: Optional[int] = None
    seed_supported: bool = False
    seed_applied: Optional[int] = None
    execution_completed: bool = False
    execution_error: Optional[str] = None
    exit_code: Optional[int] = None
    timed_out: bool = False
    latency_ms: float = 0.0
    candidate_source: Optional[str] = None
    candidate_sha256: Optional[str] = None
    patch_applied: bool = False
    syntax_valid: Optional[bool] = None
    failure_category: str = FailureCategory.NONE
    failure_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    usage_source: str = UsageSource.MISSING
    cost_usd: Optional[float] = None
    cost_source: str = UsageSource.MISSING
    telemetry_source: str = "none"
    served_model_id: Optional[str] = None
    provider: Optional[str] = None
    raw_telemetry: Dict[str, Any] = field(default_factory=dict)
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "system": self.system,
            "task_id": self.task_id,
            "repetition_id": self.repetition_id,
            "seed_requested": self.seed_requested,
            "seed_supported": self.seed_supported,
            "seed_applied": self.seed_applied,
            "execution_completed": self.execution_completed,
            "execution_error": self.execution_error,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "latency_ms": round(self.latency_ms, 2),
            "candidate_sha256": self.candidate_sha256,
            "patch_applied": self.patch_applied,
            "syntax_valid": self.syntax_valid,
            "failure_category": self.failure_category,
            "failure_reason": self.failure_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "usage_source": self.usage_source,
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "telemetry_source": self.telemetry_source,
            "served_model_id": self.served_model_id,
            "provider": self.provider,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "extra": self.extra,
        }
        return payload


class SystemAdapter(abc.ABC):
    """Base class for a real code-editing system."""

    #: stable machine name used in records and in the manifest
    name: str = "base"
    #: whether the system can be given a request-level random seed
    supports_seed: bool = False
    #: whether the system needs a network credential to run
    requires_credential: bool = True

    @abc.abstractmethod
    def preflight(self) -> Dict[str, Any]:
        """Report whether this system can run here, without running it.

        Must not perform a live model call.  May check that a CLI is on PATH
        and that a credential is configured.
        """

    @abc.abstractmethod
    def run(
        self,
        task: TaskBundle,
        workspace: str,
        repetition_id: str,
        seed: Optional[int] = None,
    ) -> SystemOutcome:
        """Run the system once inside ``workspace`` and return its outcome."""

    def describe_configuration(self) -> Dict[str, Any]:
        """Configuration echoed into run metadata (never contains secrets)."""
        return {
            "system": self.name,
            "supports_seed": self.supports_seed,
            "requires_credential": self.requires_credential,
        }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repo_root() -> str:
    """Absolute path of the repository root.

    ``<root>/experiments/itms_aider_cline_v1/systems/<this file>`` -> ``<root>``.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def load_app_config():
    """Import the repository's ``config.py`` (which loads ``.env``).

    Returns the module, or ``None`` when it cannot be loaded.  The module is
    imported directly from its path so that the experiment directory does not
    need to be on ``sys.path``.
    """
    import importlib.util

    path = os.path.join(repo_root(), "config.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("itms_app_config", path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_provider_key(provider: str, fallback_env: Optional[str] = None) -> Optional[str]:
    """Resolve an API key for ``provider`` without ever printing it.

    Order: an explicitly exported environment variable, then the
    repository's ``config.py`` (which reads the project's ``.env``).
    """
    if fallback_env:
        value = os.environ.get(fallback_env)
        if value and value.strip():
            return value.strip()
    module = load_app_config()
    if module is not None:
        try:
            value = module.AppConfig.resolve_api_key(provider)
        except Exception:
            value = None
        if value and value.strip():
            return value.strip()
    return None


def which(binary: str) -> Optional[str]:
    """Locate an executable without shelling out."""
    from shutil import which as _which

    return _which(binary)


def read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def sanitised_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a minimal environment for a subprocess.

    The credential is passed through explicitly by the caller (never on the
    command line, where it would be visible in ``ps``).  ``HOME`` is kept so
    the CLIs can find their own configuration, and ``TMPDIR``/``PATH``/``LANG``
    are kept so they can run at all.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "HOME": os.environ.get("HOME", os.path.expanduser("~")),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if extra:
        env.update({k: v for k, v in extra.items() if v is not None})
    return env
