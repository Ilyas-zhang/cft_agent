"""Persistent HTTP session tool for CTF web challenges.

Provides cookie/session persistence, header management, redirect control,
and diff analysis — all essential for interactive web CTF challenges.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Session registry: scoped_key -> HttpSession.
# Keys include a per-solver scope so concurrent solvers cannot see or close
# each other's sessions even when both use the default session_id.
_sessions: dict[str, HttpSession] = {}

MAX_RESPONSE_SIZE = 50_000  # characters


def _skey(scope: str, session_id: str) -> str:
    return f"{scope}\x1f{session_id}"


def _truncate(text: str, limit: int = MAX_RESPONSE_SIZE) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} total chars]"


@dataclass
class HttpSession:
    """A persistent HTTP session with cookies, headers, and history."""

    session_id: str
    base_url: str = ""
    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _history: list[dict[str, Any]] = field(default_factory=list)
    _request_count: int = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                verify=False,  # CTF challenges often use self-signed certs
                timeout=30.0,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
        return self._client

    async def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        form_body: dict[str, str] | None = None,
        raw_body: str | None = None,
        cookies: dict[str, str] | None = None,
        follow_redirects: bool = False,
        allow_redirects: int = 5,
    ) -> str:
        """Make an HTTP request and return the response.

        Handles redirect chains up to allow_redirects depth.
        """
        client = await self._ensure_client()

        # Resolve relative URLs
        if self.base_url and not url.startswith("http"):
            url = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"

        # Merge cookies
        extra_cookies = cookies or {}

        # Prepare body
        content = None
        data = None
        json_data = None
        if raw_body is not None:
            content = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
        elif form_body is not None:
            data = form_body
        elif json_body is not None:
            json_data = json_body

        redirect_chain = []
        current_url = url
        remaining_redirects = allow_redirects if follow_redirects else 0

        while True:
            try:
                client.cookies.update(extra_cookies)
                resp = await client.request(
                    method,
                    current_url,
                    headers=headers,
                    params=params,
                    content=content,
                    data=data,
                    json=json_data,
                )
            except Exception as e:
                return f"HTTP request error: {e}"

            self._request_count += 1

            # Record in history
            entry = {
                "request": {
                    "method": method,
                    "url": current_url,
                    "request_number": self._request_count,
                },
                "response": {
                    "status_code": resp.status_code,
                    "reason": resp.reason_phrase,
                    "headers": dict(resp.headers),
                    "url": str(resp.url),
                    "content_length": len(resp.content),
                    "body": _truncate(resp.text),
                },
            }
            self._history.append(entry)
            self._history = self._history[-100:]

            # Handle redirects
            if resp.status_code in (301, 302, 303, 307, 308) and remaining_redirects > 0:
                location = resp.headers.get("location", "")
                if not location:
                    break
                if not location.startswith("http"):
                    # Relative redirect
                    from urllib.parse import urljoin
                    location = urljoin(str(resp.url), location)
                redirect_chain.append(f"{resp.status_code} → {location}")
                from urllib.parse import urlsplit
                if urlsplit(location).netloc != urlsplit(current_url).netloc:
                    headers = {k: v for k, v in (headers or {}).items()
                               if k.lower() not in {"authorization", "cookie", "proxy-authorization"}}
                current_url = location
                remaining_redirects -= 1
                params = None
                extra_cookies = {}
                # Preserve method and body for 307/308. Conventional 301/302
                # POST and 303 redirects switch to GET and drop their body.
                if (resp.status_code == 303 and method.upper() != "HEAD") or (
                    resp.status_code in (301, 302) and method.upper() == "POST"
                ):
                    method = "GET"
                    content = data = json_data = None
                    headers = {k: v for k, v in (headers or {}).items()
                               if k.lower() not in {"content-type", "content-length"}}
                continue
            break

        # Format response
        parts = []
        if redirect_chain:
            parts.append(f"Redirect chain: {' → '.join(redirect_chain)}")

        resp_text = resp.text
        parts.append(f"HTTP {resp.status_code} {resp.reason_phrase}")
        parts.append(f"URL: {resp.url}")
        parts.append(f"Content-Type: {resp.headers.get('content-type', 'unknown')}")
        parts.append(f"Content-Length: {len(resp.content)}")

        # Show relevant headers
        interesting_headers = ["set-cookie", "x-powered-by", "server", "x-flag", "flag"]
        for h in interesting_headers:
            if h in resp.headers:
                parts.append(f"{h}: {resp.headers[h]}")

        # Show cookies
        if client.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            parts.append(f"Cookies: {cookie_str}")

        parts.append("─" * 40)
        parts.append(_truncate(resp_text))

        return "\n".join(parts)

    async def diff_last_two(self) -> str:
        """Diff the last two response bodies for comparison."""
        if len(self._history) < 2:
            return "Need at least 2 requests to diff."
        prev = self._history[-2]
        curr = self._history[-1]
        # Simple line-by-line diff
        prev_text = str(prev.get("response", {}).get("body", ""))
        curr_text = str(curr.get("response", {}).get("body", ""))
        prev_lines = prev_text.splitlines()
        curr_lines = curr_text.splitlines()

        import difflib
        diff = difflib.unified_diff(prev_lines, curr_lines, lineterm="", n=3)
        result = "\n".join(diff)
        return result if result else "No differences."

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def get_history(self) -> list[dict[str, Any]]:
        return self._history

    @property
    def request_count(self) -> int:
        return self._request_count


# ── Session management ─────────────────────────────────────────────────────


async def http_request(
    method: str = "GET",
    url: str = "",
    session_id: str = "default",
    base_url: str = "",
    headers: str = "",  # JSON string of headers dict
    params: str = "",  # JSON string of params dict
    json_body: str = "",  # JSON string of body
    form_body: str = "",  # JSON string of form data
    raw_body: str = "",
    cookies: str = "",  # JSON string of cookies dict
    follow_redirects: bool = False,
    allow_redirects: int = 5,
    scope: str = "",
) -> str:
    """Make an HTTP request with session persistence.

    Sessions maintain cookies and headers across requests.
    Pass JSON-encoded strings for headers, params, json_body, form_body, cookies.
    """
    key = _skey(scope, session_id)
    # Get or create session
    if key not in _sessions:
        _sessions[key] = HttpSession(session_id=session_id, base_url=base_url)
    elif base_url:
        _sessions[key].base_url = base_url
    session = _sessions[key]

    # Parse JSON parameters
    def _parse_json(s: str) -> dict | None:
        if not s or not s.strip():
            return None
        try:
            result = json.loads(s)
            return result if isinstance(result, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    parsed_headers = _parse_json(headers)
    parsed_params = _parse_json(params)
    parsed_json_body = _parse_json(json_body)
    parsed_form_body = _parse_json(form_body)
    parsed_cookies = _parse_json(cookies)

    return await session.request(
        method=method,
        url=url,
        headers=parsed_headers,
        params=parsed_params,
        json_body=parsed_json_body,
        form_body=parsed_form_body,
        raw_body=raw_body or None,
        cookies=parsed_cookies,
        follow_redirects=follow_redirects,
        allow_redirects=allow_redirects,
    )


async def http_diff(session_id: str = "default", scope: str = "") -> str:
    """Diff the last two responses in a session."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}' found."
    return await _sessions[key].diff_last_two()


async def http_history(session_id: str = "default", scope: str = "") -> str:
    """Show request history for a session."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}' found."
    history = _sessions[key].get_history()
    if not history:
        return "No requests made yet."
    summary = []
    for i, entry in enumerate(history, 1):
        req = entry.get("request", {})
        resp = entry.get("response", {})
        summary.append(
            f"{i}. {req.get('method', '?')} {req.get('url', '?')} → "
            f"HTTP {resp.get('status_code', '?')} ({resp.get('content_length', 0)} bytes)"
        )
    return "\n".join(summary)


async def http_close(session_id: str = "default", scope: str = "") -> str:
    """Close an HTTP session and release resources."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}' found."
    await _sessions[key].close()
    del _sessions[key]
    return f"Session '{session_id}' closed."


async def cleanup_all_sessions(scope: str | None = None) -> None:
    """Close HTTP sessions. With scope, only that solver's sessions are closed."""
    prefix = f"{scope}\x1f" if scope is not None else ""
    for key in list(_sessions.keys()):
        if scope is not None and not key.startswith(prefix):
            continue
        await _sessions[key].close()
        del _sessions[key]
