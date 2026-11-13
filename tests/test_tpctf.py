import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.config import Settings
from backend.platform.ctfy import CTFyClient, QuestionSubmission
from backend.tpctf import TPCTFRunner, concurrency_limit, eligible, model_spec
from backend.tpctf_network import WireGuardManager, connection_info, routes_in_config


def settings(tmp_path, **overrides):
    return Settings(_env_file=None, ctfy_token="pf_test", ctfy_competition_id="comp",
                    anthropic_api_key="test", anthropic_base_url="https://model.invalid",
                    tpctf_runtime=str(tmp_path), tpctf_poll_seconds=0.01, **overrides)


@pytest.mark.asyncio
async def test_client_scoping_pagination_and_auth():
    seen = []
    async def handle(req):
        seen.append(req)
        assert req.headers["authorization"] == "Bearer pf_test"
        offset = int(req.url.params["offset"])
        assert req.url.path == "/api/v1/competitions/comp/challenges"
        return httpx.Response(200, json={"items": [{"id": str(offset)}], "total": 2})
    client = CTFyClient("https://platform.invalid", "pf_test", "comp", transport=httpx.MockTransport(handle))
    try:
        assert await client.challenges() == [{"id": "0"}, {"id": "1"}]
        with pytest.raises(ValueError):
            await client.request("GET", "https://other.invalid/steal")
        assert len(seen) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_does_not_poison_answer_and_wrong_answer_is_deduped(tmp_path):
    calls = []
    async def handle(req):
        data = json.loads(req.content)
        assert data["instance_id"] == "mine" and data["question_id"] == "second"
        calls.append(data)
        if len(calls) == 1:
            raise httpx.ReadTimeout("mock timeout")
        return httpx.Response(200, json={"correct": data["answer"] == "real",
            "ranked": False, "unranked_reason": "practice", "challenge_fully_solved": False})
    client = CTFyClient("https://platform.invalid", "pf_test", "comp", transport=httpx.MockTransport(handle))
    submit = QuestionSubmission(client, "mine", "second", tmp_path / "receipts.jsonl")
    try:
        assert not (await submit("wrong"))[1]
        assert not (await submit("wrong"))[1]
        assert not (await submit("wrong"))[1]
        assert len(calls) == 2
        assert (await submit("real"))[1]
        assert submit.last_receipt["ranked"] is False
        assert submit.last_receipt["challenge_fully_solved"] is False
        assert len((tmp_path / "receipts.jsonl").read_text().splitlines()) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_attempt_cap_blocks_further_submissions(tmp_path):
    calls = []
    async def handle(req):
        calls.append(req)
        return httpx.Response(200, json={"correct": False, "attempts_remaining": 0})
    client = CTFyClient("https://platform.invalid", "pf_test", "comp", transport=httpx.MockTransport(handle))
    try:
        submit = QuestionSubmission(client, "mine", "flag", tmp_path / "log")
        await submit("a")
        await submit("b")
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_multiselect_preserves_array_and_dedupes_reordered_choices(tmp_path):
    answers = []
    async def handle(req):
        answers.append(json.loads(req.content)["answer"])
        return httpx.Response(200, json={"correct": True})
    client = CTFyClient("https://platform.invalid", "pf_test", "comp", transport=httpx.MockTransport(handle))
    try:
        submit = QuestionSubmission(client, "mine", "choices", tmp_path / "receipts")
        assert (await submit(["A", "C"]))[1]
        assert (await submit(["C", "A"]))[1]
        assert answers == [["A", "C"]]
    finally:
        await client.close()


def test_stage_model_and_team_limits(tmp_path):
    s = settings(tmp_path)
    assert model_spec(s) == "anthropic/GLM-5.1"
    s.tpctf_concurrency = 10
    assert concurrency_limit(s, {"max_instances_per_team_effective": 3}) == 1
    s.tpctf_stage = "preliminary"
    with pytest.raises(ValueError, match="GATEWAY_HOST"):
        model_spec(s)
    s.tpctf_gateway_host = "model.invalid"
    with pytest.raises(ValueError, match="pk_"):
        model_spec(s)
    s.anthropic_api_key = "pk_mock"
    assert model_spec(s) == "anthropic/GLM-5.1"
    assert concurrency_limit(s, {"max_instances_per_team_effective": 2}) == 2
    s.tpctf_model_protocol = "openai"
    s.openai_base_url = "https://model.invalid/v1"
    s.openai_api_key = "pk_mock"
    assert model_spec(s) == "openai/GLM-5.1"
    assert "pf_test" not in repr(s) and "pk_mock" not in repr(s)


def test_practice_requires_explicit_target_and_own_challenges_are_excluded():
    assert not eligible({"scoring": "practice"})
    assert eligible({"scoring": "practice"}, allow_practice=True)
    assert not eligible({"scoring": "scored", "my_team_authored": True}, allow_practice=True)


