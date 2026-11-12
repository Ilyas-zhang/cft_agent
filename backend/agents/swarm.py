"""ChallengeSwarm — Parallel solvers racing on one challenge."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.agents.solver import Solver
from backend.cost_tracker import CostTracker
from backend.ctfd import CTFdClient
from backend.message_bus import ChallengeMessageBus
from backend.models import DEFAULT_MODELS, provider_from_spec
from backend.prompts import ChallengeMeta
from backend.solver_base import (
    CANCELLED,
    ERROR,
    FLAG_FOUND,
    GAVE_UP,
    QUOTA_ERROR,
    SolverProtocol,
    SolverResult,
)

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)


# Quota fallback: DISABLED by default for competition safety.
# When running with a single GLM-5.1 model, there is no fallback.
# If you add multiple providers, map subscription-backed to API-backed equivalents:
# QUOTA_FALLBACK: dict[str, str] = {
#     "anthropic/GLM-5.1": "anthropic/GLM-5.1",  # self-fallback (no-op)
# }
QUOTA_FALLBACK: dict[str, str] = {}


def _quota_fallback_spec(model_spec: str) -> str | None:
    return QUOTA_FALLBACK.get(model_spec)


@dataclass
class ChallengeSwarm:
    """Parallel solvers racing on one challenge."""

    challenge_dir: str
    meta: ChallengeMeta
    ctfd: CTFdClient
    cost_tracker: CostTracker
    settings: Settings
    model_specs: list[str] = field(default_factory=lambda: list(DEFAULT_MODELS))
    no_submit: bool = False
    coordinator_inbox: asyncio.Queue | None = None

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    solvers: dict[str, SolverProtocol] = field(default_factory=dict)
    findings: dict[str, str] = field(default_factory=dict)
    winner: SolverResult | None = None
    confirmed_flag: str | None = None
    _flag_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _submit_count: dict[str, int] = field(default_factory=dict)  # per-model wrong submission count
    _submitted_flags: set[str] = field(default_factory=set)  # dedup exact flags
    _last_submit_time: dict[str, float] = field(default_factory=dict)  # per-model last submit timestamp
    message_bus: ChallengeMessageBus = field(default_factory=ChallengeMessageBus)

    def _create_solver(self, model_spec: str):
        """Create a solver based on provider.

        All providers now route through the Pydantic AI Solver.
        claude-sdk and codex backends are disabled for GLM-5.1 operation.
        """
        provider = provider_from_spec(model_spec)

        def _submit_fn(flag): return self.try_submit_flag(flag, model_spec)
        _notify = self._make_notify_fn(model_spec)

        # claude-sdk and codex are disabled — route everything through Pydantic AI
        if provider in ("claude-sdk", "codex"):
            logger.warning(
                f"Provider '{provider}' is disabled. "
                f"Routing {model_spec} through Pydantic AI Solver instead. "
                f"Use 'anthropic/' or 'bedrock/' or 'azure/' specs for model selection."
            )
            # Remap to anthropic provider with the same model ID
            from backend.models import model_id_from_spec
            model_id = model_id_from_spec(model_spec)
            remapped_spec = f"anthropic/{model_id}"
            return self._create_pydantic_solver(remapped_spec, submit_fn=_submit_fn, notify_fn=_notify)

        return self._create_pydantic_solver(model_spec, submit_fn=_submit_fn, notify_fn=_notify)

    def _make_notify_fn(self, model_spec: str):
        """Create a callback that pushes solver messages to the coordinator inbox."""
        async def _notify(message: str) -> None:
            if self.coordinator_inbox:
                self.coordinator_inbox.put_nowait(
                    f"[{self.meta.name}/{model_spec}] {message}"
                )
        return _notify

    def _create_pydantic_solver(
        self,
        model_spec: str,
        sandbox=None,
        owns_sandbox: bool | None = None,
        submit_fn=None,
        notify_fn=None,
    ) -> Solver:
        """Create a Pydantic AI solver. Pass sandbox to reuse an existing container (quota fallback)."""
        solver = Solver(
            model_spec=model_spec,
            challenge_dir=self.challenge_dir,
            meta=self.meta,
            ctfd=self.ctfd,
            cost_tracker=self.cost_tracker,
            settings=self.settings,
            cancel_event=self.cancel_event,
            sandbox=sandbox,
            owns_sandbox=owns_sandbox,
        )
        solver.deps.message_bus = self.message_bus
        solver.deps.model_spec = model_spec
        solver.deps.no_submit = self.no_submit
        solver.deps.submit_fn = submit_fn or (lambda flag: self.try_submit_flag(flag, model_spec))
        solver.deps.notify_coordinator = notify_fn or self._make_notify_fn(model_spec)
        return solver

    def _gather_sibling_insights(self, exclude_model: str) -> str:
        parts: list[str] = []
        for model, finding in self.findings.items():
            if model != exclude_model and finding:
                parts.append(f"[{model}]: {finding}")
        return "\n\n".join(parts) if parts else "No sibling insights available yet."

    # Escalating cooldowns after incorrect submissions (per model)
    SUBMISSION_COOLDOWNS = [0, 30, 120, 300, 600]  # 0s, 30s, 2min, 5min, 10min

    async def try_submit_flag(self, flag: str, model_spec: str) -> tuple[str, bool]:
        """Cooldown-gated, deduplicated flag submission. Returns (display, is_confirmed)."""
        async with self._flag_lock:
            normalized = flag.strip()

            if self.confirmed_flag:
                # Only the exact flag already confirmed by CTFd may be reported.
                # Any other submission must not inherit that confirmation.
                if normalized == self.confirmed_flag:
                    return f"ALREADY SOLVED — flag already confirmed: {self.confirmed_flag}", True
                return (
                    "ALREADY SOLVED — this challenge was solved with a different flag. "
                    "Your submitted flag is NOT confirmed; do not report it."
                ), False

            # Dedup exact flags across all models
            if normalized in self._submitted_flags:
                return "INCORRECT — already tried this exact flag.", False

            # Escalating cooldown after incorrect submissions
            wrong_count = self._submit_count.get(model_spec, 0)
            cooldown_idx = min(wrong_count, len(self.SUBMISSION_COOLDOWNS) - 1)
            cooldown = self.SUBMISSION_COOLDOWNS[cooldown_idx]
            if cooldown > 0:
                last_time = self._last_submit_time.get(model_spec, 0)
                elapsed = time.monotonic() - last_time
                if elapsed < cooldown:
                    remaining = int(cooldown - elapsed)
                    return (
                        f"COOLDOWN — wait {remaining}s before submitting again. "
                        f"You have {wrong_count} incorrect submissions. "
                        "Use this time to do deeper analysis and verify your flag.",
                        False,
                    )

            self._submitted_flags.add(normalized)

            from backend.tools.core import do_submit_flag
            display, is_confirmed = await do_submit_flag(self.ctfd, self.meta.name, flag)
            if is_confirmed:
                self.confirmed_flag = normalized
            else:
                self._submit_count[model_spec] = wrong_count + 1
                self._last_submit_time[model_spec] = time.monotonic()
            return display, is_confirmed

    async def _run_solver(self, model_spec: str) -> SolverResult | None:
        solver = self._create_solver(model_spec)
        self.solvers[model_spec] = solver

        try:
            result, final_solver = await self._run_solver_loop(solver, model_spec)
            solver = final_solver
            return result
        except Exception as e:
            logger.error(f"[{self.meta.name}/{model_spec}] Fatal: {e}", exc_info=True)
            return None
        finally:
            await solver.stop()

    async def _run_solver_loop(self, solver, model_spec: str) -> tuple[SolverResult, SolverProtocol]:
        """Inner loop: start → run → bump → run → ..."""
        bump_count = 0
        consecutive_errors = 0
        result = SolverResult(
            flag=None, status=CANCELLED, findings_summary="",
            step_count=0, cost_usd=0.0, log_path="",
        )
        await solver.start()

        # Budget limit per challenge
        max_cost = getattr(self.settings, "max_cost_per_challenge", 10.0)
        max_attempts = getattr(self.settings, "max_attempts_per_challenge", 10)

        while not self.cancel_event.is_set():
            # Check per-challenge and global budgets
            challenge_cost = self.cost_tracker.cost_for_agent_prefix(f"{self.meta.name}/")
            if challenge_cost >= max_cost:
                logger.warning(
                    f"[{self.meta.name}/{model_spec}] Challenge budget exhausted "
                    f"(${challenge_cost:.2f} >= ${max_cost:.2f})"
                )
                break
            max_total = getattr(self.settings, "max_cost_total", 0) or 0
            if max_total and self.cost_tracker.total_cost_usd >= max_total:
                logger.warning(
                    f"[{self.meta.name}/{model_spec}] Global budget exhausted "
                    f"(${self.cost_tracker.total_cost_usd:.2f} >= ${max_total:.2f})"
                )
                break

            result = await solver.run_until_done_or_gave_up()

            # Only broadcast useful findings — skip errors and broken solvers
            if (result.status not in (ERROR, QUOTA_ERROR)
                    and not (result.step_count == 0 and result.cost_usd == 0)
                    and result.findings_summary
                    and not result.findings_summary.startswith(("Error:", "Turn failed:"))):
                self.findings[model_spec] = result.findings_summary
                await self.message_bus.post(model_spec, result.findings_summary[:500])

            if result.status == FLAG_FOUND:
                self.cancel_event.set()
                self.winner = result
                logger.info(
                    f"[{self.meta.name}] Flag found by {model_spec}: {result.flag}"
                )
                return result, solver

            if result.status == CANCELLED:
                break

            # Quota exhaustion: fall back to API-backed Pydantic AI solver
            if result.status == QUOTA_ERROR:
                fallback_spec = _quota_fallback_spec(model_spec)
                if fallback_spec:
                    logger.warning(
                        f"[{self.meta.name}/{model_spec}] Quota exhausted — falling back to {fallback_spec}"
                    )
                    existing_sandbox = solver.sandbox
                    # Detach sandbox from old solver so stop() doesn't destroy it
                    solver.sandbox = None  # type: ignore[assignment]
                    await solver.stop()
                    solver = self._create_pydantic_solver(fallback_spec, sandbox=existing_sandbox, owns_sandbox=True)
                    self.solvers[model_spec] = solver
                    await solver.start()
                    continue
                # No fallback available, treat as error
                break

            if result.status in (GAVE_UP, ERROR):
                if result.step_count == 0 and result.cost_usd == 0:
                    logger.warning(
                        f"[{self.meta.name}/{model_spec}] Broken (0 steps, $0) — not bumping"
                    )
                    break

                # Track consecutive errors — stop after 3 in a row
                if result.status == ERROR:
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        logger.warning(
                            f"[{self.meta.name}/{model_spec}] {consecutive_errors} consecutive errors — giving up"
                        )
                        break
                else:
                    consecutive_errors = 0

                bump_count += 1

                # Check max attempts
                if bump_count >= max_attempts:
                    logger.warning(
                        f"[{self.meta.name}/{model_spec}] Max attempts ({max_attempts}) reached — giving up"
                    )
                    break

                # Cooldown between bumps — check cancellation during wait
                try:
                    await asyncio.wait_for(
                        self.cancel_event.wait(),
                        timeout=min(bump_count * 30, 300),
                    )
                    break  # cancelled during cooldown
                except TimeoutError:
                    pass  # cooldown elapsed, proceed with bump
                insights = self._gather_sibling_insights(model_spec)
                solver.bump(insights)
                logger.info(
                    f"[{self.meta.name}/{model_spec}] Bumped ({bump_count}), resuming"
                )
                continue

        return result, solver

    async def run(self) -> SolverResult | None:
        """Run all solvers in parallel. Returns the winner's result or None."""
        tasks = [
            asyncio.create_task(self._run_solver(spec), name=f"solver-{spec}")
            for spec in self.model_specs
        ]

        try:
            while tasks:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                for task in done:
                    try:
                        result = task.result()
                    except Exception:
                        continue
                    if result and result.status == FLAG_FOUND:
                        self.cancel_event.set()
                        for p in pending:
                            p.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        return result

                tasks = list(pending)

            self.cancel_event.set()
            return self.winner
        except Exception as e:
            logger.error(f"[{self.meta.name}] Swarm error: {e}", exc_info=True)
            self.cancel_event.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return None

    def kill(self) -> None:
        """Cancel all agents for this challenge."""
        self.cancel_event.set()

    def get_status(self) -> dict:
        """Get per-agent progress and findings."""
        return {
            "challenge": self.meta.name,
            "cancelled": self.cancel_event.is_set(),
            "winner": self.winner.flag if self.winner else None,
            "agents": {
                spec: {
                    "findings": self.findings.get(spec, ""),
                    "status": "running" if spec in self.solvers and not self.cancel_event.is_set()
                             else ("won" if self.winner and self.winner.flag else "finished"),
                }
                for spec in self.model_specs
            },
        }
