import time
import os
import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Callable
import httpx
from config import AppConfig
from prompt_manager import get_token_count

# Matches an explicit OpenRouter catalog identifier such as "openrouter/free"
# or "meta-llama/llama-3.3-70b-instruct:free". Whitespace is disallowed so that
# display labels like "Claude Sonnet 5.5 / Fable" are not mistaken for IDs.
OPENROUTER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/-]*$")

@dataclass
class ModelResponse:
    """Standardized response from any LLM provider or deterministic mock engine.

    The extra fields are additive and backward compatible.  They exist so a
    benchmark can record what the provider actually reported:

    ``finish_reason``       the provider's stop reason ("stop", "length", ...)
    ``served_model_id``     the model identifier the provider says it served
    ``retry_count``         how many additional HTTP requests were made
    ``usage_source``        "provider_reported" | "provider_partial" |
                            "local_estimate" | "local_estimate_not_reported"
    ``cost_usd``            provider-supplied cost only, otherwise ``None``

    Token provenance is tracked **per field**: ``provider_prompt_tokens``,
    ``provider_completion_tokens`` and ``provider_total_tokens`` are only set
    when the provider actually supplied that value. Local tiktoken estimates
    are kept separately in ``estimated_prompt_tokens`` /
    ``estimated_completion_tokens`` and are never mixed into a
    provider-reported total.
    """
    content: str
    raw_tokens_in: int
    cached_tokens_in: int
    output_tokens: int
    reasoning_tokens: int
    model_name: str
    provider: str
    latency_ms: float
    is_mock: bool = False
    finish_reason: Optional[str] = None
    served_model_id: Optional[str] = None
    retry_count: int = 0
    usage_source: str = "provider_reported"
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None
    provider_prompt_tokens: Optional[int] = None
    provider_completion_tokens: Optional[int] = None
    provider_total_tokens: Optional[int] = None
    estimated_prompt_tokens: Optional[int] = None
    estimated_completion_tokens: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --- OpenRouter model capability verification ---------------------------------
# Parameters that only exist on OpenAI-style /chat/completions payloads. A model
# that advertises none of them cannot serve the chat-completions endpoint.

CHAT_COMPLETIONS_PARAMETERS = frozenset({
    "max_tokens",
    "temperature",
    "top_p",
    "stop",
    "tools",
    "tool_choice",
    "response_format",
    "seed",
    "frequency_penalty",
    "presence_penalty",
    "logit_bias",
})


class ModelConfigurationError(RuntimeError):
    """Raised when a requested model cannot be used with the selected provider."""