def test_official_tcp_access_is_an_enum_not_a_mapping():
    info = {"services": [{"service_type": "tcp", "host": "target.invalid", "port": 443, "access": "tls_sni"}]}
    text = connection_info(info)
    assert "use_tls=True" in text and "server_hostname='target.invalid'" in text
    info["services"][0]["access"] = "vpn"
    assert connection_info(info) == "nc target.invalid 443"


@pytest.mark.asyncio
async def test_published_tls_endpoint_uses_sni(monkeypatch):
    from backend.interactive.tcp_session import TcpSession
    seen = []
    async def connect(host, port, **kwargs):
        seen.append((host, port, kwargs))
        return None, SimpleNamespace(is_closing=lambda: False)
    monkeypatch.setattr(asyncio, "open_connection", connect)
    session = TcpSession("tls")
    assert "Connected" in await session.connect("target.invalid", 443, use_tls=True, server_hostname="target.invalid")
    assert seen == [("target.invalid", 443, {"ssl": True, "server_hostname": "target.invalid"})]


class Contest:
    def __init__(self, preexisting=False):
        self.active = {"i1": "one"} if preexisting else {}
        self.solved = {}
        self.calls = []
        self.peak = 0
        self.stops = []

    def info(self, iid):
        cid = self.active[iid]
        solved = self.solved.get(cid, set())
        ids = ["first", "second"] if "first" in solved else ["first"]
        return {"id": iid, "challenge_id": cid, "competition_id": "comp", "team_id": "team",
            "status": "ready", "expires_at": time.time() + 300,
            "services": [{"service_type": "tcp", "host": "10.1.0.2", "port": 31337}],
            "questions": [{"id": q, "prompt": q, "unlocked": True, "answered_correctly": q in solved,
                           "attempts_remaining": 3} for q in ids]}

    def challenge(self, cid):
        return {"id": cid, "name": "Same title", "description": "mock target", "category": "web",
                "my_team_fully_solved": self.solved.get(cid) == {"first", "second"}}

    async def handle(self, req):
        p, method = req.url.path.removeprefix("/api/v1/"), req.method
        self.calls.append((method, p))
        if p == "competitions/comp":
            data = {"id": "comp", "my_team_id": "team", "max_instances_per_team_effective": 3}
        elif p == "competitions/comp/challenges":
            data = {"items": [self.challenge(c) for c in ["one", "two"]], "total": 2}
        elif p.startswith("challenges/"):
            data = self.challenge(p.split("/")[1])
        elif p == "instances" and method == "POST":
            cid = json.loads(req.content)["challenge_id"]
            iid = "new" + str(len([c for c in self.calls if c == ("POST", "instances")]))
            self.active[iid] = cid
            self.peak = max(self.peak, len(self.active))
            data = {"id": iid, "status": "starting"}
        elif p == "instances":
            data = {"items": [self.info(i) for i in self.active], "total": len(self.active)}
        elif p.endswith("/vpn-config"):
            return httpx.Response(200, text="[Interface]\n[Peer]\nAllowedIPs = 10.1.0.0/24\n")
        elif p.endswith("/attachments"):
            data = {"files": [{"name": "challenge.txt", "size": 20, "per_team": True}]}
        elif p.endswith("/attachments/challenge.txt"):
            return httpx.Response(200, content=b"this team's own attachment")
        elif p.startswith("instances/"):
            iid = p.split("/")[1]
            if method == "DELETE":
                self.active.pop(iid, None)
                self.stops.append(iid)
                data = {"id": iid, "status": "stopped"}
            elif iid not in self.active:
                return httpx.Response(404, json={"code": "instance_not_found"})
            else:
                data = self.info(iid)
        elif p == "submissions":
            body = json.loads(req.content)
            cid = self.active[body["instance_id"]]
            qid = body["question_id"]
            assert qid == "first" or "first" in self.solved.get(cid, set())
            assert body["answer"] == "real-" + qid
            self.solved.setdefault(cid, set()).add(qid)
            data = {"correct": True, "question_id": qid, "ranked": True,
                    "challenge_fully_solved": qid == "second", "solve_rank": 1}
        else:
            raise AssertionError((method, p))
        return httpx.Response(200, json=data)


class FakeSandbox:
    def __init__(self, directory):
        self.directory = directory
        self.workspace_dir = str(directory)
        self._container = None
    async def start(self):
        self._container = True
    async def stop(self):
        self._container = None
    async def exec(self, command, timeout_s=60):
        return SimpleNamespace(exit_code=0, stdout="x86_64" if command == "uname -m" else "mock challenge data", stderr="")


class FakeSolver:
    def __init__(self, **kwargs):
        self.deps = SimpleNamespace(submit_fn=None)
        self.qid = kwargs["meta"].name.split("-")[-1]
    async def run_until_done_or_gave_up(self):
        display, ok = await self.deps.submit_fn("real-" + self.qid)
        assert ok, display
        return SimpleNamespace(status="flag_found")
    async def stop(self):
        pass


