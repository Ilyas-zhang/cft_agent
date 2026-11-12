"""Pydantic AI coordinator — uses any model that pydantic-ai supports.

This replaces the claude-sdk and codex coordinators for GLM-5.1 operation.
The coordinator uses the same model as the solvers, with MCP-like tools
implemented as pydantic-ai function tools.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets import FunctionToolset

from backend.agents.coordinator_core import (
    do_broadcast,
    do_bump_agent,
    do_check_swarm_status,
    do_fetch_challenges,
    do_get_solve_status,
    do_kill_swarm,
    do_read_solver_trace,
    do_spawn_swarm,
    do_submit_flag,
)
from backend.agents.coordinator_loop import build_deps, run_event_loop
from backend.config import Settings
from backend.deps import CoordinatorDeps
from backend.models import (
    model_id_from_spec,
    provider_from_spec,
    resolve_model,
    resolve_model_settings,
)

logger = logging.getLogger(__name__)

COORDINATOR_PROMPT = """\
You are a CTF competition coordinator running for the ENTIRE duration of a live competition.
Your job is to maximize the number of challenges solved.

Strategy:
- Spawn swarms for unsolved challenges, prioritizing by solve count (easy first)
- Use read_solver_trace to monitor what each solver is doing and where it's stuck
- When agents are stuck, read their traces, then craft targeted bumps with specific technical guidance
- Use broadcast to share cross-solver insights (e.g. flag format discovery, shared vulnerabilities)

CRITICAL RULES:
- NEVER kill a swarm. Solvers will keep trying indefinitely with different approaches.
  Even when stuck, they often unstick themselves after several bumps. Your job is to
  HELP them, not give up on them. The only time a swarm should die is when the flag
  is confirmed correct.
- When a solver seems stuck, bump it with very specific technical guidance based on
  its trace. Tell it exactly what to try next — specific tools, techniques, approaches.
- The flag lives on the provided challenge service. NEVER tell a solver to abandon
  the service and use `process()`/local execution instead; a local copy of the binary
  does not contain the service's flag. Local execution is useful only to test an
  exploit that will then be run against the service.
- A service closing after a game/session ends (for example, printing GAME OVER) is
  normal. It is not evidence that the network is down; tell the solver to reconnect
  for a fresh round if that is useful, not to abandon the service.
- Do not interrupt a solver that is making concrete progress (new leak, primitive,
  working protocol step). Generic "execute now" or "stop analyzing" bumps waste its
  context. Bump only with a new, specific technical fact and the exact next action.
- A periodic STATUS line only means the swarm is still running; it is not evidence
  that the solver is stuck. Before bumping, call read_solver_trace and compare it
  with what you saw last turn. If there are new tool_call/tool_result events since
  then, do not bump. Never claim "you are stuck at step N" without checking.
- Respect the configured cost and attempt budgets. Do not spawn duplicate swarms or
  ask a solver to repeat work that already succeeded.

