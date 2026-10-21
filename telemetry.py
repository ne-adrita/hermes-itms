from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, List

@dataclass(frozen=True)
class PricingProfile:
    """Pricing configuration standardized per 1,000,000 tokens."""
    name: str
    tier: str                    # "Tier 1: Budget Speculator" or "Tier 2: Frontier Reasoning"
    provider: str                # e.g., OpenAI, Anthropic, Google, DeepSeek, Meta/Open
    price_input_uncached: float  # USD per 1M tokens
    price_input_cached: float    # USD per 1M tokens (KV hit)
    price_output: float          # USD per 1M tokens (output & reasoning)
    cache_min_tokens: int = 1024 # Standard provider caching threshold
    latency_profile: str = "Medium" # "Ultra-Fast (<200ms)", "Fast (<500ms)", "Heavy Reasoning (2-10s)"
    suitability_notes: str = ""

    @property
    def cost_per_uncached_token(self) -> float:
        return self.price_input_uncached / 1_000_000.0

    @property
    def cost_per_cached_token(self) -> float:
        return self.price_input_cached / 1_000_000.0

    @property
    def cost_per_output_token(self) -> float:
        return self.price_output / 1_000_000.0


# --- Tier 2: Frontier Reasoning Titans (Escalation Targets) ---
TIER_2_FRONTIER_PROFILES: Dict[str, PricingProfile] = {
    "GPT-5.6 / ChatGPT Astra": PricingProfile(
        name="GPT-5.6 / ChatGPT Astra",
        tier="Tier 2: Frontier Reasoning",
        provider="OpenAI",
        price_input_uncached=4.00,
        price_input_cached=0.40,
        price_output=18.00,
        cache_min_tokens=1024,
        latency_profile="Heavy Reasoning (3-8s)",
        suitability_notes="Deep test-time compute. Excels at complex multi-file architectural bugs. Very high token cost if contaminated by bad drafts."
    ),
    "Claude Sonnet 5.5 / Fable": PricingProfile(
        name="Claude Sonnet 5.5 / Fable",
        tier="Tier 2: Frontier Reasoning",
        provider="Anthropic",
        price_input_uncached=3.50,
        price_input_cached=0.35,
        price_output=16.00,
        cache_min_tokens=1024,
        latency_profile="Heavy Reasoning (2-6s)",
        suitability_notes="Gold-standard code refactoring. Strong resistance to hallucinated APIs when given isolated runtime error traces."
    ),
    "Claude Opus 5.5": PricingProfile(
        name="Claude Opus 5.5",
        tier="Tier 2: Frontier Reasoning",
        provider="Anthropic",
        price_input_uncached=8.00,
        price_input_cached=1.00,
        price_output=32.00,
        cache_min_tokens=1024,
        latency_profile="Heavy Reasoning (5-15s)",
        suitability_notes="Maximum reasoning density. Extremely expensive. ITMS clean-trace context hygiene is mandatory to prevent massive billing spikes."
    ),
    "DeepSeek R2 / V4 Reasoning": PricingProfile(
        name="DeepSeek R2 / V4 Reasoning",
        tier="Tier 2: Frontier Reasoning",
        provider="DeepSeek",
        price_input_uncached=0.45,
        price_input_cached=0.11,
        price_output=1.80,
        cache_min_tokens=64,
        latency_profile="Medium Reasoning (2-5s)",
        suitability_notes="Top-tier open-weights reasoning. Extreme token value. Highly responsive to static prefix KV caching."
    ),
    "Claude 3.7 Sonnet (Thinking)": PricingProfile(
        name="Claude 3.7 Sonnet (Thinking)",
        tier="Tier 2: Frontier Reasoning",
        provider="Anthropic",
        price_input_uncached=3.00,
        price_input_cached=0.375,
        price_output=15.00,
        cache_min_tokens=1024,
        latency_profile="Medium Reasoning (2-5s)",
        suitability_notes="Hybrid architecture. Balances quick code syntax generation with dynamic thinking loops."
    ),
    "OpenAI o3-mini": PricingProfile(
        name="OpenAI o3-mini",
        tier="Tier 2: Frontier Reasoning",
        provider="OpenAI",
        price_input_uncached=1.10,
        price_input_cached=0.55,
        price_output=4.40,
        cache_min_tokens=1024,
        latency_profile="Fast Reasoning (1-3s)",
        suitability_notes="Cost-efficient reasoning model. Ideal for algorithmic debugging without burning frontier enterprise budgets."
    ),
    "Frontier Standard (Spec Default)": PricingProfile(
        name="Frontier Standard (Spec Default)",
        tier="Tier 2: Frontier Reasoning",
        provider="Standard Benchmark",
        price_input_uncached=3.00,
        price_input_cached=0.30,
        price_output=15.00,
        cache_min_tokens=1024,
        latency_profile="Medium",
        suitability_notes="Standardized academic benchmark tier."
    ),
    # Key is the literal OpenRouter model ID, so it is passed through to the
    # /chat/completions endpoint verbatim (never remapped to a paid model).
    "openrouter/free": PricingProfile(
        name="openrouter/free",
        tier="Tier 2: Frontier Reasoning",
        provider="OpenRouter (Free Tier)",
        price_input_uncached=0.0,
        price_input_cached=0.0,
        price_output=0.0,
        cache_min_tokens=0,
        latency_profile="Free Tier (rate-limited)",
        suitability_notes="OpenRouter 'Free Models Router' used as the escalation target. Zero token cost, but heavily rate-limited and the routed model can change without notice."
    )
}


