"""Flag submission tool."""

import json

from pydantic_ai import RunContext

from backend.deps import SolverDeps
from backend.tools.core import do_submit_flag


async def submit_flag(ctx: RunContext[SolverDeps], flag: str | list[str]) -> str:
    """Submit a flag to CTFd to verify it. Always call this before reporting a flag.

    For a CTFy multi-select question, pass its answer as a list of choice strings.
    Returns CORRECT, ALREADY SOLVED, or INCORRECT.
    Do NOT submit placeholder flags like CTF{flag} or CTF{placeholder}.
    """
    if ctx.deps.no_submit:
        return f'DRY RUN — would submit "{flag}" but --no-submit is set.'

    if isinstance(flag, list) and not ctx.deps.competition_mode:
        return "Array answers are supported only in the CTFy competition mode."

    # Use deduped submission via swarm if available, otherwise direct CTFd call
    if ctx.deps.submit_fn:
        display, is_confirmed = await ctx.deps.submit_fn(flag)
    else:
        display, is_confirmed = await do_submit_flag(ctx.deps.ctfd, ctx.deps.challenge_name, flag)
    if is_confirmed:
        ctx.deps.confirmed_flag = flag.strip() if isinstance(flag, str) else json.dumps(flag, ensure_ascii=False)
    return display
