import os
from typing import Optional, Dict
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

class AppConfig:
    """Centralized, security-conscious configuration manager."""

    # Default OpenAI-compatible gateway for OpenRouter's model catalog.
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

    @staticmethod
    def get_api_key(provider: str) -> Optional[str]:
        """
        Retrieves the API key for a specified provider.
        Supported providers: 'gemini', 'openai', 'anthropic', 'deepseek', 'openrouter'.
        """
        p = provider.strip().lower()
        key_map = {
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openrouter": "OPENROUTER_API_KEY"
        }
        env_var = key_map.get(p)
        if env_var:
            val = os.getenv(env_var)
            if val and val.strip():
                return val.strip()
        return None

    @classmethod
    def is_provider_configured(cls, provider: str) -> bool:
        """Returns True if a valid non-empty API key exists for the provider."""
        return cls.get_api_key(provider) is not None

    @staticmethod
    def mask_key(key: Optional[str]) -> str:
        """Masks an API key for safe display in UI and logs (e.g., 'sk-...d8f9')."""
        if not key or len(key.strip()) < 8:
            return "[NOT CONFIGURED]"
        k = key.strip()
        return f"{k[:3]}...{k[-4:]}"

    @classmethod
    def get_configured_providers(cls) -> Dict[str, bool]:
        """Returns dictionary of provider availability status."""
        return {
            "Gemini": cls.is_provider_configured("gemini"),
            "OpenAI": cls.is_provider_configured("openai"),
            "Anthropic": cls.is_provider_configured("anthropic"),
            "DeepSeek": cls.is_provider_configured("deepseek"),
            "OpenRouter": cls.is_provider_configured("openrouter")
        }

    @staticmethod
    def get_execution_timeout() -> float:
        """Returns execution timeout limit in seconds."""
        try:
            return float(os.getenv("EXECUTION_TIMEOUT_SECONDS", "3.0"))
        except ValueError:
            return 3.0

    @staticmethod
    def get_openai_base_url() -> Optional[str]:
        """Returns custom OpenAI-compatible base URL if specified."""
        url = os.getenv("OPENAI_BASE_URL")
        return url.strip() if url and url.strip() else None

    @staticmethod
    def get_openrouter_base_url() -> Optional[str]:
        """Returns an explicit OpenRouter base URL override if specified."""
        url = os.getenv("OPENROUTER_BASE_URL")
        return url.strip() if url and url.strip() else None

    @staticmethod
    def is_openrouter_url(url: Optional[str]) -> bool:
        """Returns True when the supplied base URL targets the OpenRouter gateway."""
        return bool(url) and "openrouter.ai" in url.lower()

    @classmethod
    def resolve_base_url(cls, provider: str) -> str:
        """
        Resolves the OpenAI-compatible base URL for a provider.

        - 'openrouter' -> OPENROUTER_BASE_URL, else OPENAI_BASE_URL when it
          points at openrouter.ai, else https://openrouter.ai/api/v1
        - 'openai'     -> OPENAI_BASE_URL if set, else the official endpoint
        - 'deepseek'   -> the official DeepSeek endpoint, unless OPENAI_BASE_URL
          overrides it (preserves the pre-existing override behaviour)
        """
        p = (provider or "").strip().lower()
        openai_custom = cls.get_openai_base_url()

        if p == "openrouter":
            custom = cls.get_openrouter_base_url()
            if custom:
                return custom.rstrip("/")
            if openai_custom:
                return openai_custom.rstrip("/")
            return cls.OPENROUTER_BASE_URL

        if openai_custom:
            return openai_custom.rstrip("/")

        if p == "deepseek":
            return cls.DEEPSEEK_BASE_URL
        return "https://api.openai.com/v1"

    @classmethod
    def resolve_api_key(cls, provider: str) -> Optional[str]:
        """
        Returns the API key that matches the resolved gateway for a provider.

        - gemini/anthropic always use their own vendor key.
        - When the effective base URL is the OpenRouter gateway, the dedicated
          OPENROUTER_API_KEY is used, falling back to OPENAI_API_KEY only when
          no OpenRouter key is configured.
        - Any other OpenAI-compatible gateway (api.openai.com, Ollama, vLLM,
          Groq, Together, ...) uses OPENAI_API_KEY, so an OpenRouter key is
          never sent to a third-party endpoint.
        The OPENAI_API_KEY value itself is never modified.
        """
        p = (provider or "").strip().lower()

        if p in ("gemini", "anthropic"):
            return cls.get_api_key(p)

        base_url = cls.resolve_base_url(p)

        if cls.is_openrouter_url(base_url):
            return cls.get_api_key("openrouter") or cls.get_api_key("openai")

        if p == "openrouter":
            # A custom OpenAI-compatible gateway serving "vendor/model" IDs.
            return cls.get_api_key("openai")

        return cls.get_api_key(p)

    @staticmethod
    def get_strict_live() -> bool:
        """Returns True when STRICT_LIVE is enabled in the environment."""
        val = os.getenv("STRICT_LIVE", "").strip().lower()
        return val in ("1", "true", "yes", "on")

    @staticmethod
    def _default_tier_model(env_var: str, options) -> Optional[str]:
        """
        Returns the configured default tier model.

        Accepts a known catalog option, or any explicit "vendor/model" OpenRouter
        model ID (which is passed through to the API verbatim).
        """
        name = os.getenv(env_var, "").strip()
        if not name:
            return None
        if name in options:
            return name
        if "/" in name:
            return name
        return None

    @classmethod
    def get_default_tier1_model(cls, options) -> Optional[str]:
        """Returns the DEFAULT_TIER1_MODEL override when selectable."""
        return cls._default_tier_model("DEFAULT_TIER1_MODEL", options)

    @classmethod
    def get_default_tier2_model(cls, options) -> Optional[str]:
        """Returns the DEFAULT_TIER2_MODEL override when selectable."""
        return cls._default_tier_model("DEFAULT_TIER2_MODEL", options)

    @staticmethod
    def get_openrouter_headers() -> Dict[str, str]:
        """Optional OpenRouter attribution headers (never contains secrets)."""
        headers: Dict[str, str] = {}
        referer = os.getenv("OPENROUTER_HTTP_REFERER")
        title = os.getenv("OPENROUTER_APP_TITLE")
        if referer and referer.strip():
            headers["HTTP-Referer"] = referer.strip()
        if title and title.strip():
            headers["X-Title"] = title.strip()
        return headers