# --- Tier 1: Budget Workhorses (First-Pass Speculators) ---
TIER_1_BUDGET_PROFILES: Dict[str, PricingProfile] = {
    "Gemini 3.5 Flash": PricingProfile(
        name="Gemini 3.5 Flash",
        tier="Tier 1: Budget Speculator",
        provider="Google DeepMind",
        price_input_uncached=0.075,
        price_input_cached=0.01875,
        price_output=0.30,
        cache_min_tokens=1024,
        latency_profile="Ultra-Fast (<150ms)",
        suitability_notes="Prime Tier 1 workhorse. Sub-150ms time-to-first-token. Solves 65-75% of routine boilerplate logic for pennies."
    ),
    "GPT-4o-mini": PricingProfile(
        name="GPT-4o-mini",
        tier="Tier 1: Budget Speculator",
        provider="OpenAI",
        price_input_uncached=0.15,
        price_input_cached=0.075,
        price_output=0.60,
        cache_min_tokens=1024,
        latency_profile="Ultra-Fast (<200ms)",
        suitability_notes="High reliability for JSON schemas and deterministic code contracts. Excellent pass rate on standard unit tests."
    ),
    "DeepSeek V4 Flash": PricingProfile(
        name="DeepSeek V4 Flash",
        tier="Tier 1: Budget Speculator",
        provider="DeepSeek",
        price_input_uncached=0.08,
        price_input_cached=0.02,
        price_output=0.25,
        cache_min_tokens=64,
        latency_profile="Ultra-Fast (<180ms)",
        suitability_notes="Exceptional cost-to-performance ratio for code synthesis. RadixAttention KV caching compatible."
    ),
    "Qwen 3.5 / 3.8 Coder": PricingProfile(
        name="Qwen 3.5 / 3.8 Coder",
        tier="Tier 1: Budget Speculator",
        provider="Alibaba / Open",
        price_input_uncached=0.12,
        price_input_cached=0.03,
        price_output=0.40,
        cache_min_tokens=128,
        latency_profile="Fast (<300ms)",
        suitability_notes="Specialized code weights. Outperforms generic small models on AST understanding and function signatures."
    ),
    "Llama 3.3 / Llama 4 (70B)": PricingProfile(
        name="Llama 3.3 / Llama 4 (70B)",
        tier="Tier 1: Budget Speculator",
        provider="Meta / Open",
        price_input_uncached=0.20,
        price_input_cached=0.05,
        price_output=0.60,
        cache_min_tokens=64,
        latency_profile="Fast (<250ms via Groq/Together)",
        suitability_notes="Robust open-weights standard. High instruction adherence without vendor lock-in. Ideal for on-premise execution."
    ),
    # Key is the literal OpenRouter model ID, so it is passed through to the
    # /chat/completions endpoint verbatim (never remapped to a paid model).
    "openrouter/free": PricingProfile(
        name="openrouter/free",
        tier="Tier 1: Budget Speculator",
        provider="OpenRouter (Free Tier)",
        price_input_uncached=0.0,
        price_input_cached=0.0,
        price_output=0.0,
        cache_min_tokens=0,
        latency_profile="Free Tier (rate-limited)",
        suitability_notes="OpenRouter 'Free Models Router'. Zero token cost, but heavily rate-limited and the routed model can change without notice."
    )
}

