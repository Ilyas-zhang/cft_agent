"""Pydantic Settings — credentials from .env file + environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings

# Never let credentials leak through repr()/str()/debug logging.
_SENSITIVE_FIELDS = frozenset({
    "anthropic_api_key",
    "openai_api_key",
    "gemini_api_key",
    "aws_bearer_token",
    "azure_openai_api_key",
    "opencode_zen_api_key",
})


class Settings(BaseSettings):
    # CTFd
    ctfd_url: str = "http://localhost:8000"
    ctfd_user: str = "admin"
    ctfd_pass: str = "admin"
    ctfd_token: str = ""

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # Provider-specific (optional, for Bedrock/Azure/Zen fallback)
    aws_region: str = "us-east-1"
    aws_bearer_token: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    opencode_zen_api_key: str = ""

    # ── GLM / Anthropic-compatible model configuration ──────────────────────
    # These fields support connecting to any Anthropic Messages-compatible API
    # (e.g. GLM-5.1, or any other provider exposing an Anthropic-compatible endpoint).
    anthropic_base_url: str = ""  # e.g. https://open.bigmodel.cn/api/paas/v4
    model_name: str = "GLM-5.1"  # Model ID as required by the provider
    model_timeout: int = 300  # Request timeout in seconds
    max_output_tokens: int = 128_000  # Maximum output tokens per request
    max_retries: int = 3  # API retry count on transient failures
    max_concurrency: int = 5  # Max parallel API calls
    api_retry_delay: float = 1.0  # Initial retry delay (exponential backoff)

    # ── Budget limits ────────────────────────────────────────────────────────
    max_cost_per_challenge: float = 10.0  # USD budget per challenge
    max_cost_total: float = 500.0  # USD total budget across all challenges
    max_attempts_per_challenge: int = 10  # Max solver attempts before giving up

    # ── Pricing (USD per 1M tokens) ─────────────────────────────────────────
    # genai-prices covers many public models; private/provider-specific models
    # such as GLM-5.1 need explicit rates. If all stay 0, cost is recorded as
    # $0 and cost-based budget limits cannot trigger (attempt limits still do).
    model_input_price_per_mtok: float = 0.0
    model_cached_input_price_per_mtok: float = 0.0
    model_output_price_per_mtok: float = 0.0

    # Infra
    sandbox_image: str = "ctf-sandbox"
    max_concurrent_challenges: int = 10
    container_memory_limit: str = "16g"
    container_cpus: float = 2.0

    # ── Path mapping for Docker-in-Docker (agent runs in a container) ──────
    # When the agent runs inside a Docker container and talks to the host
    # Docker daemon, bind-mount sources for sibling sandbox containers must be
    # paths visible to the *host*, not the agent container.
    # workspace_root: agent-side root mount point (e.g. /work)
    # sandbox_bind_root: the same directory as seen by the host (e.g. D:/code/...)
    # If either is empty, sandbox paths are used as-is (agent on host).
    workspace_root: str = ""
    sandbox_bind_root: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def __repr_args__(self):
        """Mask credentials in repr(settings) / str(settings)."""
        for name, value in super().__repr_args__():
            if name in _SENSITIVE_FIELDS and value:
                yield name, "***REDACTED***"
            else:
                yield name, value
