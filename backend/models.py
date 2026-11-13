"""Model resolution — Anthropic-compatible, Zai (Zhipu GLM), Bedrock, Azure/Zen, Google.

Verified against pydantic-ai 2.61.0:
- AnthropicModel + AnthropicProvider(api_key=..., base_url=...)
- ZaiModel + ZaiProvider(api_key=..., base_url=...)
- OpenAIChatModel + OpenAIProvider(base_url=..., api_key=...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.zai import ZaiModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.zai import ZaiProvider
from pydantic_ai.settings import ModelSettings

if TYPE_CHECKING:
    from backend.config import Settings

# Default model specs — GLM-5.1 via Anthropic-compatible endpoint.
# Format: provider/model_id[/effort]
# Single model by default: no unauthorized fallback to other paid models.
DEFAULT_MODELS: list[str] = [
    "anthropic/GLM-5.1",
]

# Context window sizes (tokens)
CONTEXT_WINDOWS: dict[str, int] = {
    # GLM-5.1 — conservative estimate until confirmed by provider docs
    "GLM-5.1": 128_000,
    # Legacy models kept for reference
    "us.anthropic.claude-opus-4-6-v1": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "gpt-5.4": 1_000_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.3-codex": 1_000_000,
    "gpt-5.3-codex-spark": 128_000,
    "gemini-3-flash-preview": 1_000_000,
}

# Models that support vision (verify against provider docs before relying on it)
VISION_MODELS: set[str] = {
    "GLM-5.1",
    "us.anthropic.claude-opus-4-6-v1",
    "claude-opus-4-6",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gemini-3-flash-preview",
}

# Models that support tool use (function calling)
TOOL_USE_MODELS: set[str] = {
    "GLM-5.1",
}


def resolve_model(spec: str, settings: Settings) -> Model:
    """Resolve a 'provider/model_id' spec to a Pydantic AI Model."""
    provider = provider_from_spec(spec)
    model_id = model_id_from_spec(spec)
    match provider:
        case "openai":
            if not settings.openai_api_key or not settings.openai_base_url:
                raise ValueError("OPENAI_API_KEY and OPENAI_BASE_URL are required")
            return OpenAIChatModel(model_id, provider=OpenAIProvider(
                base_url=settings.openai_base_url, api_key=settings.openai_api_key,
            ))
        case "anthropic":
            # Anthropic Messages-compatible endpoint (GLM-5.1 or any compatible API)
            api_key = settings.anthropic_api_key
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY is required for the 'anthropic' provider. "
                    "Set it in .env or as an environment variable."
                )
            return AnthropicModel(
                model_id,
                provider=AnthropicProvider(
                    api_key=api_key,
                    base_url=settings.anthropic_base_url or None,
                ),
            )
        case "zai":
            # Zhipu AI native endpoint (GLM models, OpenAI-compatible protocol)
            api_key = settings.anthropic_api_key
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY is required for the 'zai' provider. "
                    "Set it in .env or as an environment variable."
                )
            return ZaiModel(
                model_id,
                provider=ZaiProvider(
                    api_key=api_key,
                    base_url=settings.anthropic_base_url or None,
                ),
            )
        case "bedrock":
            if settings.aws_bearer_token:
                return BedrockConverseModel(
                    model_id,
                    provider=BedrockProvider(
                        api_key=settings.aws_bearer_token,
                        region_name=settings.aws_region,
                    ),
                )
            session = boto3.Session()
            client = session.client("bedrock-runtime", region_name=settings.aws_region)
            return BedrockConverseModel(
                model_id,
                provider=BedrockProvider(bedrock_client=client),
            )
        case "azure":
            return OpenAIChatModel(
                model_id,
                provider=OpenAIProvider(
                    base_url=settings.azure_openai_endpoint,
                    api_key=settings.azure_openai_api_key,
                ),
            )
        case "zen":
            return OpenAIChatModel(
                model_id,
                provider=OpenAIProvider(
                    base_url="https://opencode.ai/zen/v1",
                    api_key=settings.opencode_zen_api_key,
                ),
            )
        case "google":
            return GoogleModel(
                model_id,
                provider=GoogleProvider(api_key=settings.gemini_api_key),
            )
        case "claude-sdk" | "codex":
            raise ValueError(
                f"Provider '{provider}' uses its own solver backend, not Pydantic AI. "
                f"resolve_model() should not be called for {spec}."
            )
        case _:
            raise ValueError(f"Unknown provider: {provider}")


def resolve_model_settings(spec: str, settings: Settings | None = None) -> ModelSettings:
    """Get provider-specific model settings."""
    provider = spec.split("/", 1)[0]
    max_tokens = getattr(settings, "max_output_tokens", 128_000) if settings else 128_000
    # Per-request timeout and provider-SDK retry count are configurable so a
    # stalled endpoint cannot hang a solver forever.
    timeout = float(getattr(settings, "model_timeout", 300) or 300) if settings else 300.0
    retries = int(getattr(settings, "max_retries", 3)) if settings else 3
    common = {
        "max_tokens": max_tokens,
        "timeout": timeout,
        "request_timeout": timeout,
        "max_retries": retries,
    }
    match provider:
        case "anthropic" | "zai":
            return ModelSettings(**common)
        case "bedrock":
            return BedrockModelSettings(
                **common,
                bedrock_cache_instructions=True,
                bedrock_cache_tool_definitions=True,
                bedrock_cache_messages=True,
            )
        case "openai" | "azure" | "zen":
            # OpenAI-compatible chat completions — prompt caching is automatic
            return OpenAIChatModelSettings(**common)
        case "google":
            return GoogleModelSettings(
                **common,
                google_thinking_config={
                    "thinking_level": "high",
                    "include_thoughts": True,
                },
            )
        case _:
            return ModelSettings(**common)


def model_id_from_spec(spec: str) -> str:
    """Extract just the model ID from a spec (strips effort suffix)."""
    parts = spec.split("/")
    return parts[1] if len(parts) >= 2 else spec


def provider_from_spec(spec: str) -> str:
    """Extract the provider from a spec."""
    return spec.split("/", 1)[0]


def effort_from_spec(spec: str) -> str | None:
    """Extract effort level from a spec like 'anthropic/GLM-5.1/high'."""
    parts = spec.split("/")
    if len(parts) >= 3 and parts[2] in ("low", "medium", "high", "max"):
        return parts[2]
    return None


def supports_vision(spec: str) -> bool:
    """Check if a model spec supports vision."""
    return model_id_from_spec(spec) in VISION_MODELS


def supports_tool_use(spec: str) -> bool:
    """Check if a model spec supports tool use (function calling)."""
    return model_id_from_spec(spec) in TOOL_USE_MODELS


def context_window(spec: str) -> int:
    """Get context window size for a model spec."""
    return CONTEXT_WINDOWS.get(model_id_from_spec(spec), 200_000)
