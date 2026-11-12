"""Per-agent cost tracking using genai-prices."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from genai_prices import calc_price
from pydantic_ai.usage import RunUsage

logger = logging.getLogger(__name__)

# Provider ID mapping for genai-prices
PROVIDER_MAP: dict[str, str] = {
    "bedrock": "anthropic",
    "claude-sdk": "anthropic",
    "azure": "openai",
    "zen": "openai",
    "codex": "openai",
    "google": "google",
}

# Fallback pricing for models not in genai-prices (per 1M tokens, USD)
FALLBACK_PRICING: dict[str, dict[str, float]] = {
    "us.anthropic.claude-opus-4-6-v1": {
        "input": 5.00,
        "cached_input": 0.50,
        "output": 25.00,
    },
    "claude-opus-4-6": {
        "input": 5.00,
        "cached_input": 0.50,
        "output": 25.00,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "cached_input": 0.075,
        "output": 4.50,
    },
    "gpt-5.4": {
        "input": 2.50,
        "cached_input": 0.25,
        "output": 15.00,
    },
    "gpt-5.3-codex": {
        "input": 1.75,
        "cached_input": 0.175,
        "output": 14.00,
    },
    "gpt-5.3-codex-spark": {
        "input": 0.50,
        "cached_input": 0.05,
        "output": 2.00,
    },
    "gemini-3-flash-preview": {
        "input": 0.15,
        "cached_input": 0.02,
        "output": 0.60,
    },
}


# Pricing injected from Settings (env/config) for provider-specific models.
CONFIGURED_PRICING: dict[str, dict[str, float]] = {}
_warned_models: set[str] = set()


def configure_pricing(pricing: dict[str, dict[str, float]]) -> None:
    """Register per-model pricing (USD per 1M tokens: input/cached_input/output)."""
    CONFIGURED_PRICING.update(pricing)


def configure_pricing_from_settings(settings: Any) -> None:
    """Register pricing from Settings.model_*_price_per_mtok for model_name."""
    model = getattr(settings, "model_name", "") or ""
    input_rate = getattr(settings, "model_input_price_per_mtok", 0.0) or 0.0
    output_rate = getattr(settings, "model_output_price_per_mtok", 0.0) or 0.0
    if not model or not (input_rate or output_rate):
        return
    cached_rate = getattr(settings, "model_cached_input_price_per_mtok", 0.0) or input_rate
    configure_pricing(
        {model: {"input": input_rate, "cached_input": cached_rate, "output": output_rate}}
    )


def _normalize_model(model: str) -> str:
    """Strip provider spec prefix: 'anthropic/GLM-5.1' -> 'GLM-5.1'."""
    return model.rsplit("/", 1)[-1] if "/" in model else model


def _calc_fallback_cost(usage: RunUsage, model: str) -> float | None:
    pricing = FALLBACK_PRICING.get(model) or CONFIGURED_PRICING.get(model)
    if not pricing:
        return None
    input_rate = pricing.get("input", 0)
    cached_rate = pricing.get("cached_input", input_rate)
    output_rate = pricing.get("output", 0)
    uncached = max(0, usage.input_tokens - usage.cache_read_tokens)
    return (
        (uncached * input_rate) / 1_000_000
        + (usage.cache_read_tokens * cached_rate) / 1_000_000
        + (usage.output_tokens * output_rate) / 1_000_000
    )


def calc_cost(usage: RunUsage, model_name: str, provider_spec: str = "") -> float:
    """Calculate cost using genai-prices with fallback."""
    if not usage.has_values():
        return 0.0

    model_name = _normalize_model(model_name)
    provider_id = PROVIDER_MAP.get(provider_spec, "unknown")

    try:
        price = calc_price(usage, model_name, provider_id=provider_id)
        return float(price.total_price)
    except Exception:
        pass

    fallback = _calc_fallback_cost(usage, model_name)
    if fallback is not None:
        return fallback

    if model_name not in _warned_models:
        _warned_models.add(model_name)
        logger.warning(
            f"No pricing available for {model_name}; cost recorded as $0. "
            f"Set MODEL_INPUT_PRICE_PER_MTOK / MODEL_OUTPUT_PRICE_PER_MTOK "
            f"to enable cost tracking and budget limits."
        )
    return 0.0


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _cache_rate(usage: RunUsage) -> str:
    """Compute cache hit rate as a percentage string."""
    if usage.input_tokens == 0:
        return "n/a"
    rate = (usage.cache_read_tokens / usage.input_tokens) * 100
    return f"{rate:.0f}%"


@dataclass
class AgentUsage:
    usage: RunUsage = field(default_factory=RunUsage)
    model_name: str = ""
    provider_spec: str = ""
    duration_seconds: float = 0.0
    cost_usd: float = 0.0


@dataclass
class CostTracker:
    by_agent: dict[str, AgentUsage] = field(default_factory=dict)
    # Cost carried over from a previous run (loaded from the state snapshot).
    prior_cost_usd: float = 0.0

    def record_tokens(
        self,
        agent_name: str,
        model_name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        provider_spec: str = "",
        duration_seconds: float = 0.0,
    ) -> None:
        """Record token usage without requiring pydantic_ai.RunUsage."""
        usage = RunUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        self.record(agent_name, usage, model_name, provider_spec, duration_seconds)

    def record(
        self,
        agent_name: str,
        usage: RunUsage,
        model_name: str,
        provider_spec: str = "",
        duration_seconds: float = 0.0,
    ) -> None:
        cost = calc_cost(usage, model_name, provider_spec)

        if agent_name not in self.by_agent:
            self.by_agent[agent_name] = AgentUsage(
                model_name=model_name, provider_spec=provider_spec
            )

        agent = self.by_agent[agent_name]
        agent.usage += usage
        agent.duration_seconds += duration_seconds
        agent.cost_usd += cost

        # Log per-step with cache rate (f-string avoids Rich handler % formatting issues)
        logger.debug(
            f"{agent_name}: {_fmt_tokens(usage.input_tokens)} in / "
            f"{_fmt_tokens(usage.cache_read_tokens)} cached ({_cache_rate(usage)} hit) / "
            f"{_fmt_tokens(usage.output_tokens)} out | ${cost:.4f} | {duration_seconds:.1f}s"
        )

    @property
    def total_cost_usd(self) -> float:
        return self.prior_cost_usd + sum(a.cost_usd for a in self.by_agent.values())

    def cost_for_agent_prefix(self, prefix: str) -> float:
        """Sum cost of agents whose name starts with prefix (e.g. 'challenge/')."""
        return sum(a.cost_usd for name, a in self.by_agent.items() if name.startswith(prefix))

    @property
    def total_tokens(self) -> int:
        total = RunUsage()
        for a in self.by_agent.values():
            total += a.usage
        return total.total_tokens

    def format_usage(self, agent_name: str) -> str:
        """Format: '45k in / 32k cached (71% hit) / 2.1k out | $0.05 | 28.3s'"""
        agent = self.by_agent.get(agent_name)
        if not agent:
            return ""
        u = agent.usage
        return (
            f"{_fmt_tokens(u.input_tokens)} in / "
            f"{_fmt_tokens(u.cache_read_tokens)} cached ({_cache_rate(u)} hit) / "
            f"{_fmt_tokens(u.output_tokens)} out | "
            f"${agent.cost_usd:.2f} | "
            f"{agent.duration_seconds:.1f}s"
        )

    def get_usage_by_model(self) -> dict[str, dict[str, Any]]:
        by_model: dict[str, dict[str, Any]] = {}
        for agent in self.by_agent.values():
            model = agent.model_name
            if model not in by_model:
                by_model[model] = {"cost": 0.0, "input": 0, "cached": 0, "output": 0}
            by_model[model]["cost"] += agent.cost_usd
            by_model[model]["input"] += agent.usage.input_tokens
            by_model[model]["cached"] += agent.usage.cache_read_tokens
            by_model[model]["output"] += agent.usage.output_tokens
        return by_model

    def log_summary(self) -> None:
        """Log summary grouped by model with cache hit rates."""
        by_model = self.get_usage_by_model()
        for model, s in by_model.items():
            hit_rate = f"{(s['cached'] / s['input'] * 100):.0f}%" if s["input"] > 0 else "n/a"
            logger.info(
                "  %s: $%.2f | %s in / %s cached (%s hit) / %s out",
                model, s["cost"],
                _fmt_tokens(s["input"]),
                _fmt_tokens(s["cached"]),
                hit_rate,
                _fmt_tokens(s["output"]),
            )
        total = sum(s["input"] for s in by_model.values())
        total_cached = sum(s["cached"] for s in by_model.values())
        overall_hit = f"{(total_cached / total * 100):.0f}%" if total > 0 else "n/a"
        logger.info(
            "  Total: $%.2f | %s tokens | %s overall cache hit rate",
            self.total_cost_usd,
            _fmt_tokens(self.total_tokens),
            overall_hit,
        )
