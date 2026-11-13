import httpx
import pytest

from backend.interactive.http_session import HttpSession


@pytest.mark.asyncio
async def test_diff_reports_changed_body():
    replies = iter(["denied", "accepted"])
    session = HttpSession("test")
    session._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=next(replies))))
    try:
        await session.request("GET", "https://mock.invalid/")
        await session.request("GET", "https://mock.invalid/")
        diff = await session.diff_last_two()
        assert "-denied" in diff and "+accepted" in diff
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(307, "POST"), (308, "POST"), (303, "GET"), (302, "GET")])
async def test_redirect_method_and_body(status, expected):
    calls = []
    def handler(req):
        calls.append((req.method, req.content))
        return httpx.Response(status, headers={"location": "/next"}) if len(calls) == 1 else httpx.Response(200)
    session = HttpSession("redirect")
    session._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await session.request("POST", "https://mock.invalid/", raw_body="payload", follow_redirects=True)
        assert calls[-1] == (expected, b"payload" if expected == "POST" else b"")
    finally:
        await session.close()
