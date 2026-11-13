"""TPCTF player coordinator: bounded instance scheduling, one solver per question."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

import click

from backend.config import Settings
from backend.cost_tracker import CostTracker, configure_pricing_from_settings
from backend.platform.ctfy import CTFyClient, CTFyError, QuestionSubmission
from backend.prompts import ChallengeMeta
from backend.sandbox import DockerSandbox, configure_path_mapping
from backend.tpctf_network import WireGuardManager, connection_info, probe_services

logger = logging.getLogger(__name__)


def model_spec(settings: Settings) -> str:
    if settings.tpctf_stage not in {"qualifier", "preliminary", "final"}:
        raise ValueError("TPCTF_STAGE must be qualifier, preliminary or final")
    protocol = settings.tpctf_model_protocol
    if protocol not in {"anthropic", "openai"}:
        raise ValueError("TPCTF_MODEL_PROTOCOL must be anthropic or openai")
    base = getattr(settings, protocol + "_base_url")
    key = getattr(settings, protocol + "_api_key")
    if not base or not key:
        raise ValueError(f"{protocol.upper()}_BASE_URL and {protocol.upper()}_API_KEY are required")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Model endpoint must be an HTTPS URL without embedded credentials")
    if settings.tpctf_stage != "qualifier":
        # Explicitly pin the host printed on the platform's local Agent session page.
        # Never infer a gateway URL, or silently fall back to a provider endpoint.
        if not settings.tpctf_gateway_host or parsed.hostname != settings.tpctf_gateway_host:
            raise ValueError("Set TPCTF_GATEWAY_HOST to the platform session's exact hostname")
        if not key.startswith("pk_"):
            raise ValueError("Preliminary/final require the platform's pk_ session credential")
    if settings.tpctf_stage == "final" and not settings.model_name.upper().startswith("GLM"):
        raise ValueError("Final stage requires the organizer's GLM model")
    return f"{protocol}/{settings.model_name}"


def concurrency_limit(settings: Settings, competition: dict):
    stage_cap = 1 if settings.tpctf_stage == "qualifier" else 3
    if settings.tpctf_concurrency < 1:
        raise ValueError("TPCTF_CONCURRENCY must be positive")
    configured = competition.get("max_instances_per_team_effective")
    if not isinstance(configured, int) or configured < 1:
        raise ValueError("Competition did not publish a positive team instance limit")
    return min(settings.tpctf_concurrency, stage_cap, configured)


def eligible(challenge: dict, allow_practice: bool = False):
    return not (challenge.get("my_team_fully_solved") or challenge.get("my_team_authored")
                or challenge.get("closed") or challenge.get("scoring", "scored") not in
                ({"scored", "practice"} if allow_practice else {"scored"}))


class TPCTFRunner:
    def __init__(self, client, settings, *, solver_factory=None, sandbox_factory=None, vpn=None, probe=None):
        self.client, self.settings = client, settings
        self.spec = model_spec(settings)
        self.root = Path(settings.tpctf_runtime)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.root.chmod(0o700)
        self.state_path = self.root / "state.json"
        self.owned = {}
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("competition_id") != client.competition_id:
                raise ValueError("Runtime directory belongs to a different competition")
            self.owned = data.get("instances", {})
        self.vpn = vpn or WireGuardManager(self.root / "vpn", settings.tpctf_vpn_mode)
        self.solver_factory = solver_factory
        self.sandbox_factory = sandbox_factory or self._sandbox
        self.probe = probe or probe_services
        self.tracker = CostTracker()
        self.tasks = {}
        self.attempted = set()
        self.save()

    def save(self):
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({"competition_id": self.client.competition_id,
                                    "instances": self.owned}, indent=2), encoding="utf-8")
        temp.replace(self.state_path)

    def _sandbox(self, directory):
        return DockerSandbox(
            image=self.settings.sandbox_image, challenge_dir=str(directory),
            memory_limit=self.settings.container_memory_limit, cpus=self.settings.container_cpus,
            network_mode="host", preserve_workspace=True, extra_capabilities=False,
        )

    async def release(self, instance_id):
        # Retain ownership until the platform confirms reclamation; 202 != released.
        await self.client.stop(instance_id)
        try:
            async with asyncio.timeout(90):
                while True:
                    try:
                        current = await self.client.instance(instance_id)
                    except CTFyError as error:
                        if error.code != "instance_not_found":
                            raise
                        break
                    if current["status"] == "stopped":
                        break
                    await asyncio.sleep(3)
        finally:
            await self.vpn.disconnect(instance_id)
        self.owned.pop(instance_id, None)
        self.save()

    async def solve_question(self, directory, challenge, info, question, sandbox):
        factory = self.solver_factory
        if factory is None:
            from backend.agents.solver import Solver
            factory = Solver
        qid = question["id"]
        submit = QuestionSubmission(self.client, info["id"], qid, directory / "submissions.jsonl")
        description = challenge.get("description", "") + "\n\nCURRENT QUESTION:\n" + json.dumps(question, ensure_ascii=False)
        description += "\nSubmit answers for this question only. Another solver handles later questions."
        # Name is for tracing only: API calls always use stable ids, not challenge titles.
        meta = ChallengeMeta(name=f"{challenge['id']}-{qid}", category=challenge.get("category", ""),
                             description=description, connection_info=connection_info(info))
        solver = factory(model_spec=self.spec, challenge_dir=str(directory), meta=meta,
                         ctfd=None, cost_tracker=self.tracker, settings=self.settings,
                         sandbox=sandbox, owns_sandbox=False)
        solver.deps.submit_fn = submit
        try:
            for _ in range(self.settings.tpctf_question_attempts):
                result = await solver.run_until_done_or_gave_up()
                if submit.confirmed or submit.exhausted:
                    break
                if getattr(result, "status", None) == "cancelled":
                    break
                solver.bump("No platform-confirmed answer yet. Review your evidence and try a different approach.")
        finally:
            await solver.stop()

    async def play(self, challenge_id, instance_id=None):
        directory = None
        sandbox = None
        phase = "instance_start"
        try:
            async with asyncio.timeout(self.settings.tpctf_challenge_seconds):
                if not instance_id:
                    started = await self.client.start(challenge_id)
                    instance_id = started["id"]
                    self.owned[instance_id] = {"challenge_id": challenge_id}
                    self.save()
                phase = "instance_ready"
                info = await self.client.wait_ready(instance_id)
                # Each instance has different flags and rendered attachments.
                safe_id = hashlib.sha256(instance_id.encode()).hexdigest()[:20]
                directory = self.root / "instances" / safe_id
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "instance.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
                phase = "wireguard"
                config = "" if self.settings.tpctf_vpn_mode == "external" else await self.client.vpn_config(instance_id)
                await self.vpn.connect(instance_id, config)
                phase = "agent_network"
                await self.probe(info)
                phase = "attachments"
                await self.client.attachments(instance_id, directory / "distfiles")
                (directory / "metadata.yml").write_text("name: TPCTF\n", encoding="utf-8")
                sandbox = self.sandbox_factory(directory)
                phase = "sandbox_start"
                await sandbox.start()
                phase = "sandbox_network"
                await self.probe(info, sandbox)
                phase = "solve_questions"
                attempted_questions = set()
                while True:
                    # Refresh after EVERY question, including a timeout after a successful tool call.
                    challenge = await self.client.challenge(challenge_id)
                    info = await self.client.instance(instance_id)
                    if challenge.get("my_team_fully_solved"):
                        logger.info("Challenge fully solved: %s", challenge_id)
                        return
                    if info["status"] != "ready" or info.get("expires_at", time.time() + 60) <= time.time() + 10:
                        return
                    pending = [q for q in info.get("questions", []) if q.get("unlocked", True)
                               and not q.get("answered_correctly") and q.get("attempts_remaining") != 0
                               and q["id"] not in attempted_questions]
                    if not pending:
                        return
                    question = pending[0]
                    attempted_questions.add(question["id"])
                    try:
                        seconds = min(self.settings.tpctf_question_seconds,
                                      info.get("expires_at", time.time() + self.settings.tpctf_question_seconds) - time.time() - 5)
                        async with asyncio.timeout(max(1, seconds)):
                            await self.solve_question(directory, challenge, info, question, sandbox)
                    except TimeoutError:
                        logger.warning("Question time budget exhausted: %s/%s", challenge_id, question["id"])
        except (CTFyError, RuntimeError, TimeoutError, OSError, ValueError) as error:
            logger.warning("Challenge %s stopped during %s: %s", challenge_id, phase, type(error).__name__ if not isinstance(error, CTFyError) else error.code)
            if directory:
                (directory / "failure.json").write_text(json.dumps({"type": type(error).__name__, "phase": phase,
                    "code": error.code if isinstance(error, CTFyError) else "local_setup_or_timeout"}), encoding="utf-8")
        finally:
            try:
                if sandbox:
                    await sandbox.stop()
            finally:
                if instance_id:
                    try:
                        await self.release(instance_id)
                    except Exception as error:
                        logger.error("Instance cleanup pending: %s (%s); ownership saved for restart", instance_id, type(error).__name__)

    async def run(self, once=False, challenge_id=None):
        comp = await self.client.competition()
        limit = concurrency_limit(self.settings, comp)
        team_id = comp.get("my_team_id")
        # Reconcile crashes before scheduling new instances; never stop unrelated team instances.
        for iid, saved in list(self.owned.items()):
            try:
                info = await self.client.instance(iid)
            except CTFyError as error:
                if error.code == "instance_not_found":
                    self.owned.pop(iid)
                    self.save()
                    continue
                raise
            if info.get("competition_id") != self.client.competition_id or (team_id and info.get("team_id") != team_id):
                raise ValueError("Saved instance ownership does not match this team/competition")
            if info.get("challenge_id") != saved["challenge_id"]:
                raise ValueError("Saved instance challenge does not match the platform record")
            if info["status"] in {"starting", "ready"}:
                # Resume rather than destroy an already-running target and its dynamic flag.
                cid = saved["challenge_id"]
                self.tasks[cid] = asyncio.create_task(self.play(cid, iid))
                self.attempted.add(cid)
            else:
                await self.release(iid)
        try:
            while True:
                challenges = await self.client.challenges()
                remote = await self.client.instances()
                # Capacity is TEAM-wide, including manual starts and other members' Agents.
                remote_active = [i for i in remote if i["status"] in {"starting", "ready", "stopping"}
                                 and (not team_id or i.get("team_id") == team_id)]
                slots = max(0, min(limit - len(self.tasks), limit - len(remote_active)))
                candidates = [c for c in challenges if eligible(c, allow_practice=bool(challenge_id)) and c["id"] not in self.tasks
                              and c["id"] not in self.attempted and (not challenge_id or c["id"] == challenge_id)]
                # First prefer more frequently solved tasks; the model works inside each selected task.
                candidates.sort(key=lambda c: (-c.get("solves_count", 0), c["id"]))
                for challenge in candidates[:slots]:
                    cid = challenge["id"]
                    self.attempted.add(cid)
                    self.tasks[cid] = asyncio.create_task(self.play(cid))
                if not self.tasks:
                    if once:
                        return
                    self.attempted.clear()  # A later pass may retry failed tasks, after the poll interval.
                    await asyncio.sleep(self.settings.tpctf_poll_seconds)
                    continue
                done, _ = await asyncio.wait(self.tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                for cid, task in list(self.tasks.items()):
                    if task in done:
                        del self.tasks[cid]
                        task.result()
                if once and not self.tasks and not [c for c in candidates if c["id"] not in self.attempted]:
                    return
        finally:
            for task in self.tasks.values():
                task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            for iid in list(self.owned):
                try:
                    await self.release(iid)
                except Exception:
                    logger.error("Retained unreleased instance in state: %s", iid)


def local_check(settings):
    spec = model_spec(settings)
    if not settings.ctfy_token or not settings.ctfy_competition_id:
        raise ValueError("CTFY_TOKEN and CTFY_COMPETITION_ID are required")
    if settings.tpctf_vpn_mode not in {"auto", "external"}:
        raise ValueError("TPCTF_VPN_MODE must be auto or external")
    if platform.system() != "Linux":
        raise ValueError("The TPCTF runner needs Linux host networking (use the Linux VPS)")
    if settings.tpctf_vpn_mode == "auto" and (not shutil.which("wg-quick") or os.geteuid() != 0):
        raise ValueError("Auto VPN needs wg-quick and root; external mode requires a pre-established VPN")
    if not shutil.which("docker"):
        raise ValueError("Install Docker and load the prebuilt sandbox image before network freeze")
    if min(settings.tpctf_question_seconds, settings.tpctf_challenge_seconds,
           settings.tpctf_question_attempts, settings.solver_request_limit, settings.solver_token_limit) < 1:
        raise ValueError("Time, attempts and model request/token budgets must be positive")
    return spec


@click.command()
@click.option("--stage", type=click.Choice(["qualifier", "preliminary", "final"]), default=None)
@click.option("--check", is_flag=True, help="Local configuration/dependency check; no platform writes or model calls")
@click.option("--list-competitions", is_flag=True, help="Read accessible competition IDs; no model calls")
@click.option("--once", is_flag=True, help="Try the currently available unsolved tasks once, then exit")
@click.option("--challenge", default=None, help="Restrict work to one challenge ID")
def main(stage, check, list_competitions, once, challenge):
    """Run the TPCTF player Agent on the assigned Linux VPS."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for name in ("httpx", "httpcore", "anthropic", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    settings = Settings()
    if stage:
        settings.tpctf_stage = stage
    settings.tpctf_competition_mode = True
    settings.sandbox_network_mode = "host"
    settings.preserve_workspaces = True
    settings.sandbox_extra_capabilities = False

    async def run():
        client = CTFyClient(settings.ctfy_url, settings.ctfy_token, settings.ctfy_competition_id)
        try:
            if list_competitions:
                if not settings.ctfy_token:
                    raise ValueError("CTFY_TOKEN is required")
                click.echo(json.dumps([{k: c.get(k) for k in ("id", "title", "is_registered", "my_team_name",
                                      "max_instances_per_team_effective")} for c in await client.competitions()], ensure_ascii=False, indent=2))
                return
            spec = local_check(settings)
            if check:
                # Read-only Docker checks use the actual daemon and preloaded image.
                proc = await asyncio.create_subprocess_exec("docker", "image", "inspect", settings.sandbox_image,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                if await proc.wait():
                    raise ValueError("Docker daemon or sandbox image unavailable")
                click.echo(f"Local preflight passed: stage={settings.tpctf_stage}, model={spec}. VPN and scoring still need a live instance.")
                return
            configure_pricing_from_settings(settings)
            if settings.workspace_root:
                configure_path_mapping(settings.workspace_root, settings.sandbox_bind_root,
                                       str(Path(settings.workspace_root) / "runtime" / "workspaces"))
            await TPCTFRunner(client, settings).run(once=once, challenge_id=challenge)
        finally:
            await client.close()
    try:
        asyncio.run(run())
    except (ValueError, CTFyError) as error:
        raise click.ClickException(str(error)) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