class FakeVPN:
    def __init__(self):
        self.active = set()
    async def connect(self, iid, config):
        self.active.add(iid)
    async def disconnect(self, iid):
        self.active.discard(iid)


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_full_instance_lifecycle_multistep_and_restart(tmp_path, recover):
    contest = Contest(preexisting=recover)
    if recover:
        (tmp_path / "state.json").write_text(json.dumps({"competition_id": "comp", "instances": {"i1": {"challenge_id": "one"}}}))
    client = CTFyClient("https://platform.invalid", "pf_test", "comp", transport=httpx.MockTransport(contest.handle))
    probes = []
    async def probe(info, sandbox=None):
        probes.append("sandbox" if sandbox else "agent")
    vpn = FakeVPN()
    try:
        runner = TPCTFRunner(client, settings(tmp_path), solver_factory=FakeSolver,
                             sandbox_factory=FakeSandbox, vpn=vpn, probe=probe)
        await asyncio.wait_for(runner.run(once=True), 5)
        assert contest.solved == {"one": {"first", "second"}, "two": {"first", "second"}}
        assert contest.peak <= 1
        assert not contest.active and not vpn.active and not runner.owned
        assert probes == ["agent", "sandbox", "agent", "sandbox"]
        assert len(list(tmp_path.rglob("submissions.jsonl"))) == 2
        assert len([c for c in contest.calls if c == ("POST", "instances")]) == (1 if recover else 2)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_wireguard_overlap_hooks_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tpctf_network.platform.system", lambda: "Linux")
    calls = []
    async def command(*args):
        calls.append(args[:2])
    vpn = WireGuardManager(tmp_path, command=command)
    config = "[Interface]\nPrivateKey = mock\n[Peer]\nAllowedIPs = 10.1.0.0/24\n"
    await vpn.connect("one", config)
    with pytest.raises(RuntimeError, match="overlap"):
        await vpn.connect("two", config)
    await vpn.disconnect("one")
    assert calls == [("wg-quick", "up"), ("wg-quick", "down")]
    assert not list(tmp_path.glob("*.conf"))
    for bad in [config + "PostUp = echo bad", config.replace("10.1.0.0/24", "0.0.0.0/0")]:
        with pytest.raises(ValueError):
            routes_in_config(bad)


@pytest.mark.asyncio
async def test_real_solver_tools_complete_two_gated_questions(tmp_path, monkeypatch):
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    from backend.agents.solver import Solver

    calls = []
    async def model(messages, info):
        qid = "second" if "CURRENT QUESTION:\n{\"id\": \"second\"" in str(messages) else "first"
        # Parse the current question from the actual system prompt, without seeing the mock server state.
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    content = getattr(part, "content", "")
                    if isinstance(content, str) and "CURRENT QUESTION:\n" in content:
                        qid = json.JSONDecoder().raw_decode(content.split("CURRENT QUESTION:\n", 1)[1])[0]["id"]
        returns = [part for message in messages if isinstance(message, ModelRequest)
                   for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            calls.append((qid, "bash"))
            return ModelResponse(parts=[ToolCallPart("bash", {"command": "cat /challenge/distfiles/challenge.txt"})])
        if returns[-1].tool_name == "bash":
            calls.append((qid, "submit_flag"))
            return ModelResponse(parts=[ToolCallPart("submit_flag", {"flag": "real-" + qid})])
        assert "CORRECT" in str(returns[-1].content)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                {"flag": "real-" + qid, "method": "scripted offline model"})])

    monkeypatch.setattr("backend.agents.solver.resolve_model", lambda *args: FunctionModel(model))
    contest = Contest()
    client = CTFyClient("https://platform.invalid", "pf_test", "comp", transport=httpx.MockTransport(contest.handle))
    async def probe(*args):
        pass
    try:
        s = settings(tmp_path, tpctf_competition_mode=True)
        runner = TPCTFRunner(client, s, solver_factory=Solver, sandbox_factory=FakeSandbox, vpn=FakeVPN(), probe=probe)
        await asyncio.wait_for(runner.run(once=True, challenge_id="one"), 15)
        assert contest.solved == {"one": {"first", "second"}}
        assert calls == [("first", "bash"), ("first", "submit_flag"), ("second", "bash"), ("second", "submit_flag")]
        assert not contest.active
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancel_releases_owned_target(tmp_path):
    contest = Contest()
    client = CTFyClient("https://platform.invalid", "pf_test", "comp", transport=httpx.MockTransport(contest.handle))
    entered = asyncio.Event()
    class SlowSolver(FakeSolver):
        async def run_until_done_or_gave_up(self):
            entered.set()
            await asyncio.Event().wait()
    async def probe(*args):
        pass
    try:
        runner = TPCTFRunner(client, settings(tmp_path), solver_factory=SlowSolver,
                             sandbox_factory=FakeSandbox, vpn=FakeVPN(), probe=probe)
        task = asyncio.create_task(runner.run(once=True))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not contest.active and not runner.owned
    finally:
        await client.close()
