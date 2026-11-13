"""TPCTF 2026 player API. No CTFd response or authentication assumptions."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx


class CTFyError(RuntimeError):
    def __init__(self, status: int, code: str, retry_after: float = 5):
        # The response detail may contain credentials or reflected user data.
        super().__init__(f"CTFy HTTP {status}: {code}")
        self.status, self.code, self.retry_after = status, code, retry_after


class CTFyClient:
    def __init__(self, base_url: str, token: str, competition_id: str = "", *, transport=None):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url += "/api/v1"
        self.competition_id = competition_id
        self._client = httpx.AsyncClient(
            base_url=self.base_url + "/", transport=transport, timeout=45,
            headers={"Authorization": f"Bearer {token}"}, follow_redirects=False,
        )

    async def close(self):
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = urljoin(self.base_url + "/", path.lstrip("/")) if not path.startswith("http") else path
        if urlsplit(url).netloc != urlsplit(self.base_url).netloc or urlsplit(url).scheme != urlsplit(self.base_url).scheme:
            raise ValueError("Refusing to send platform credentials to another origin")
        for attempt in range(3):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if method not in {"GET", "DELETE"} or attempt == 2:
                    raise CTFyError(0, "transport_error") from None
                await asyncio.sleep(2 ** attempt)
                continue
            if response.is_success:
                return response
            try:
                body = response.json()
            except ValueError:
                body = {}
            code = body.get("code", f"http_{response.status_code}")
            detail = body.get("detail")
            delay = response.headers.get("Retry-After", "5")
            if isinstance(detail, dict):
                delay = detail.get("retry_after_s", delay)
            try:
                delay = max(1, float(delay))
            except (ValueError, TypeError):
                delay = 5
            if method in {"GET", "DELETE"} and response.status_code in {429, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(min(delay, 60))
                continue
            raise CTFyError(response.status_code, code, delay)
        raise AssertionError("unreachable")

    async def json(self, method: str, path: str, **kwargs) -> Any:
        return (await self.request(method, path, **kwargs)).json()

    async def pages(self, path: str, **params) -> list[dict]:
        items, offset = [], 0
        while True:
            page = await self.json("GET", path, params={**params, "limit": 100, "offset": offset})
            batch = page["items"]
            items.extend(batch)
            offset += len(batch)
            if offset >= page["total"]:
                return items
            if not batch:
                raise CTFyError(200, "pagination_stalled")

    async def competitions(self):
        return await self.pages("competitions")

    async def competition(self):
        return await self.json("GET", f"competitions/{quote(self.competition_id, safe='')}")

    async def challenges(self):
        return await self.pages(f"competitions/{quote(self.competition_id, safe='')}/challenges")

    async def challenge(self, challenge_id: str):
        return await self.json("GET", f"challenges/{quote(challenge_id, safe='')}", params=self.scope)

    @property
    def scope(self):
        return {"competition_id": self.competition_id}

    async def instances(self):
        return await self.pages("instances", **self.scope)

    async def instance(self, instance_id: str):
        return await self.json("GET", f"instances/{quote(instance_id, safe='')}", params=self.scope)

    async def start(self, challenge_id: str):
        try:
            return await self.json("POST", "instances", json={"challenge_id": challenge_id, **self.scope})
        except CTFyError as error:
            if error.code not in {"instance_already_running", "transport_error"}:
                raise
            # A lost POST response must not create a second instance. Reconcile first.
            existing = [i for i in await self.instances() if i["challenge_id"] == challenge_id
                        and i["status"] in {"starting", "ready"}]
            if len(existing) == 1:
                return existing[0]
            raise error

    async def wait_ready(self, instance_id: str, timeout: float = 600):
        async with asyncio.timeout(timeout):
            while True:
                info = await self.instance(instance_id)
                if info["status"] == "ready":
                    return info
                if info["status"] not in {"starting"}:
                    raise CTFyError(409, "instance_" + info["status"])
                status_response = await self.request("GET", f"instances/{quote(instance_id, safe='')}/status", params=self.scope)
                status = status_response.json()
                await asyncio.sleep(max(1, float(status.get("poll_after_s") or status_response.headers.get("Retry-After", 5))))

    async def stop(self, instance_id: str):
        try:
            return await self.json("DELETE", f"instances/{quote(instance_id, safe='')}", params=self.scope)
        except CTFyError as error:
            if error.code != "instance_not_found":
                raise
            return {"id": instance_id, "status": "stopped"}

    async def submit(self, instance_id: str, question_id: str, answer: str | list[str]):
        # No automatic POST retries: wrong submissions consume a finite attempt budget.
        return await self.json("POST", "submissions", json={
            "instance_id": instance_id, "question_id": question_id, "answer": answer,
        })

    async def vpn_config(self, instance_id: str):
        # Use the documented same-origin route, not a URL supplied by a target.
        return (await self.request("GET", f"instances/{quote(instance_id, safe='')}/vpn-config", params=self.scope)).text

    async def attachments(self, instance_id: str, output_dir: Path):
        listing = await self.json("GET", f"instances/{quote(instance_id, safe='')}/attachments", params=self.scope)
        output_dir.mkdir(parents=True, exist_ok=True)
        for item in listing["files"]:
            name = item["name"]
            if not name or name in {".", ".."} or "/" in name or "\\" in name or ":" in name:
                raise ValueError("Unsafe attachment filename")
            response = await self.request("GET", f"instances/{quote(instance_id, safe='')}/attachments/{quote(name, safe='')}", params=self.scope)
            (output_dir / name).write_bytes(response.content)


class QuestionSubmission:
    """One instance/question's verdicts. A transport error is never a wrong answer."""
    def __init__(self, client: CTFyClient, instance_id: str, question_id: str, journal: Path):
        self.client, self.instance_id, self.question_id = client, instance_id, question_id
        self.journal = journal
        self.confirmed = None
        self.verdicts = {}
        self.exhausted = False
        self.lock = asyncio.Lock()
        self.last_receipt = None

    async def __call__(self, answer: str | list[str]):
        async with self.lock:
            if isinstance(answer, str):
                answer = answer.strip()
                key = answer
            elif isinstance(answer, list) and all(isinstance(item, str) for item in answer):
                key = json.dumps(sorted(answer), ensure_ascii=False)
            else:
                return "Answer must be a string or list of strings", False
            if not answer:
                return "Empty answer", False
            if self.exhausted:
                return "STOP — question attempt cap reached", False
            if key in self.verdicts:
                return self.verdicts[key]
            if self.confirmed is not None:
                return "Question already confirmed; do not submit another answer", False
            try:
                receipt = await self.client.submit(self.instance_id, self.question_id, answer)
            except CTFyError as error:
                self.exhausted = error.code == "question_attempt_cap_exceeded"
                return f"Submission not confirmed: {error.code}; refresh state before retrying", False
            self.last_receipt = receipt
            correct = receipt.get("correct") is True
            self.exhausted = not correct and receipt.get("attempts_remaining") == 0
            if correct:
                self.confirmed = key
            prefix = "CORRECT —" if correct else "INCORRECT —"
            result = (prefix + json.dumps(receipt, ensure_ascii=False), correct)
            self.verdicts[key] = result
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"instance_id": self.instance_id, "question_id": self.question_id,
                                         "answer": answer, "receipt": receipt}, ensure_ascii=False) + "\n")
            return result
