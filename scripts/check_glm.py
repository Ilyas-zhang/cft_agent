"""Verify the configured GLM-5.1 endpoint: one chat request + one tool call.

Run inside the agent container (dependencies installed, /work mounted):

    docker exec -w /work ctf-dev python scripts/check_glm.py

Reads .env / environment variables via Settings. Never prints the API key.
Exit codes: 0 = both checks passed, 1 = a check failed, 2 = no credentials.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.messages import ToolCallPart  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.cost_tracker import calc_cost, configure_pricing_from_settings  # noqa: E402
from backend.models import (  # noqa: E402
    model_id_from_spec,
    resolve_model,
    resolve_model_settings,
)


def _redact(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "***REDACTED***")
    return text


def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


async def main() -> int:
    settings = Settings()
    if not settings.anthropic_api_key:
        print("SKIP: ANTHROPIC_API_KEY is not configured.")
        print("Set it in .env (never commit it) and rerun this script.")
        return 2

    spec = f"anthropic/{settings.model_name}"
    print(f"Model     : {spec}")
    print(f"Base URL  : {settings.anthropic_base_url or '(provider default)'}")
    print(f"Timeout   : {settings.model_timeout}s, retries: {settings.max_retries}")

    configure_pricing_from_settings(settings)
    model = resolve_model(spec, settings)
    model_settings = resolve_model_settings(spec, settings)

    agent = Agent(
        model,
        model_settings=model_settings,
        tools=[add],
        output_type=str,
        system_prompt="You are a test harness. Use tools when asked.",
    )

    try:
        result = await agent.run(
            "Use the add tool to compute 17 + 25, then reply with only the number."
        )
    except Exception as e:
        print(f"FAIL: model request raised {type(e).__name__}: {_redact(str(e), settings.anthropic_api_key)}")
        return 1

    calls = [
        part.tool_name
        for msg in result.all_messages()
        for part in getattr(msg, "parts", [])
        if isinstance(part, ToolCallPart)
    ]
    output = str(result.output).strip()
    usage = result.usage
    cost = calc_cost(usage, model_id_from_spec(spec), "anthropic")

    print(f"Tool calls: {calls or '(none)'}")
    print(f"Output    : {output[:200]}")
    print(f"Usage     : {usage.input_tokens} in / {usage.output_tokens} out")
    print(f"Est. cost : ${cost:.4f}")

    if not calls:
        print("FAIL: model did not make a tool call.")
        return 1
    if "42" not in output:
        print("FAIL: expected 42 in the final answer.")
        return 1

    print("PASS: model responded and executed a tool call.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