class LiveModeError(RuntimeError):
    """
    Raised when a live measurement was required but could not be performed.

    Covers authentication failures, timeouts, provider errors and any other
    condition that prevented a genuine live inference. A benchmark must record
    this as a FAILURE; it must never be replaced by a mock response.
    """

    def __init__(self, message: str, category: str = "provider_error", status_code: Optional[int] = None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        # Number of additional HTTP requests that were already performed for
        # this call before it failed. Preserved so a failed attempt is not
        # reported as "no retries".
        self.retry_count = 0


class QuotaExceededError(LiveModeError):
    """
    Raised when the upstream gateway rejects a request because a quota or
    credit limit has been exhausted (HTTP 429 / 402).

    This error is never masked by a deterministic mock response: a quota
    failure means no real inference happened, and reporting a mock result as
    if it were a live one would misrepresent the run.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message, category="quota_exceeded", status_code=status_code)


def _classify_live_error(exc: Exception, provider: str, model_name: str) -> LiveModeError:
    """
    Converts a raw transport/provider exception into a categorised LiveModeError.

    Categories: auth_error, timeout, quota_exceeded, provider_error, network_error.
    The HTTP status code is preserved so a benchmark can record the exact cause
    of a failure instead of collapsing every error into one generic message.
    The number of retries that were already performed is carried over, so a
    request that was retried twice and then failed is not recorded as
    "no retries".
    """
    status_code = None
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)

    if status_code in (401, 403):
        category = "auth_error"
    elif status_code == 429 or status_code == 402:
        category = "quota_exceeded"
    elif status_code is not None and status_code >= 500:
        category = "provider_error"
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        # Checked before TransportError: httpx.TimeoutException is a subclass.
        category = "timeout"
    elif isinstance(exc, httpx.TransportError):
        category = "network_error"
    else:
        category = "provider_error"

    message = (
        f"Live API call failed for provider '{provider}' and model '{model_name}' "
        f"({category}"
        + (f", HTTP {status_code}" if status_code is not None else "")
        + f"): {type(exc).__name__}"
    )
    error = LiveModeError(message, category=category, status_code=status_code)
    error.retry_count = int(getattr(exc, "retry_count", 0) or 0)
    return error


def _is_daily_quota_response(resp) -> bool:
    """
    Returns True when a 429 response describes a daily/periodic quota cap
    (e.g. OpenRouter's "free-models-per-day"), which cannot clear within a
    benchmark run and must therefore not be retried.
    """
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        message = ""
        if isinstance(err, dict):
            message = str(err.get("message") or "").lower()
        elif isinstance(err, str):
            message = err.lower()
        elif isinstance(body, dict):
            message = str(body.get("message") or "").lower()
    except Exception:
        return False

    markers = ("per-day", "per day", "daily", "free-models-per-day")
    return any(marker in message for marker in markers)


def _describe_quota_response(resp, provider_name: str, model_name: str) -> str:
    """Builds an actionable message for a 429/402 quota response."""
    detail = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or "").strip()
        elif isinstance(err, str):
            detail = err.strip()
    except Exception:
        detail = ""

    headers = getattr(resp, "headers", {}) or {}
    limit = headers.get("x-ratelimit-limit")
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    retry_after = headers.get("retry-after")

    parts = [
        f"Quota exhausted for provider '{provider_name}' and model '{model_name}' "
        f"(HTTP {getattr(resp, 'status_code', '?')})."
    ]
    if detail:
        parts.append(detail)
    if limit or remaining:
        parts.append(f"Rate limit window: limit={limit}, remaining={remaining}.")
    if reset:
        parts.append(f"Quota resets at epoch {reset}.")
    if retry_after:
        parts.append(f"Retry after {retry_after}s.")
    if not detail and not (limit or remaining):
        parts.append("The account has no remaining request quota for this model.")
    return " ".join(parts)


def _as_int(value) -> Optional[int]:
    """Return the value when it is a genuine integer, otherwise ``None``.

    Booleans are rejected: ``True`` is not a token count.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


@dataclass
class ChatSupportResult:
    """Outcome of a chat-completions capability check for a model ID."""
    model_id: str
    supported: Optional[bool]  # True = supported, False = unsupported, None = unknown
    reason: str
    catalog_entry: Optional[Dict[str, Any]] = None

    @property
    def is_usable(self) -> bool:
        return self.supported is not False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "supported": self.supported,
            "reason": self.reason,
        }


def fetch_openrouter_models(api_key: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves the OpenRouter model catalog. Requires network access."""
    base = (base_url or AppConfig.resolve_base_url("openrouter")).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    headers.update(AppConfig.get_openrouter_headers())
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{base}/models", headers=headers)
        resp.raise_for_status()
        return resp.json()


def verify_openrouter_chat_support(
    model_id: str,
    models_payload: Optional[Dict[str, Any]] = None,
    fetch_models: Optional[Callable[[], Dict[str, Any]]] = None
) -> ChatSupportResult:
    """
    Verifies that a model ID is exposed by OpenRouter and supports the
    /chat/completions endpoint.

    `models_payload` / `fetch_models` are injectable so this can be exercised
    without any network access.
    """
    mid = (model_id or "").strip()

    payload = models_payload
    if payload is None:
        if fetch_models is None:
            return ChatSupportResult(mid, None, "no catalog supplied and no fetcher configured")
        try:
            payload = fetch_models()
        except Exception as e:
            return ChatSupportResult(mid, None, f"catalog lookup failed: {type(e).__name__}")

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return ChatSupportResult(mid, None, "catalog payload did not contain a 'data' list")

    entry = None
    for item in entries:
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "")).strip().lower() == mid.lower():
            entry = item
            break

    if entry is None:
        return ChatSupportResult(
            mid, None,
            f"model '{mid}' is not listed in the OpenRouter /models catalog "
            "(the router may still accept it at request time)"
        )

    endpoints = entry.get("endpoints")
    if endpoints is not None:
        if isinstance(endpoints, dict):
            names = set(endpoints.keys())
        elif isinstance(endpoints, (list, tuple, set)):
            names = {str(n) for n in endpoints}
        else:
            names = set()
        if names and not any("chat" in n.lower() for n in names):
            return ChatSupportResult(mid, False, "catalog entry does not advertise a chat/completions endpoint", entry)

    supported_params = entry.get("supported_parameters")
    if supported_params is not None:
        params = {str(p) for p in supported_params}
        if not (params & CHAT_COMPLETIONS_PARAMETERS):
            return ChatSupportResult(
                mid, False,
                "catalog entry advertises no chat-completions parameters "
                f"(supported_parameters={sorted(params) or 'none'})",
                entry
            )

    return ChatSupportResult(mid, True, "model is listed and supports the chat/completions endpoint", entry)


class ModelDispatcher:
    """
    Unified client for model inference across Google Gemini, OpenAI, Anthropic,
    DeepSeek, OpenRouter, and deterministic Mock providers without external
    vendor SDK bloat.
    """
    def __init__(
        self,
        force_mock: bool = False,
        strict_live: Optional[bool] = None,
        max_retries: int = 2,
        request_timeout: Optional[float] = None,
    ):
        self.force_mock = force_mock
        if strict_live is None:
            # STRICT_LIVE=1 promotes API errors to hard failures. It is never
            # applied while force_mock is on, so the deterministic mock engine
            # remains usable.
            strict_live = AppConfig.get_strict_live() and not force_mock
        self.strict_live = strict_live
        # Explicit, bounded retry policy. The previous behaviour (2 retries)
        # is preserved as the default; benchmarks can pin a different value
        # and the number of retries actually performed is reported per request.
        self.max_retries = max(0, int(max_retries))
        # Optional per-request timeout override (seconds).
        self.request_timeout = request_timeout
        self._openrouter_support_cache: Dict[str, ChatSupportResult] = {}

    def generate_live(
        self,
        prompt: str,
        model_name: str,
        system_instruction: Optional[str] = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
        is_tier1: bool = True,
    ) -> ModelResponse:
        """
        Performs a strictly live generation.

        Unlike :meth:`generate`, this method can never fall back to the
        deterministic mock engine. It fails closed when no credential is
        configured, and it raises a categorised :class:`LiveModeError` for any
        transport or provider failure.
        """
        if self.force_mock:
            raise ModelConfigurationError(
                "generate_live() cannot be used with force_mock=True; "
                "a mock response must never be reported as a live measurement."
            )
        provider = self._resolve_provider(model_name)
        if not AppConfig.resolve_api_key(provider):
            raise ModelConfigurationError(
                f"No API key configured for provider '{provider}'. Live mode is "
                f"fail-closed: no request was attempted and no mock response was "
                f"substituted."
            )

        previous_strict = self.strict_live
        self.strict_live = True
        try:
            return self.generate(
                prompt=prompt,
                model_name=model_name,
                system_instruction=system_instruction,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                is_tier1=is_tier1,
            )
        finally:
            self.strict_live = previous_strict


    def generate(
        self,
        prompt: str,
        model_name: str,
        system_instruction: Optional[str] = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
        is_tier1: bool = True
    ) -> ModelResponse:
        """
        Dispatches request to configured provider or falls back to deterministic mock.
        """
        t0 = time.perf_counter()

        # Check for active provider keys
        provider = self._resolve_provider(model_name)
        api_key = AppConfig.resolve_api_key(provider)

        if self.strict_live and self.force_mock:
            raise RuntimeError(
                "Strict live mode cannot use force_mock=True"
            )

        if self.strict_live and not api_key:
            # ModelConfigurationError is a RuntimeError subclass, so existing
            # callers that expect RuntimeError keep working, while the
            # benchmark can classify this as a configuration error.
            raise ModelConfigurationError(
                f"No API key configured for provider '{provider}'"
            )

        if not self.force_mock and api_key:
            try:
                if provider == "gemini":
                    return self._call_gemini(prompt, model_name, api_key, system_instruction, max_output_tokens, temperature, t0)
                elif provider in ("openai", "deepseek", "openrouter"):
                    base_url, remote_model_id = self._prepare_openai_target(model_name, provider)
                    if AppConfig.is_openrouter_url(base_url):
                        # Confirm the model can actually serve /chat/completions.
                        self._assert_openrouter_model_supported(remote_model_id, api_key)
                    return self._call_openai_compatible(prompt, model_name, api_key, provider, system_instruction, max_output_tokens, temperature, t0, base_url=base_url, remote_model_id=remote_model_id)
                elif provider == "anthropic":
                    return self._call_anthropic(prompt, model_name, api_key, system_instruction, max_output_tokens, temperature, t0)
            except Exception as e:
                if isinstance(e, ModelConfigurationError):
                    # Misconfiguration is never silently masked by a mock response.
                    raise
                if isinstance(e, LiveModeError):
                    # Quota / auth / timeout / provider failures are live-mode
                    # failures and must never be masked by a mock response.
                    raise
                if self.strict_live:
                    raise _classify_live_error(e, provider, model_name) from e
                # Preserve mock fallback for normal app usage.
                pass

        if self.strict_live:
            raise RuntimeError(
                f"Strict live mode refused mock fallback for "
                f"provider '{provider}' and model '{model_name}'"
            )

        # Fallback to deterministic mock simulation
        return self._generate_mock_response(prompt, model_name, provider, is_tier1, t0)

    @staticmethod
    def _resolve_provider(model_name: str) -> str:
        """Infers the cloud provider from model name."""
        m = (model_name or "").lower().strip()
        if OPENROUTER_ID_PATTERN.match((model_name or "").strip()):
            # Explicit "vendor/model" identifier (e.g. "openrouter/free",
            # "meta-llama/llama-3.3-70b-instruct:free"). No spaces allowed, so
            # display labels such as "Claude Sonnet 5.5 / Fable" are unaffected.
            return "openrouter"
        if "gemini" in m:
            return "gemini"
        elif "claude" in m or "fable" in m or "sonnet" in m or "opus" in m:
            return "anthropic"
        elif "deepseek" in m:
            return "deepseek"
        elif "gpt" in m or "o3" in m or "o1" in m or "astra" in m:
            return "openai"
        elif "openrouter" in m:
            return "openrouter"
        return "openai"

    @staticmethod
    def _resolve_remote_model_id(model_name: str, provider_name: str, base_url: str) -> str:
        """
        Maps a local model label to the identifier sent to the remote API.

        Explicit OpenRouter model IDs (e.g. "openrouter/free",
        "meta-llama/llama-3.3-70b-instruct:free") are passed through verbatim
        so they are never remapped to a different (e.g. paid) model. When the
        OpenAI-compatible gateway is OpenRouter, canonical upstream IDs are
        namespaced ("gpt-4o-mini" -> "openai/gpt-4o-mini") as the catalog
        requires.
        """
        raw = (model_name or "").strip()

        if provider_name == "openrouter":
            if "/" in raw:
                return raw
            return os.getenv("OPENROUTER_DEFAULT_MODEL", "openrouter/free").strip() or "openrouter/free"

        if AppConfig.is_openrouter_url(base_url):
            if provider_name == "deepseek":
                return "deepseek/deepseek-reasoner" if "r" in raw.lower() else "deepseek/deepseek-chat"
            if ModelDispatcher._is_openai_family_label(raw):
                return "openai/gpt-4o-mini" if "mini" in raw.lower() else "openai/gpt-4o"
            # Not an OpenAI model: never silently substitute a paid OpenAI
            # model for a different vendor's label.
            raise ModelConfigurationError(
                f"Model '{raw}' is not an OpenAI model and cannot be mapped to the "
                f"OpenRouter catalog. Use an explicit OpenRouter model ID "
                f"(for example 'openrouter/free' or 'vendor/model:free')."
            )

        if provider_name == "deepseek":
            return "deepseek-reasoner" if "r" in raw.lower() else "deepseek-chat"
        return "gpt-4o-mini" if "mini" in raw.lower() else "gpt-4o"

    @staticmethod
    def _is_openai_family_label(raw: str) -> bool:
        """Returns True when a display label explicitly names an OpenAI model."""
        m = (raw or "").lower()
        return "gpt" in m or "o3" in m or "o1" in m or "astra" in m

    @classmethod
    def has_live_provider_key(cls, model_names) -> bool:
        """
        Returns True when at least one of the given model labels resolves to a
        provider that has a usable API key configured.

        Used to decide whether a run is a *live* measurement (where a failed
        request must raise) or an offline mock run (where deterministic mock
        output is the intended behaviour).
        """
        for model_name in model_names or []:
            provider = cls._resolve_provider(model_name)
            if AppConfig.resolve_api_key(provider):
                return True
        return False

    @staticmethod
    def _prepare_openai_target(model_name: str, provider_name: str):
        """Returns the (base_url, remote_model_id) pair for an OpenAI-compatible call."""
        base_url = AppConfig.resolve_base_url(provider_name)
        remote_model_id = ModelDispatcher._resolve_remote_model_id(model_name, provider_name, base_url)
        return base_url, remote_model_id

    def _assert_openrouter_model_supported(self, model_id: str, api_key: str) -> None:
        """
        Confirms the requested OpenRouter model is exposed and supports the
        /chat/completions endpoint. Raises ModelConfigurationError when the
        catalog explicitly reports the model as unusable. Results are cached
        per model ID.
        """
        model_id = (model_id or "").strip()
        if model_id in self._openrouter_support_cache:
            result = self._openrouter_support_cache[model_id]
        else:
            result = verify_openrouter_chat_support(
                model_id,
                fetch_models=lambda: fetch_openrouter_models(api_key)
            )
            self._openrouter_support_cache[model_id] = result

        if result.supported is False:
            raise ModelConfigurationError(
                f"OpenRouter model '{model_id}' cannot serve the chat-completions "
                f"endpoint: {result.reason}"
            )

    def _call_gemini(
        self,
        prompt: str,
        model_name: str,
        api_key: str,
        system_instruction: Optional[str],
        max_output_tokens: int,
        temperature: float,
        start_time: float
    ) -> ModelResponse:
        """Calls Google Gemini REST API."""
        clean_model = "gemini-2.0-flash" if "flash" in model_name.lower() else "gemini-1.5-pro"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_output_tokens,
                "temperature": temperature
            }
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        max_retries = self.max_retries
        retries_used = 0
        for attempt_i in range(max_retries + 1):
            with httpx.Client(timeout=self.request_timeout or 30.0) as client:
                try:
                    resp = client.post(url, json=payload)
                except Exception as exc:
                    try:
                        exc.retry_count = retries_used
                    except Exception:
                        pass
                    raise
                if resp.status_code == 402:
                    error = QuotaExceededError(
                        _describe_quota_response(resp, "gemini", model_name),
                        status_code=getattr(resp, "status_code", None),
                    )
                    error.retry_count = retries_used
                    raise error
                if resp.status_code == 429:
                    if _is_daily_quota_response(resp) or attempt_i >= max_retries:
                        error = QuotaExceededError(
                            _describe_quota_response(resp, "gemini", model_name),
                            status_code=getattr(resp, "status_code", None),
                        )
                        error.retry_count = retries_used
                        raise error
                    retries_used += 1
                    time.sleep(2.0 * (attempt_i + 1))
                    continue
                try:
                    resp.raise_for_status()
                except Exception as exc:
                    try:
                        exc.retry_count = retries_used
                    except Exception:
                        pass
                    raise
                data = resp.json()
                break

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        candidate = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {}) or {}

        provider_prompt = _as_int(usage.get("promptTokenCount"))
        provider_completion = _as_int(usage.get("candidatesTokenCount"))
        provider_total = _as_int(usage.get("totalTokenCount"))
        if provider_total is None and provider_prompt is not None and provider_completion is not None:
            provider_total = provider_prompt + provider_completion

        estimated_prompt = get_token_count(prompt) if provider_prompt is None else None
        estimated_completion = get_token_count(candidate) if provider_completion is None else None

        cached_in = usage.get("cachedContentTokenCount", 0)
        raw_in = provider_prompt if provider_prompt is not None else (estimated_prompt or 0)
        out_tokens = provider_completion if provider_completion is not None else (estimated_completion or 0)

        if provider_prompt is not None and provider_completion is not None:
            usage_source = "provider_reported"
        elif provider_prompt is not None or provider_completion is not None or provider_total is not None:
            usage_source = "provider_partial"
        else:
            usage_source = "local_estimate"

        return ModelResponse(
            content=candidate,
            raw_tokens_in=raw_in,
            cached_tokens_in=cached_in,
            output_tokens=out_tokens,
            reasoning_tokens=0,
            model_name=model_name,
            provider="Google DeepMind",
            latency_ms=latency_ms,
            is_mock=False,
            finish_reason=data.get("candidates", [{}])[0].get("finishReason") if data.get("candidates") else None,
            served_model_id=data.get("modelVersion"),
            retry_count=retries_used,
            usage_source=usage_source,
            provider_prompt_tokens=provider_prompt,
            provider_completion_tokens=provider_completion,
            provider_total_tokens=provider_total,
            estimated_prompt_tokens=estimated_prompt,
            estimated_completion_tokens=estimated_completion,
        )

    def _call_openai_compatible(
        self,
        prompt: str,
        model_name: str,
        api_key: str,
        provider_name: str,
        system_instruction: Optional[str],
        max_output_tokens: int,
        temperature: float,
        start_time: float,
        base_url: Optional[str] = None,
        remote_model_id: Optional[str] = None
    ) -> ModelResponse:
        """Calls an OpenAI-compatible /chat/completions endpoint (OpenAI, DeepSeek, OpenRouter)."""
        if base_url is None or remote_model_id is None:
            base_url, remote_model_id = self._prepare_openai_target(model_name, provider_name)

        endpoint = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        if AppConfig.is_openrouter_url(base_url):
            headers.update(AppConfig.get_openrouter_headers())

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        # Map to valid remote API model identifier
        clean_model = remote_model_id

        payload = {
            "model": clean_model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "temperature": temperature
        }

        max_retries = self.max_retries
        retries_used = 0
        for attempt_i in range(max_retries + 1):
            with httpx.Client(timeout=self.request_timeout or 35.0) as client:
                try:
                    resp = client.post(endpoint, json=payload, headers=headers)
                except Exception as exc:
                    # Preserve the number of retries already performed so a
                    # failed request is not reported as "no retries".
                    try:
                        exc.retry_count = retries_used
                    except Exception:
                        pass
                    raise
                if resp.status_code == 402:
                    # Insufficient credits: retrying cannot help.
                    error = QuotaExceededError(
                        _describe_quota_response(resp, provider_name, model_name),
                        status_code=getattr(resp, "status_code", None),
                    )
                    error.retry_count = retries_used
                    raise error
                if resp.status_code == 429:
                    if _is_daily_quota_response(resp):
                        # A daily quota cannot clear within a run, and falling
                        # back to a mock would hide a failed live request.
                        error = QuotaExceededError(
                            _describe_quota_response(resp, provider_name, model_name),
                            status_code=getattr(resp, "status_code", None),
                        )
                        error.retry_count = retries_used
                        raise error
                    if attempt_i < max_retries:
                        # Transient per-minute limit: back off and retry.
                        retries_used += 1
                        time.sleep(2.0 * (attempt_i + 1))
                        continue
                    error = QuotaExceededError(
                        _describe_quota_response(resp, provider_name, model_name),
                        status_code=getattr(resp, "status_code", None),
                    )
                    error.retry_count = retries_used
                    raise error
                try:
                    resp.raise_for_status()
                except Exception as exc:
                    try:
                        exc.retry_count = retries_used
                    except Exception:
                        pass
                    raise
                data = resp.json()
                break

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        choice = data["choices"][0]["message"]
        content = choice.get("content", "")
        usage = data.get("usage", {}) or {}
        finish_reason = data["choices"][0].get("finish_reason")
        served_model_id = data.get("model")

        # ------------------------------------------------------------------
        # Token provenance is tracked per field. A local tiktoken estimate is
        # NEVER labelled as provider-reported: it is stored in a separate
        # field so a consumer cannot mistake one for the other.
        # ------------------------------------------------------------------
        provider_prompt = _as_int(usage.get("prompt_tokens"))
        provider_completion = _as_int(usage.get("completion_tokens"))
        provider_total = _as_int(usage.get("total_tokens"))
        if provider_total is None and provider_prompt is not None and provider_completion is not None:
            provider_total = provider_prompt + provider_completion

        estimated_prompt = get_token_count(prompt) if provider_prompt is None else None
        estimated_completion = get_token_count(content) if provider_completion is None else None

        cached_in = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

        raw_in = provider_prompt if provider_prompt is not None else (estimated_prompt or 0)
        out_tokens = provider_completion if provider_completion is not None else (estimated_completion or 0)

        if provider_prompt is not None and provider_completion is not None:
            usage_source = "provider_reported"
        elif provider_prompt is not None or provider_completion is not None or provider_total is not None:
            usage_source = "provider_partial"
        else:
            usage_source = "local_estimate"

        # Some gateways report a cost for the request. It is only used when the
        # provider actually supplied it; otherwise it stays None.
        cost_usd = None
        cost_source = None
        raw_cost = data.get("cost")
        if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool):
            cost_usd = float(raw_cost)
            cost_source = "provider_reported"

        return ModelResponse(
            content=content,
            raw_tokens_in=raw_in,
            cached_tokens_in=cached_in,
            output_tokens=out_tokens,
            reasoning_tokens=reasoning_tokens,
            model_name=model_name,
            provider=("OpenRouter" if AppConfig.is_openrouter_url(base_url)
                      else ("DeepSeek" if provider_name == "deepseek" else "OpenAI")),
            latency_ms=latency_ms,
            is_mock=False,
            finish_reason=finish_reason,
            served_model_id=served_model_id,
            retry_count=retries_used,
            usage_source=usage_source,
            cost_usd=cost_usd,
            cost_source=cost_source,
            provider_prompt_tokens=provider_prompt,
            provider_completion_tokens=provider_completion,
            provider_total_tokens=provider_total,
            estimated_prompt_tokens=estimated_prompt,
            estimated_completion_tokens=estimated_completion,
        )

    def _call_anthropic(
        self,
        prompt: str,
        model_name: str,
        api_key: str,
        system_instruction: Optional[str],
        max_output_tokens: int,
        temperature: float,
        start_time: float
    ) -> ModelResponse:
        """Calls Anthropic Claude /v1/messages endpoint."""
        endpoint = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        payload: Dict[str, Any] = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
            "temperature": temperature
        }
        if system_instruction:
            payload["system"] = system_instruction

        max_retries = self.max_retries
        retries_used = 0
        for attempt_i in range(max_retries + 1):
            with httpx.Client(timeout=self.request_timeout or 40.0) as client:
                try:
                    resp = client.post(endpoint, json=payload, headers=headers)
                except Exception as exc:
                    try:
                        exc.retry_count = retries_used
                    except Exception:
                        pass
                    raise
                if resp.status_code == 402:
                    error = QuotaExceededError(
                        _describe_quota_response(resp, "anthropic", model_name),
                        status_code=getattr(resp, "status_code", None),
                    )
                    error.retry_count = retries_used
                    raise error
                if resp.status_code == 429:
                    if _is_daily_quota_response(resp) or attempt_i >= max_retries:
                        error = QuotaExceededError(
                            _describe_quota_response(resp, "anthropic", model_name),
                            status_code=getattr(resp, "status_code", None),
                        )
                        error.retry_count = retries_used
                        raise error
                    retries_used += 1
                    time.sleep(2.0 * (attempt_i + 1))
                    continue
                try:
                    resp.raise_for_status()
                except Exception as exc:
                    try:
                        exc.retry_count = retries_used
                    except Exception:
                        pass
                    raise
                data = resp.json()
                break

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        content = data["content"][0]["text"]
        usage = data.get("usage", {}) or {}

        provider_prompt = _as_int(usage.get("input_tokens"))
        provider_completion = _as_int(usage.get("output_tokens"))
        provider_total = None
        if provider_prompt is not None and provider_completion is not None:
            provider_total = provider_prompt + provider_completion

        estimated_prompt = get_token_count(prompt) if provider_prompt is None else None
        estimated_completion = get_token_count(content) if provider_completion is None else None

        cached_in = usage.get("cache_read_input_tokens", 0)
        raw_in = provider_prompt if provider_prompt is not None else (estimated_prompt or 0)
        out_tokens = provider_completion if provider_completion is not None else (estimated_completion or 0)

        if provider_prompt is not None and provider_completion is not None:
            usage_source = "provider_reported"
        elif provider_prompt is not None or provider_completion is not None:
            usage_source = "provider_partial"
        else:
            usage_source = "local_estimate"

        return ModelResponse(
            content=content,
            raw_tokens_in=raw_in,
            cached_tokens_in=cached_in,
            output_tokens=out_tokens,
            reasoning_tokens=0,
            model_name=model_name,
            provider="Anthropic",
            latency_ms=latency_ms,
            is_mock=False,
            finish_reason=data.get("stop_reason"),
            served_model_id=data.get("model"),
            retry_count=retries_used,
            usage_source=usage_source,
            provider_prompt_tokens=provider_prompt,
            provider_completion_tokens=provider_completion,
            provider_total_tokens=provider_total,
            estimated_prompt_tokens=estimated_prompt,
            estimated_completion_tokens=estimated_completion,
        )

    def _generate_mock_response(
        self,
        prompt: str,
        model_name: str,
        provider: str,
        is_tier1: bool,
        start_time: float
    ) -> ModelResponse:
        """
        Deterministic mock generator for offline evaluation, grading, and testing.
        Simulates realistic outputs and token accounting without network I/O.
        """
        tokens_in = get_token_count(prompt)

        # Detect target domain / function from prompt
        if "dispatch_batch" in prompt:
            if "IngestionPipeline" in prompt or "expected_hash_prefix" in prompt:
                # App Scenario 2: IngestionPipeline.dispatch_batch(expected_hash_prefix) -> int
                if is_tier1:
                    mock_code = """```python
    def dispatch_batch(self, expected_hash_prefix: str) -> int:
        dispatched = 0
        for p in self.queue:
            # Bug: Checking payload_id instead of compute_sha256()
            if len(p.payload_id) == len(expected_hash_prefix):
                dispatched += 1
        return dispatched
```"""
                    out_tokens = get_token_count(mock_code)
                    reasoning = 0
                    latency = 120.0
                else:
                    mock_code = """```python
    def dispatch_batch(self, expected_hash_prefix: str) -> int:
        dispatched = 0
        for p in self.queue:
            if p.compute_sha256().startswith(expected_hash_prefix):
                dispatched += 1
        return dispatched
```"""
                    out_tokens = get_token_count(mock_code)
                    reasoning = 360
                    latency = 1050.0
            else:
                # Benchmark BM-02: IngestionBatch.dispatch_batch(raw_records) -> list[str]
                if is_tier1:
                    mock_code = """```python
    def dispatch_batch(self, raw_records: list[dict]) -> list[str]:
        return [r.get('payload', '') for r in raw_records]
```"""
                    out_tokens = get_token_count(mock_code)
                    reasoning = 0
                    latency = 130.0
                else:
                    mock_code = """```python
    def dispatch_batch(self, raw_records: list[dict]) -> list[str]:
        import hashlib
        digests = []
        for r in raw_records:
            if not r.get('corrupt', False):
                p = str(r.get('payload', '')).encode('utf-8')
                digests.append(hashlib.sha256(p).hexdigest())
        return digests
```"""
                    out_tokens = get_token_count(mock_code)
                    reasoning = 380
                    latency = 1100.0

        elif "put" in prompt and "LRUCache" in prompt:
            if is_tier1:
                mock_code = """```python
    def put(self, key: str, value: int) -> None:
        self.cache[key] = value
        self.order.append(key)
```"""
                out_tokens = get_token_count(mock_code)
                reasoning = 0
                latency = 120.0
            else:
                mock_code = """```python
    def put(self, key: str, value: int) -> None:
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            oldest = self.order.pop(0)
            del self.cache[oldest]
        self.cache[key] = value
        self.order.append(key)
```"""
                out_tokens = get_token_count(mock_code)
                reasoning = 410
                latency = 1150.0

        elif "acquire" in prompt and "TokenBucket" in prompt:
            if is_tier1:
                mock_code = """```python
    def acquire(self, tokens: float) -> bool:
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
```"""
                out_tokens = get_token_count(mock_code)
                reasoning = 0
                latency = 115.0
            else:
                mock_code = """```python
    def acquire(self, tokens: float) -> bool:
        now = time.time()
        elapsed = now - self.last_check
        self.last_check = now
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.rate))
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
```"""
                out_tokens = get_token_count(mock_code)
                reasoning = 440
                latency = 1200.0

        elif "calculate_net_yield" in prompt:
            if is_tier1:
                if "Your previous code produced this error:" in prompt or "Intra-Tier Surgical Repair" in prompt:
                    # Tier 1 intra-tier repair fixes the syntax slip!
                    mock_code = """```python
    def calculate_net_yield(self, gross_margin: float, opex: float) -> float:
        return float(gross_margin - opex)
```"""
                    out_tokens = get_token_count(mock_code)
                    reasoning = 0
                    latency = 85.0
                else:
                    # Initial pass produces a syntax slip (missing colon)
                    mock_code = """```python
    def calculate_net_yield(self, gross_margin: float, opex: float) -> float
        return float(gross_margin - opex)
```"""
                    out_tokens = get_token_count(mock_code)
                    reasoning = 0
                    latency = 95.0
            else:
                mock_code = """```python
    def calculate_net_yield(self, gross_margin: float, opex: float) -> float:
        return float(gross_margin - opex)
```"""
                out_tokens = get_token_count(mock_code)
                reasoning = 210
                latency = 850.0

        else:
            # Default: Account ledger reconciliation
            if is_tier1:
                if "Your previous code produced this error:" in prompt or "Intra-Tier Surgical Repair" in prompt:
                    if any(err in prompt for err in ("SyntaxError", "IndentationError", "TabError", "NameError", "UnboundLocalError")):
                        mock_code = """```python
    def reconcile_balance(self, target_date: str) -> float:
        total = 0.0
        for tx in self.transactions:
            if tx.validate_checksum():
                tax = tx.calculate_tax_allocation(0.05)
                total += (tx.amount - tax)
        return round(float(total), 2)
```"""
                        out_tokens = get_token_count(mock_code)
                        reasoning = 0
                        latency = 110.0
                    else:
                        mock_code = """```python
    def reconcile_balance(self, target_date: str) -> float:
        total = 0.0
        for tx in self.transactions:
            if not tx.validate_checksum():
                total += tx.amount
        return float(total)
```"""
                        out_tokens = get_token_count(mock_code)
                        reasoning = 0
                        latency = 125.0
                else:
                    mock_code = """```python
    def reconcile_balance(self, target_date: str) -> float:
        total = 0.0
        for tx in self.transactions:
            if not tx.validate_checksum():
                total += tx.amount
        return float(total)
```"""
                    out_tokens = get_token_count(mock_code)
                    reasoning = 0
                    latency = 145.0
            else:
                mock_code = """```python
    def reconcile_balance(self, target_date: str) -> float:
        total = 0.0
        for tx in self.transactions:
            if tx.validate_checksum():
                tax = tx.calculate_tax_allocation(0.05)
                total += (tx.amount - tax)
        return round(float(total), 2)
```"""
                out_tokens = get_token_count(mock_code)
                reasoning = 420
                latency = 1250.0

        return ModelResponse(
            content=mock_code,
            raw_tokens_in=tokens_in,
            cached_tokens_in=73 if not is_tier1 else 0,
            output_tokens=out_tokens,
            reasoning_tokens=reasoning,
            model_name=model_name,
            provider=provider.capitalize() + " (Mock Engine)",
            latency_ms=latency,
            is_mock=True,
            finish_reason="mock",
            served_model_id=model_name,
            retry_count=0,
            usage_source="local_estimate",
            provider_prompt_tokens=None,
            provider_completion_tokens=None,
            provider_total_tokens=None,
            estimated_prompt_tokens=tokens_in,
            estimated_completion_tokens=out_tokens,
        )
