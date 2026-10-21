"""
Provider interface for the Phase 1 benchmark.

Two implementations are provided:

*   :class:`MockProvider` — a deterministic, offline fake.  Its responses are
    clearly marked ``is_mock=True`` and their token counts are *local
    estimates*, never provider-reported measurements.
*   :class:`LiveProvider` — a thin wrapper around the project's existing
    :class:`model_dispatcher.ModelDispatcher`.  It fails closed when the
    provider, the credential or the pinned model identifier is missing, and it
    can never fall back to a mock response.

The provider boundary is deliberately narrow: a prompt goes in, a
:class:`ProviderResult` comes out.  Everything the benchmark records about the
provider (finish reason, served model id, usage provenance, retry count) is
carried on that result object, so a live run cannot accidentally report
estimated values as measured ones.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from model_dispatcher import (  # noqa: E402
    LiveModeError,
    ModelConfigurationError,
    ModelDispatcher,
    QuotaExceededError,
)

__all__ = [
    "ProviderError",
    "ProviderResult",
    "BenchmarkProvider",
    "MockProvider",
    "LiveProvider",
    "MOCK_PROVIDER_NAME",
    "redact_secrets",
    "DYNAMIC_MODEL_ALIASES",
]


MOCK_PROVIDER_NAME = "deterministic-mock"

#: Model identifiers that route to a *different* model on each request.  They
#: are refused for a controlled experiment unless the operator explicitly opts
#: in, because the served model would then vary between attempts.
DYNAMIC_MODEL_ALIASES = {
    "openrouter/free",
    "openrouter/auto",
    "openrouter/fast",
}


class ProviderError(RuntimeError):
    """A provider-level failure that must be recorded as a failed attempt."""

    def __init__(
        self,
        message: str,
        category: str = "provider_error",
        status_code: Optional[int] = None,
        retry_count: int = 0,
    ):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        # Number of additional HTTP requests already performed for the failed
        # call. Preserved so a retried-then-failed attempt is never recorded
        # as "no retries".
        self.retry_count = int(retry_count)


#: Characters that may appear inside a credential value.  Whitespace, quotes
#: and the punctuation that commonly *delimits* a value in code or prose
#: (``,`` ``;`` ``()`` ``[]`` ``{}`` ``:``) are excluded so that a redaction
#: cannot swallow the surrounding source code.
_SECRET_VALUE_CHARS = r"[A-Za-z0-9._\-+/=~!@#$%^&*]"

_SECRET_RULES = (
    # -- Authorization headers -------------------------------------------
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?bearer\s+)[A-Za-z0-9._\-]{6,}"), r"\1[REDACTED]"),
    # Any other Authorization scheme (Basic, Digest, Negotiate, Token, ...).
    # The scheme word is kept, the credential after it is replaced.
    (
        re.compile(
            r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?"
            r"(?:basic|digest|negotiate|token|apikey|api[-_]key)\s+)[^\s'\"]{4,}"
        ),
        r"\1[REDACTED]",
    ),
    # An Authorization value with no scheme word in front of it.
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-+/=]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    # -- Provider key shapes ----------------------------------------------
    (re.compile(r"(?i)\bsk-[A-Za-z0-9._\-]{6,}"), "[REDACTED]"),
    (re.compile(r"(?i)\bor-[A-Za-z0-9._\-]{6,}"), "[REDACTED]"),
    (re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]{6,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]{6,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]{8,}"), r"\1[REDACTED]"),
    # -- Named secret assignments -----------------------------------------
    # A credential-named key with a value that looks like a secret. Two
    # narrow variants are used so that ordinary source code is not damaged:
    #   * a value that carries a digit, or
    #   * a value of at least twelve characters.
    # "password = os.getenv('PW')" and "password = getpass('x')" are therefore
    # left untouched, while "password = 'FAKEpass123456'" is redacted.
    (
        re.compile(
            r"(?i)((?:client[-_]?secret|secret|password|passwd)['\"]?\s*[:=]\s*['\"]?)"
            r"(?=" + _SECRET_VALUE_CHARS + r"*\d)"
            + _SECRET_VALUE_CHARS
            + r"{6,}"        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)((?:client[-_]?secret|secret|password|passwd)['\"]?\s*[:=]\s*['\"]?)"
            + _SECRET_VALUE_CHARS
            + r"{12,}"
        ),
        r"\1[REDACTED]",
    ),
    # -- Other well-known credential shapes --------------------------------
    # AWS-style access-key identifiers ("AKIA" + 16 upper-case characters).
    (re.compile(r"AKIA[0-9A-Z]{16}(?![A-Z0-9])"), "[REDACTED]"),
    # JWT-like strings: the first segment of a JWT always starts with "eyJ".
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}"), "[REDACTED]"),
    # Slack-like tokens.
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"), "[REDACTED]"),
    # URL basic authentication ("https://user:password@host/path"). Only the
    # password is replaced; the user and the host stay readable.
    (re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^\s/:@]+:)[^\s/@]+@"), r"\1[REDACTED]@"),
    # Unanchored, high-entropy variants. The rules above require a word
    # boundary, so a credential-shaped token that is directly adjacent to a
    # word character (e.g. "prefixsk-live-...") would slip through. These
    # variants are unanchored but require a long, high-entropy token, so
    # ordinary prose ("task-oriented") is not affected.
    (re.compile(r"(?i)sk-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"(?i)or-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"), "Bearer [REDACTED]"),
)


def contains_credential(text: Optional[str]) -> bool:
    """Return True when ``text`` contains something that looks like a credential.

    This is the single source of truth for credential *detection*: it is
    derived from the very same rule set that :func:`redact_secrets` applies,
    so the two can never drift apart.
    """
    if not text:
        return False
    return any(pattern.search(text) for pattern, _ in _SECRET_RULES)


def redact_secrets(text: Optional[str]) -> Optional[str]:
    """Remove anything that looks like a credential from a message."""
    if not text:
        return text
    redacted = text
    for pattern, replacement in _SECRET_RULES:
        redacted = pattern.sub(replacement, redacted)
    return redacted


@dataclass
class ProviderResult:
    """Everything the benchmark records about one provider response."""

    content: str
    provider: str
    requested_model_id: str
    is_mock: bool
    finish_reason: Optional[str] = None
    served_model_id: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    usage_source: str = "unavailable"
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None
    retry_count: int = 0
    latency_ms: float = 0.0
    raw_usage: Dict[str, Any] = field(default_factory=dict)
    #: Local estimates are kept strictly separate from provider-reported values.
    estimated_prompt_tokens: Optional[int] = None
    estimated_completion_tokens: Optional[int] = None
    #: The provider path that will actually be used (may differ from the
    #: declared provider when the caller did not pin it).
    effective_provider: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "requested_model_id": self.requested_model_id,
            "is_mock": self.is_mock,
            "finish_reason": self.finish_reason,
            "served_model_id": self.served_model_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "usage_source": self.usage_source,
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "retry_count": self.retry_count,
            "latency_ms": round(self.latency_ms, 3),
            "raw_usage": dict(self.raw_usage),
        }


class BenchmarkProvider:
    """Interface implemented by every provider used by the benchmark."""

    name: str = "abstract"

    def complete(self, prompt: str, system_instruction: Optional[str] = None) -> ProviderResult:
        raise NotImplementedError


class MockProvider(BenchmarkProvider):
    """
    Deterministic offline provider used for pipeline testing.

    The mock never produces a correct repair: it echoes the defective code that
    was shown in the prompt (or an equivalent no-op edit), so a mock run can
    only ever demonstrate that the *pipeline* works.  A mock result must never
    be presented as a measurement of model quality.

    Policies
    --------
    ``wrong_answer`` (default)
        A well-formed response that leaves the defect in place.
    ``malformed``
        A response that violates the requested output format, used to
        exercise the parse-failure path.
    """

    name = MOCK_PROVIDER_NAME

    def __init__(self, policy: str = "wrong_answer", model_id: str = "mock/deterministic"):
        if policy not in ("wrong_answer", "malformed"):
            raise ValueError(f"unknown mock policy: {policy}")
        self.policy = policy
        self.model_id = model_id
        self._call_index = 0

    def complete(self, prompt: str, system_instruction: Optional[str] = None) -> ProviderResult:
        self._call_index += 1
        if self.policy == "malformed":
            content = (
                "I was unable to follow the requested output format. "
                "Here is the repaired logic in prose: compare the distance to the "
                "threshold and return early when it is smaller."
            )
        else:
            content = self._synthesise(prompt)
        return ProviderResult(
            content=content,
            provider=MOCK_PROVIDER_NAME,
            requested_model_id=self.model_id,
            is_mock=True,
            finish_reason="mock",
            served_model_id=self.model_id,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            usage_source="local_estimate_not_reported",
            cost_usd=None,
            cost_source=None,
            retry_count=0,
            latency_ms=0.0,
            raw_usage={"policy": self.policy, "call_index": self._call_index},
        )

    # -- helpers --------------------------------------------------------
    def _synthesise(self, prompt: str) -> str:
        """Build a deterministic, format-plausible response for ``prompt``."""
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        if "OUTPUT FORMAT: single fenced python block" in prompt:
            source = _extract_source_block(prompt)
            return f"```python\n{source}\n```\n<!-- mock {digest} -->"
        if "OUTPUT FORMAT: search/replace block" in prompt:
            source = _extract_source_block(prompt)
            return (
                "<<<<<<< SEARCH\n"
                f"{source}\n"
                "=======\n"
                f"{source}\n"
                ">>>>>>> REPLACE\n"
                f"<!-- mock {digest} -->"
            )
        # ITMS: return the replacement node, unchanged.
        source = _extract_source_block(prompt)
        return f"```python\n{source}\n```\n<!-- mock {digest} -->"


def _extract_source_block(prompt: str) -> str:
    """Extract the source file that the prompt embedded, if any."""
    marker = "----- BEGIN FILE -----\n"
    if marker in prompt:
        body = prompt.split(marker, 1)[1]
        end = body.find("----- END FILE -----")
        if end != -1:
            return body[:end].rstrip("\n")
    return "def repaired():\n    return None\n"


class LiveProvider(BenchmarkProvider):
    """
    Live provider backed by the project's existing model dispatcher.

    Fail-closed rules
    -----------------
    *   a provider name and a pinned model identifier must be supplied,
    *   the declared provider must be the provider the dispatcher will
        actually use, and the pinned model identifier must be forwarded
        **verbatim** — a path that would remap or hardcode the model is
        refused instead of silently changing the experiment,
    *   the provider must have a credential configured,
    *   dynamic model aliases (e.g. ``openrouter/free``) are refused unless
        the operator explicitly allows them,
    *   the dispatcher is created with ``strict_live=True`` so a failed request
        raises instead of degrading into a mock response.

    The dispatcher instance is created **once** and reused for every attempt,
    so its model-catalog cache is effective: the OpenRouter ``/models``
    lookup happens at most once per run instead of once per attempt.
    """

    def __init__(
        self,
        provider: str,
        model_id: str,
        max_output_tokens: int = 2048,
        temperature: float = 0.2,
        request_timeout: float = 60.0,
        max_retries: int = 2,
        allow_dynamic_alias: bool = False,
        dispatcher: Optional[ModelDispatcher] = None,
    ):
        self.provider = (provider or "").strip().lower()
        self.model_id = (model_id or "").strip()
        self.max_output_tokens = int(max_output_tokens)
        self.temperature = float(temperature)
        self.request_timeout = float(request_timeout)
        self.max_retries = int(max_retries)
        self.allow_dynamic_alias = bool(allow_dynamic_alias)
        self.name = self.provider or "unconfigured"
        self._dispatcher = dispatcher
        self._dispatcher_owned = dispatcher is None
        self.effective_provider: Optional[str] = None
        self.catalog_checks_performed = 0

    # -- configuration --------------------------------------------------
    def _dispatcher_instance(self) -> ModelDispatcher:
        """Return the shared dispatcher, creating it once per provider."""
        if self._dispatcher is None:
            self._dispatcher = ModelDispatcher(
                force_mock=False,
                strict_live=True,
                max_retries=self.max_retries,
                request_timeout=self.request_timeout,
            )
        return self._dispatcher

    def preflight(self) -> None:
        """Validate the configuration without making any network request."""
        if not self.provider:
            raise ProviderError(
                "Live mode requires an explicit provider name.", category="configuration_error"
            )
        if not self.model_id:
            raise ProviderError(
                "Live mode requires an explicit, pinned model identifier.",
                category="configuration_error",
            )
        if self.model_id.lower() in DYNAMIC_MODEL_ALIASES and not self.allow_dynamic_alias:
            raise ProviderError(
                f"Model identifier '{self.model_id}' is a dynamic alias that may "
                f"route to different models between attempts. A controlled "
                f"experiment requires a pinned model id; pass "
                f"--allow-dynamic-alias to override.",
                category="configuration_error",
            )

        # -- provider identity -------------------------------------------
        resolved_provider = ModelDispatcher._resolve_provider(self.model_id)
        self.effective_provider = resolved_provider
        if resolved_provider != self.provider:
            raise ProviderError(
                f"Declared provider '{self.provider}' does not match the provider "
                f"that the model identifier '{self.model_id}' resolves to "
                f"('{resolved_provider}'). Refusing to run: the experiment would "
                f"silently use a different provider than the one declared.",
                category="configuration_error",
            )

        # -- model identity ------------------------------------------------
        remote_model_id = self._resolve_remote_model_id()
        if remote_model_id != self.model_id:
            raise ProviderError(
                f"Provider '{self.provider}' would send model id "
                f"'{remote_model_id}' instead of the requested '{self.model_id}'. "
                f"Refusing to run: the pinned model id cannot be honoured on this "
                f"dispatch path. Use an explicit OpenRouter-style model id "
                f"(for example 'vendor/model').",
                category="configuration_error",
            )

        from config import AppConfig

        if not AppConfig.resolve_api_key(self.provider):
            raise ProviderError(
                f"No credential configured for provider '{self.provider}'. Live "
                f"mode is fail-closed: no request was attempted.",
                category="configuration_error",
            )

    def _resolve_remote_model_id(self) -> str:
        """Return the model id the dispatcher would put on the wire."""
        from config import AppConfig

        try:
            base_url = AppConfig.resolve_base_url(self.provider)
            return ModelDispatcher._resolve_remote_model_id(
                self.model_id, self.provider, base_url
            )
        except Exception as exc:
            raise ProviderError(
                f"Model id '{self.model_id}' cannot be used with provider "
                f"'{self.provider}': {type(exc).__name__}: {exc}",
                category="configuration_error",
            ) from exc

    # -- inference ------------------------------------------------------
    def complete(self, prompt: str, system_instruction: Optional[str] = None) -> ProviderResult:
        self.preflight()
        dispatcher = self._dispatcher_instance()
        try:
            response = dispatcher.generate_live(
                prompt=prompt,
                model_name=self.model_id,
                system_instruction=system_instruction,
                max_output_tokens=self.max_output_tokens,
                temperature=self.temperature,
            )
        except ModelConfigurationError as exc:
            raise ProviderError(
                redact_secrets(str(exc)) or "model configuration error",
                category="configuration_error",
                retry_count=int(getattr(exc, "retry_count", 0) or 0),
            ) from exc
        except QuotaExceededError as exc:
            raise ProviderError(
                redact_secrets(str(exc)) or "quota exhausted",
                category="quota_exceeded",
                status_code=getattr(exc, "status_code", None),
                retry_count=int(getattr(exc, "retry_count", 0) or 0),
            ) from exc
        except LiveModeError as exc:
            raise ProviderError(
                redact_secrets(str(exc)) or "live request failed",
                category=getattr(exc, "category", "provider_error"),
                status_code=getattr(exc, "status_code", None),
                retry_count=int(getattr(exc, "retry_count", 0) or 0),
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError(
                redact_secrets(f"{type(exc).__name__}: {exc}") or "live request failed",
                category="provider_error",
                retry_count=int(getattr(exc, "retry_count", 0) or 0),
            ) from exc

        # -- token provenance (per field) ----------------------------------
        # Only values the provider actually sent are treated as
        # provider-reported. Local estimates stay in their own fields and are
        # never mixed into a provider-reported total.
        provider_prompt = getattr(response, "provider_prompt_tokens", None)
        provider_completion = getattr(response, "provider_completion_tokens", None)
        provider_total = getattr(response, "provider_total_tokens", None)
        usage_source = getattr(response, "usage_source", "local_estimate")

        return ProviderResult(
            content=response.content,
            provider=response.provider,
            requested_model_id=self.model_id,
            is_mock=bool(getattr(response, "is_mock", False)),
            finish_reason=getattr(response, "finish_reason", None),
            # Only what the provider actually reported. Never a guess.
            served_model_id=getattr(response, "served_model_id", None),
            prompt_tokens=provider_prompt,
            completion_tokens=provider_completion,
            total_tokens=provider_total,
            usage_source=usage_source,
            cost_usd=getattr(response, "cost_usd", None),
            cost_source=getattr(response, "cost_source", None),
            retry_count=int(getattr(response, "retry_count", 0) or 0),
            latency_ms=float(getattr(response, "latency_ms", 0.0) or 0.0),
            raw_usage={
                "cached_input_tokens": response.cached_tokens_in,
                "reasoning_tokens": response.reasoning_tokens,
            },
            estimated_prompt_tokens=getattr(response, "estimated_prompt_tokens", None),
            estimated_completion_tokens=getattr(response, "estimated_completion_tokens", None),
            effective_provider=self.effective_provider,
        )