You will receive event messages. Respond with tool calls to manage the competition.
"""


# ── Coordinator tool functions ──────────────────────────────────────────────


async def fetch_challenges(ctx: RunContext[CoordinatorDeps]) -> str:
    """List all challenges with category, points, solve count, and status."""
    return await do_fetch_challenges(ctx.deps)


async def get_solve_status(ctx: RunContext[CoordinatorDeps]) -> str:
    """Check which challenges are solved and which swarms are running."""
    return await do_get_solve_status(ctx.deps)


async def spawn_swarm(ctx: RunContext[CoordinatorDeps], challenge_name: str) -> str:
    """Launch all solver models on a challenge."""
    return await do_spawn_swarm(ctx.deps, challenge_name)


async def check_swarm_status(ctx: RunContext[CoordinatorDeps], challenge_name: str) -> str:
    """Get per-agent progress for a swarm."""
    return await do_check_swarm_status(ctx.deps, challenge_name)


async def submit_flag_to_ctfd(ctx: RunContext[CoordinatorDeps], challenge_name: str, flag: str) -> str:
    """Submit a flag to CTFd."""
    return await do_submit_flag(ctx.deps, challenge_name, flag)


async def kill_swarm(ctx: RunContext[CoordinatorDeps], challenge_name: str) -> str:
    """Cancel all agents for a challenge."""
    return await do_kill_swarm(ctx.deps, challenge_name)


async def bump_agent(ctx: RunContext[CoordinatorDeps], challenge_name: str, model_spec: str, insights: str) -> str:
    """Send targeted insights to a stuck agent."""
    return await do_bump_agent(ctx.deps, challenge_name, model_spec, insights)


async def broadcast(ctx: RunContext[CoordinatorDeps], challenge_name: str, message: str) -> str:
    """Broadcast a strategic hint to ALL solvers on a challenge."""
    return await do_broadcast(ctx.deps, challenge_name, message)


async def read_solver_trace(ctx: RunContext[CoordinatorDeps], challenge_name: str, model_spec: str, last_n: int = 20) -> str:
    """Read recent trace events from a specific solver. Use this to understand what a solver is doing, what it tried, and where it's stuck."""
    return await do_read_solver_trace(ctx.deps, challenge_name, model_spec, last_n)


# ── Build coordinator agent ─────────────────────────────────────────────────


def _build_coordinator_agent(model_spec: str, settings: Settings) -> Agent[CoordinatorDeps, str]:
    """Build a Pydantic AI Agent for the coordinator."""
    model = resolve_model(model_spec, settings)
    model_settings = resolve_model_settings(model_spec, settings)

    toolset = FunctionToolset(tools=[
        fetch_challenges,
        get_solve_status,
        spawn_swarm,
        check_swarm_status,
        submit_flag_to_ctfd,
        kill_swarm,
        bump_agent,
        broadcast,
        read_solver_trace,
    ])

    agent = Agent(
        model,
        deps_type=CoordinatorDeps,
        system_prompt=COORDINATOR_PROMPT,
        model_settings=model_settings,
        toolsets=[toolset],
        output_type=str,
    )
    return agent


async def run_pydantic_coordinator(
    settings: Settings,
    model_specs: list[str] | None = None,
    challenges_root: str = "challenges",
    no_submit: bool = False,
    coordinator_model: str | None = None,
    msg_port: int = 0,
) -> dict[str, Any]:
    """Run the Pydantic AI coordinator."""
    ctfd, cost_tracker, deps = build_deps(
        settings, model_specs, challenges_root, no_submit,
    )
    deps.msg_port = msg_port

    # Use the first model spec for coordinator, or explicit override
    coord_spec = coordinator_model or (model_specs[0] if model_specs else "anthropic/GLM-5.1")
    agent = _build_coordinator_agent(coord_spec, settings)

    messages: list = []

    async def turn_fn(msg: str) -> None:
        """Send a message to the coordinator and let it respond with tool calls."""
        logger.debug(f"Coordinator query: {msg[:200]}")

        try:
            from pydantic_ai.usage import UsageLimits
            result = await agent.run(
                msg,
                deps=deps,
                message_history=messages if messages else None,
                usage_limits=UsageLimits(request_limit=None),
            )

            # Update message history for context continuity
            messages.extend(result.new_messages())

            # Track usage
            usage = result.usage  # pydantic-ai 2.x: property, not method
            cost_tracker.record(
                "coordinator", usage, model_id_from_spec(coord_spec),
                provider_spec=provider_from_spec(coord_spec),
            )

            logger.info(
                f"Coordinator turn done "
                f"(in={usage.input_tokens}, out={usage.output_tokens}, "
                f"cost=${cost_tracker.by_agent.get('coordinator', type('',(),{'cost_usd':0})()).cost_usd:.4f})"
            )
        except Exception as e:
            logger.error(f"Coordinator turn error: {e}", exc_info=True)

    return await run_event_loop(deps, ctfd, cost_tracker, turn_fn)
