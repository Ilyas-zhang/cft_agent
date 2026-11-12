"""Docker verification script for CTF Agent with GLM-5.1.

Usage: docker run --rm -e ANTHROPIC_API_KEY=your_key ctf-agent python /app/scripts/verify_docker.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback


def check_python_version():
    """Check 1: Python 3.14 is available."""
    print("=" * 60)
    print("CHECK 1: Python version")
    print(f"  Python: {sys.version}")
    major, minor = sys.version_info[:2]
    if major >= 3 and minor >= 14:
        print("  PASS ✓")
        return True
    print(f"  FAIL ✗ (expected >= 3.14, got {major}.{minor})")
    return False


def check_dependencies():
    """Check 2: Key dependencies are importable."""
    print("=" * 60)
    print("CHECK 2: Dependencies")
    deps = [
        ("pydantic_ai", "pydantic-ai"),
        ("pydantic_settings", "pydantic-settings"),
        ("httpx", "httpx"),
        ("aiodocker", "aiodocker"),
        ("yaml", "pyyaml"),
        ("rich", "rich"),
        ("click", "click"),
    ]
    all_ok = True
    for module, package in deps:
        try:
            __import__(module)
            print(f"  {package}: PASS ✓")
        except ImportError:
            print(f"  {package}: FAIL ✗ (not installed)")
            all_ok = False
    return all_ok


def check_config():
    """Check 3: Configuration loads correctly."""
    print("=" * 60)
    print("CHECK 3: Configuration")
    try:
        from backend.config import Settings
        settings = Settings()
        print(f"  model_name: {settings.model_name}")
        print(f"  model_timeout: {settings.model_timeout}")
        print(f"  max_output_tokens: {settings.max_output_tokens}")
        print(f"  max_retries: {settings.max_retries}")
        print(f"  max_concurrency: {settings.max_concurrency}")
        print(f"  anthropic_base_url: {settings.anthropic_base_url or '(not set)'}")
        has_key = bool(settings.anthropic_api_key)
        print(f"  anthropic_api_key: {'(set)' if has_key else '(not set)'}")
        print("  PASS ✓")
        return True
    except Exception as e:
        print(f"  FAIL ✗ ({e})")
        traceback.print_exc()
        return False


def check_model_resolution():
    """Check 4: Model resolution works."""
    print("=" * 60)
    print("CHECK 4: Model resolution")
    try:
        from backend.models import (
            DEFAULT_MODELS,
            provider_from_spec,
            model_id_from_spec,
            context_window,
            supports_vision,
            supports_tool_use,
        )
        print(f"  DEFAULT_MODELS: {DEFAULT_MODELS}")
        spec = DEFAULT_MODELS[0]
        print(f"  Provider: {provider_from_spec(spec)}")
        print(f"  Model ID: {model_id_from_spec(spec)}")
        print(f"  Context window: {context_window(spec)}")
        print(f"  Vision: {supports_vision(spec)}")
        print(f"  Tool use: {supports_tool_use(spec)}")
        print("  PASS ✓")
        return True
    except Exception as e:
        print(f"  FAIL ✗ ({e})")
        traceback.print_exc()
        return False


def check_anthropic_model():
    """Check 5: AnthropicModel can be created (with API key)."""
    print("=" * 60)
    print("CHECK 5: Anthropic model creation")
    try:
        from backend.models import resolve_model
        from backend.config import Settings
        settings = Settings()
        if not settings.anthropic_api_key:
            print("  SKIP (ANTHROPIC_API_KEY not set)")
            return None
        model = resolve_model("anthropic/GLM-5.1", settings)
        from pydantic_ai.models.anthropic import AnthropicModel
        assert isinstance(model, AnthropicModel)
        print(f"  Model type: {type(model).__name__}")
        print("  PASS ✓")
        return True
    except Exception as e:
        print(f"  FAIL ✗ ({e})")
        traceback.print_exc()
        return False


async def check_basic_model_call():
    """Check 6: Basic model API call (requires credentials)."""
    print("=" * 60)
    print("CHECK 6: Basic model API call")
    try:
        from backend.models import resolve_model
        from backend.config import Settings
        from pydantic_ai import Agent

        settings = Settings()
        if not settings.anthropic_api_key:
            print("  SKIP (ANTHROPIC_API_KEY not set)")
            return None

        model = resolve_model("anthropic/GLM-5.1", settings)
        agent = Agent(model, output_type=str)
        result = await agent.run("What is 2+2? Answer with just the number.")

        if "4" in result.output:
            print(f"  Response: {result.output[:100]}")
            print("  PASS ✓")
            return True
        else:
            print(f"  Unexpected response: {result.output[:200]}")
            print("  FAIL ✗")
            return False
    except Exception as e:
        print(f"  FAIL ✗ ({e})")
        traceback.print_exc()
        return False


async def check_tool_call():
    """Check 7: Tool calling (requires credentials)."""
    print("=" * 60)
    print("CHECK 7: Tool calling")
    try:
        from backend.models import resolve_model
        from backend.config import Settings
        from pydantic_ai import Agent, RunContext

        settings = Settings()
        if not settings.anthropic_api_key:
            print("  SKIP (ANTHROPIC_API_KEY not set)")
            return None

        def add_numbers(ctx: RunContext[None], a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        model = resolve_model("anthropic/GLM-5.1", settings)
        agent = Agent(model, output_type=int, tools=[add_numbers])
        result = await agent.run("What is 3 + 5?")

        if result.output == 8:
            print(f"  Tool call result: {result.output}")
            print("  PASS ✓")
            return True
        else:
            print(f"  Unexpected result: {result.output}")
            print("  FAIL ✗")
            return False
    except Exception as e:
        print(f"  FAIL ✗ ({e})")
        traceback.print_exc()
        return False


def check_interactive_tools():
    """Check 8: Interactive tools import correctly."""
    print("=" * 60)
    print("CHECK 8: Interactive tools")
    try:
        from backend.interactive.http_session import HttpSession, http_request
        from backend.interactive.tcp_session import TcpSession, tcp_connect, tcp_send_recv
        print("  HTTP session: PASS ✓")
        print("  TCP session: PASS ✓")
        return True
    except Exception as e:
        print(f"  FAIL ✗ ({e})")
        traceback.print_exc()
        return False


def check_platform_adapter():
    """Check 9: Platform adapter interface."""
    print("=" * 60)
    print("CHECK 9: Platform adapter")
    try:
        from backend.platform.base import PlatformAdapter, ChallengeInfo, SubmitResult
        from backend.platform.ctfd_adapter import CTFdAdapter
        print("  Base interface: PASS ✓")
        print("  CTFd adapter: PASS ✓")
        return True
    except Exception as e:
        print(f"  FAIL ✗ ({e})")
        traceback.print_exc()
        return False


async def main():
    """Run all verification checks."""
    print("\n" + "=" * 60)
    print("CTF Agent — GLM-5.1 Verification Suite")
    print("=" * 60 + "\n")

    results = {}

    # Sync checks
    results["python_version"] = check_python_version()
    results["dependencies"] = check_dependencies()
    results["config"] = check_config()
    results["model_resolution"] = check_model_resolution()
    results["anthropic_model"] = check_anthropic_model()
    results["interactive_tools"] = check_interactive_tools()
    results["platform_adapter"] = check_platform_adapter()

    # Async checks
    results["basic_model_call"] = await check_basic_model_call()
    results["tool_call"] = await check_tool_call()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    total = len(results)

    for name, result in results.items():
        if result is True:
            status = "PASS ✓"
        elif result is False:
            status = "FAIL ✗"
        else:
            status = "SKIP —"
        print(f"  {name}: {status}")

    print(f"\n  {passed} passed, {failed} failed, {skipped} skipped, {total} total")

    if failed > 0:
        print("\n  ⚠️  Some checks FAILED — see details above.")
        sys.exit(1)
    else:
        print("\n  ✅ All checks PASSED (skipped checks require API credentials).")

    return results


if __name__ == "__main__":
    asyncio.run(main())
