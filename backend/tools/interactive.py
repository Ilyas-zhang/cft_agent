"""Stateful HTTP/TCP interaction tools exposed to solvers.

Bash-based one-shot requests lose cookies/connections between calls. These
tools keep persistent sessions (cookies, redirects, TCP sockets) inside the
agent process, scoped per solver so concurrent challenges cannot interfere.

All JSON-valued parameters are passed as JSON-encoded strings, e.g.
  headers='{"X-Admin": "yes"}'
  json_body='{"username": "admin", "password": "x"}'
"""

from __future__ import annotations

from pydantic_ai import RunContext

from backend.deps import SolverDeps
from backend.interactive import http_session as _http
from backend.interactive import tcp_session as _tcp


def _scope(ctx: RunContext[SolverDeps]) -> str:
    return ctx.deps.session_scope or ctx.deps.challenge_name


# ── HTTP ────────────────────────────────────────────────────────────────────


async def http_request(
    ctx: RunContext[SolverDeps],
    method: str = "GET",
    url: str = "",
    session_id: str = "default",
    base_url: str = "",
    headers: str = "",
    params: str = "",
    json_body: str = "",
    form_body: str = "",
    raw_body: str = "",
    cookies: str = "",
    follow_redirects: bool = False,
) -> str:
    """Send an HTTP request with persistent cookies and headers.

    Use one session_id per target/site (default: "default"). Cookies set by the
    server are reused automatically. Redirect chains are shown step by step
    unless follow_redirects=true. Set base_url once and then pass relative URLs.

    headers/params/json_body/form_body/cookies are JSON-encoded strings.
    json_body sends an application/json body; form_body sends x-www-form-urlencoded.
    raw_body sends the body verbatim (e.g. XML).
    """
    return await _http.http_request(
        method=method,
        url=url,
        session_id=session_id,
        base_url=base_url,
        headers=headers,
        params=params,
        json_body=json_body,
        form_body=form_body,
        raw_body=raw_body,
        cookies=cookies,
        follow_redirects=follow_redirects,
        scope=_scope(ctx),
    )


async def http_diff(
    ctx: RunContext[SolverDeps],
    session_id: str = "default",
) -> str:
    """Show a diff between the last two responses in an HTTP session.

    Useful for spotting boolean-based SQLi, blind injection, and state changes.
    """
    return await _http.http_diff(session_id=session_id, scope=_scope(ctx))


async def http_history(
    ctx: RunContext[SolverDeps],
    session_id: str = "default",
) -> str:
    """List the request history of an HTTP session (method, URL, status, size)."""
    return await _http.http_history(session_id=session_id, scope=_scope(ctx))


async def http_close(
    ctx: RunContext[SolverDeps],
    session_id: str = "default",
) -> str:
    """Close an HTTP session and release its connection pool."""
    return await _http.http_close(session_id=session_id, scope=_scope(ctx))


# ── TCP ─────────────────────────────────────────────────────────────────────


async def tcp_connect(
    ctx: RunContext[SolverDeps],
    host: str,
    port: int,
    session_id: str = "default",
    timeout: float = 30.0,
    use_tls: bool = False,
    server_hostname: str = "",
) -> str:
    """Open a persistent TCP connection and return the initial banner.

    Use one session_id per service. Follow with tcp_send_recv / tcp_recv_until
    for multi-round menu interaction. The connection stays open between calls.
    """
    return await _tcp.tcp_connect(
    host=host, port=port, session_id=session_id, timeout=timeout, scope=_scope(ctx),
    use_tls=use_tls, server_hostname=server_hostname,
    )


async def tcp_send_recv(
    ctx: RunContext[SolverDeps],
    data: str,
    session_id: str = "default",
    timeout: float = 30.0,
    newline: bool = True,
) -> str:
    """Send text to a TCP session and return the received response.

    newline=true appends "\\n" (most menu protocols expect it). Call tcp_connect
    first. Use this repeatedly to walk multi-step menus.
    """
    return await _tcp.tcp_send_recv(
        data=data,
        session_id=session_id,
        timeout=timeout,
        newline=newline,
        scope=_scope(ctx),
    )


async def tcp_send(
    ctx: RunContext[SolverDeps],
    data: str,
    session_id: str = "default",
    newline: bool = True,
) -> str:
    """Send text to a TCP session without waiting for a response."""
    return await _tcp.tcp_send(
        data=data, session_id=session_id, newline=newline, scope=_scope(ctx)
    )


async def tcp_recv(
    ctx: RunContext[SolverDeps],
    session_id: str = "default",
    timeout: float = 30.0,
    size: int = 4096,
) -> str:
    """Receive up to `size` bytes from a TCP session without sending."""
    return await _tcp.tcp_recv(
        session_id=session_id, timeout=timeout, size=size, scope=_scope(ctx)
    )


async def tcp_recv_until(
    ctx: RunContext[SolverDeps],
    delimiter: str,
    session_id: str = "default",
    timeout: float = 30.0,
) -> str:
    """Receive until a delimiter appears (e.g. "> " prompt or "\\n")."""
    return await _tcp.tcp_recv_until(
        delimiter=delimiter, session_id=session_id, timeout=timeout, scope=_scope(ctx)
    )


async def tcp_send_bytes(
    ctx: RunContext[SolverDeps],
    data_hex: str,
    session_id: str = "default",
) -> str:
    """Send raw bytes (hex-encoded string, e.g. "000102ff") to a TCP session."""
    return await _tcp.tcp_send_bytes(
        data_hex=data_hex, session_id=session_id, scope=_scope(ctx)
    )


async def tcp_interactive(
    ctx: RunContext[SolverDeps],
    steps: str,
    session_id: str = "default",
    timeout: float = 30.0,
) -> str:
    """Run a scripted sequence of send/recv steps on a TCP session.

    steps is a JSON array, e.g.:
      [{"send": "1"}, {"recv": true}, {"send": "2"}, {"recv": true}]
      [{"send": "name"}, {"send": "admin"}, {"recv_until": ">"}]
    Each step may contain: send (str), send_hex (str), recv (true),
    recv_until (delimiter str), sleep (seconds).
    """
    return await _tcp.tcp_interactive(
        steps=steps, session_id=session_id, timeout=timeout, scope=_scope(ctx)
    )


async def tcp_disconnect(
    ctx: RunContext[SolverDeps],
    session_id: str = "default",
) -> str:
    """Close a TCP session."""
    return await _tcp.tcp_disconnect(session_id=session_id, scope=_scope(ctx))


async def tcp_status(
    ctx: RunContext[SolverDeps],
    session_id: str = "default",
) -> str:
    """Show connection state and send/recv counters for a TCP session."""
    return await _tcp.tcp_status(session_id=session_id, scope=_scope(ctx))


async def cleanup_solver_sessions(scope: str) -> None:
    """Close all HTTP/TCP sessions belonging to one solver."""
    await _http.cleanup_all_sessions(scope)
    await _tcp.cleanup_all_sessions(scope)


INTERACTIVE_TOOLS = [
    http_request,
    http_diff,
    http_history,
    http_close,
    tcp_connect,
    tcp_send_recv,
    tcp_send,
    tcp_recv,
    tcp_recv_until,
    tcp_send_bytes,
    tcp_interactive,
    tcp_disconnect,
    tcp_status,
]
