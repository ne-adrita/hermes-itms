import os
from typing import Optional, Dict, Any, List
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

    # ------------------------------------------------------------------
    # Credential-binding and environment hygiene (never discloses a secret)
    # ------------------------------------------------------------------
    @classmethod
    def credential_binding_report(cls) -> Dict[str, Any]:
        """
        Reports which providers resolve to the *same* credential, and whether
        that credential would be sent to **different vendors**.

        The report contains **provider names and gateway hosts only**.  No
        credential value, no length, no hash and no fingerprint is produced,
        printed or stored: the comparison is done in memory with a
        constant-time comparison, so the output cannot leak information about
        the secret itself.

        Two different situations must not be conflated:

        *   several providers that all point at the **same** gateway (for
            example every provider configured against one OpenRouter base
            URL) legitimately share one key. That is not a risk.
        *   one key shared by providers that resolve to **different** base URLs
            is a real risk: a wrong ``--provider`` would send the key to
            another vendor.  The live benchmark refuses to start in that case
            unless the operator acknowledges it explicitly.
        """
        import hmac
        from urllib.parse import urlparse

        providers = ("gemini", "openai", "anthropic", "deepseek", "openrouter")
        resolved: Dict[str, Optional[str]] = {
            provider: cls.resolve_api_key(provider) for provider in providers
        }
        configured = [provider for provider, key in resolved.items() if key]
        base_urls = {provider: cls.resolve_base_url(provider) for provider in configured}

        groups: List[List[str]] = []
        cross_vendor: List[Dict[str, Any]] = []
        for first in configured:
            group = tuple(
                sorted(
                    second
                    for second in configured
                    # Constant-time comparison of the two in-memory values.
                    if hmac.compare_digest(resolved[first] or "", resolved[second] or "")
                )
            )
            if len(group) > 1 and list(group) not in groups:
                groups.append(list(group))
                hosts = sorted(
                    {
                        urlparse(base_urls[provider]).netloc or base_urls[provider]
                        for provider in group
                    }
                )
                if len(hosts) > 1:
                    cross_vendor.append({"providers": list(group), "gateways": hosts})

        return {
            "providers_with_credential": sorted(configured),
            "shared_credential_groups": sorted(groups),
            "shared_credential_detected": bool(groups),
            "cross_vendor_credential_groups": cross_vendor,
            "cross_vendor_credential_detected": bool(cross_vendor),
            "note": (
                "Providers that share a credential AND resolve to the same "
                "gateway are not a risk; only a credential shared across "
                "different gateways is reported as cross-vendor."
            ),
        }

    @staticmethod
    def _env_file_path() -> str:
        """The path of the project's environment file."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

    @classmethod
    def env_hygiene_report(cls) -> Dict[str, Any]:
        """
        Reports the *structure* of the environment file, never its contents.

        ``bare_secret_line_present`` is True when ``.env`` contains at least one
        non-empty, non-comment line that is not a ``KEY=value`` assignment -
        typically a pasted secret.  Such a line is ignored by every loader in
        this project (it defines no variable), but it is still a live secret in
        the working tree and must be removed and rotated by the operator.
        """
        path = cls._env_file_path()
        names: List[str] = []
        bare = False
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    for raw in handle:
                        line = raw.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line and line.split("=", 1)[0].strip().replace(
                            "_", ""
                        ).isalnum():
                            names.append(line.split("=", 1)[0].strip())
                        else:
                            bare = True
            except OSError:
                pass
        return {
            "env_file_present": os.path.exists(path),
            "defined_variable_names": sorted(set(names)),
            "bare_secret_line_present": bare,
        }