# Zero-cost profile used for any OpenRouter model ID that is not a catalog key
# (for example "meta-llama/llama-3.3-70b-instruct:free"). Such IDs are still
# passed through verbatim to the API; they simply have no published pricing in
# this catalog, so they are costed as free-tier rather than priced at a
# mismatched vendor's rate.
OPENROUTER_FREE_TIER_PROFILE = PricingProfile(
    name="OpenRouter (Free Tier)",
    tier="Tier 1: Budget Speculator",
    provider="OpenRouter (Free Tier)",
    price_input_uncached=0.0,
    price_input_cached=0.0,
    price_output=0.0,
    cache_min_tokens=0,
    latency_profile="Free Tier (rate-limited)",
    suitability_notes="OpenRouter model ID not present in this pricing catalog. Costed as free tier."
)

# Unified Preset Catalog (maintains backward compatibility with test suites)
PRESET_PRICING_PROFILES: Dict[str, PricingProfile] = {
    **TIER_2_FRONTIER_PROFILES,
    **TIER_1_BUDGET_PROFILES,
    "DeepSeek R1 (Reasoning)": PricingProfile(
        name="DeepSeek R1 (Reasoning)",
        tier="Tier 2: Frontier Reasoning",
        provider="DeepSeek",
        price_input_uncached=0.55,
        price_input_cached=0.14,
        price_output=2.19,
        cache_min_tokens=64
    )
}


def resolve_tier_pricing(model_name: str, tier_catalog: Dict[str, PricingProfile]) -> PricingProfile:
    """
    Returns the pricing profile for a tier model name.

    Falls back to the zero-cost OpenRouter free-tier profile for explicit
    "vendor/model" OpenRouter IDs that are not present in the catalog, so an
    arbitrary OpenRouter model can be selected without a KeyError and is never
    costed at another vendor's rate.
    """
    profile = tier_catalog.get(model_name)
    if profile is not None:
        return profile
    if "/" in (model_name or ""):
        return OPENROUTER_FREE_TIER_PROFILE
    return PRESET_PRICING_PROFILES["Frontier Standard (Spec Default)"]


@dataclass
class TelemetryMetrics:
    """Container for pipeline telemetry results with dictionary compatibility."""
    cached_input_tokens: int
    uncached_input_tokens: int
    total_input_tokens: int
    reasoning_tokens: int
    output_tokens: int
    total_tokens: int
    total_cost: float
    cache_hit_rate: float
    pricing_profile_name: str = "Frontier Standard (Spec Default)"

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComparisonDelta:
    """Rigorous counterfactual comparison metrics between ITMS and Baseline."""
    cost_reduction_percent: float
    token_reduction_percent: float
    input_tokens_saved: int
    reasoning_tokens_saved: int
    total_tokens_saved: int
    cost_saved_usd: float

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TelemetryEngine:
    """
    Accounting and cost-estimation engine with multi-profile pricing and counterfactual comparison.
    Default Model pricing structures (Standardized per 1M tokens):
    - Frontier (Tier 2):
        Input Uncached: $3.00 / 1M tokens
        Input Cached (KV Hit): $0.30 / 1M tokens (90% discount)
        Output / Reasoning: $15.00 / 1M tokens
    """
    PRICE_INPUT_UNCACHED = 3.00 / 1_000_000
    PRICE_INPUT_CACHED = 0.30 / 1_000_000
    PRICE_OUTPUT = 15.00 / 1_000_000

    @classmethod
    def calculate_itms_metrics(
        cls,
        prompt_data: Dict[str, Any],
        output_tokens: int,
        reasoning_tokens: int,
        pricing: Optional[PricingProfile] = None
    ) -> TelemetryMetrics:
        """Computes hardware-aware token economics and costs for the ITMS pipeline."""
        profile = pricing or PRESET_PRICING_PROFILES["Frontier Standard (Spec Default)"]
        
        static_tokens = prompt_data.get("static_token_count", 0)
        dynamic_tokens = prompt_data.get("dynamic_token_count", 0)
        total_input = static_tokens + dynamic_tokens

        # Static prefix receives the provider KV cache discount
        cost_cached_input = static_tokens * profile.cost_per_cached_token
        cost_uncached_input = dynamic_tokens * profile.cost_per_uncached_token
        cost_generation = (output_tokens + reasoning_tokens) * profile.cost_per_output_token
        total_cost = cost_cached_input + cost_uncached_input + cost_generation

        # Guard against division by zero
        cache_hit_rate = (static_tokens / total_input * 100.0) if total_input > 0 else 0.0

        return TelemetryMetrics(
            cached_input_tokens=static_tokens,
            uncached_input_tokens=dynamic_tokens,
            total_input_tokens=total_input,
            reasoning_tokens=reasoning_tokens,
            output_tokens=output_tokens,
            total_tokens=total_input + output_tokens + reasoning_tokens,
            total_cost=total_cost,
            cache_hit_rate=cache_hit_rate,
            pricing_profile_name=profile.name
        )

    # Backward-compatible alias for existing code and test suites
    calculate_tdsc_metrics = calculate_itms_metrics

    @classmethod
    def calculate_baseline_metrics(
        cls,
        prompt_data: Dict[str, Any],
        output_tokens: int,
        reasoning_tokens: int,
        pricing: Optional[PricingProfile] = None
    ) -> TelemetryMetrics:
        """Computes token metrics and costs for the baseline pipeline."""
        profile = pricing or PRESET_PRICING_PROFILES["Frontier Standard (Spec Default)"]
        
        total_input = prompt_data.get("total_tokens", 0)

        # Baseline pays full price for all input tokens (cache miss caused by non-static prompt)
        cost_input = total_input * profile.cost_per_uncached_token
        cost_generation = (output_tokens + reasoning_tokens) * profile.cost_per_output_token
        total_cost = cost_input + cost_generation

        return TelemetryMetrics(
            cached_input_tokens=0,
            uncached_input_tokens=total_input,
            total_input_tokens=total_input,
            reasoning_tokens=reasoning_tokens,
            output_tokens=output_tokens,
            total_tokens=total_input + output_tokens + reasoning_tokens,
            total_cost=total_cost,
            cache_hit_rate=0.0,
            pricing_profile_name=profile.name
        )

    @staticmethod
    def compare(itms: TelemetryMetrics, baseline: TelemetryMetrics) -> ComparisonDelta:
        """
        Calculates side-by-side counterfactual deltas with division-by-zero protection.
        """
        cost_reduction = (
            (1.0 - (itms.total_cost / baseline.total_cost)) * 100.0
            if baseline.total_cost > 0.0 else 0.0
        )
        token_reduction = (
            (1.0 - (itms.total_tokens / baseline.total_tokens)) * 100.0
            if baseline.total_tokens > 0 else 0.0
        )
        input_saved = baseline.total_input_tokens - itms.total_input_tokens
        reasoning_saved = baseline.reasoning_tokens - itms.reasoning_tokens
        total_tokens_saved = baseline.total_tokens - itms.total_tokens
        cost_saved = baseline.total_cost - itms.total_cost

        return ComparisonDelta(
            cost_reduction_percent=cost_reduction,
            token_reduction_percent=token_reduction,
            input_tokens_saved=input_saved,
            reasoning_tokens_saved=reasoning_saved,
            total_tokens_saved=total_tokens_saved,
            cost_saved_usd=cost_saved
        )

    @classmethod
    def calculate_cascade_lifecycle_metrics(
        cls,
        total_tasks: int = 1000,
        tier1_pass_rate: float = 0.72,
        tier1_profile: Optional[PricingProfile] = None,
        tier2_profile: Optional[PricingProfile] = None,
        t1_input_tokens: int = 600,
        t1_output_tokens: int = 250,
        t2_frontier_direct_input: int = 1200,
        t2_frontier_direct_output: int = 300,
        t2_frontier_direct_reasoning: int = 1500,
        t2_naive_input: int = 1500,
        t2_naive_output: int = 310,
        t2_naive_reasoning: int = 2850,
        itms_cached_input: int = 73,
        itms_dynamic_input: int = 220,
        itms_output: int = 180,
        itms_reasoning: int = 420
    ) -> "CascadeLifecycleMetrics":
        """
        Computes the complete end-to-end lifecycle economics for a batch of coding tasks
        comparing:
        1. Direct Frontier Approach (100% routed to Tier 2)
        2. Naive Agentic Cascade (Tier 1 + contaminated Tier 2 draft loops)
        3. ITMS Speculative Relay (Tier 1 + clean-trace surgical escalation)
        """
        t1 = tier1_profile or TIER_1_BUDGET_PROFILES["Gemini 3.5 Flash"]
        t2 = tier2_profile or TIER_2_FRONTIER_PROFILES["Claude Sonnet 5.5 / Fable"]

        # 1. Direct Frontier
        direct_unit_cost = (
            t2_frontier_direct_input * t2.cost_per_uncached_token +
            (t2_frontier_direct_output + t2_frontier_direct_reasoning) * t2.cost_per_output_token
        )
        direct_frontier_cost = total_tasks * direct_unit_cost

        # 2. Tier 1 Base Run (applied in both cascading systems)
        t1_unit_cost = (
            t1_input_tokens * t1.cost_per_uncached_token +
            t1_output_tokens * t1.cost_per_output_token
        )
        total_t1_base_cost = total_tasks * t1_unit_cost
        escalated_count = int(total_tasks * (1.0 - tier1_pass_rate))

        # 3. Naive Agentic Cascade
        naive_escalate_unit_cost = (
            t2_naive_input * t2.cost_per_uncached_token +
            (t2_naive_output + t2_naive_reasoning) * t2.cost_per_output_token
        )
        naive_agentic_cost = total_t1_base_cost + (escalated_count * naive_escalate_unit_cost)

        # 4. ITMS Speculative Relay
        itms_escalate_unit_cost = (
            itms_cached_input * t2.cost_per_cached_token +
            itms_dynamic_input * t2.cost_per_uncached_token +
            (itms_output + itms_reasoning) * t2.cost_per_output_token
        )
        itms_relay_cost = total_t1_base_cost + (escalated_count * itms_escalate_unit_cost)

        savings_vs_frontier_pct = (
            (1.0 - (itms_relay_cost / direct_frontier_cost)) * 100.0
            if direct_frontier_cost > 0 else 0.0
        )
        savings_vs_naive_pct = (
            (1.0 - (itms_relay_cost / naive_agentic_cost)) * 100.0
            if naive_agentic_cost > 0 else 0.0
        )

        return CascadeLifecycleMetrics(
            total_tasks=total_tasks,
            tier1_pass_rate=tier1_pass_rate,
            tier1_model_name=t1.name,
            tier2_model_name=t2.name,
            direct_frontier_cost=direct_frontier_cost,
            naive_agentic_cost=naive_agentic_cost,
            itms_relay_cost=itms_relay_cost,
            savings_vs_direct_frontier_pct=savings_vs_frontier_pct,
            savings_vs_naive_agentic_pct=savings_vs_naive_pct,
            dollar_savings_vs_frontier=direct_frontier_cost - itms_relay_cost,
            dollar_savings_vs_naive=naive_agentic_cost - itms_relay_cost
        )


@dataclass
class CascadeLifecycleMetrics:
    """End-to-end lifecycle economics for a batch of coding tasks through the two-tiered relay."""
    total_tasks: int
    tier1_pass_rate: float
    tier1_model_name: str
    tier2_model_name: str
    direct_frontier_cost: float
    naive_agentic_cost: float
    itms_relay_cost: float
    savings_vs_direct_frontier_pct: float
    savings_vs_naive_agentic_pct: float
    dollar_savings_vs_frontier: float
    dollar_savings_vs_naive: float

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

