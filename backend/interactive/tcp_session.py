"""Persistent TCP session tool for CTF challenges.

Provides interactive TCP connections with menu-style interaction,
multi-round send/receive, binary data support, and reconnection.
Essential for pwn, netcat, and interactive service challenges.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Session registry: scoped_key -> TcpSession.
# Keys include a per-solver scope so concurrent solvers cannot see or close
# each other's sessions even when both use the default session_id.
_sessions: dict[str, TcpSession] = {}

MAX_RECV_SIZE = 100_000  # bytes
DEFAULT_TIMEOUT = 30.0  # seconds


def _skey(scope: str, session_id: str) -> str:
    return f"{scope}\x1f{session_id}"


@dataclass
class TcpSession:
    """A persistent TCP connection for interactive CTF challenges."""

    session_id: str
    host: str = ""
    port: int = 0
    _reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    _connected: bool = False
    _history: list[dict[str, Any]] = field(default_factory=list)
    _recv_buffer: bytes = b""
    _send_count: int = 0
    _recv_count: int = 0

    @property
    def connected(self) -> bool:
        return self._connected and self._writer is not None and not self._writer.is_closing()

    async def connect(self, host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Connect to a TCP service."""
        if self.connected:
            await self.disconnect()

        self.host = host
        self.port = port

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            self._connected = True
            self._history.append({
                "type": "connect",
                "host": host,
                "port": port,
            })
            logger.info(f"TCP session '{self.session_id}' connected to {host}:{port}")
            return f"Connected to {host}:{port}"
        except Exception as e:
            self._connected = False
            return f"Connection failed: {e}"

    async def send(self, data: str, newline: bool = True) -> str:
        """Send data over the TCP connection.

        Args:
            data: Text to send.
            newline: Append newline (\\n) to the data.
        """
        if not self.connected:
            return "Not connected. Call connect() first."

        assert self._writer is not None

        payload = data + ("\n" if newline else "")
        try:
            self._writer.write(payload.encode("utf-8"))
            await self._writer.drain()
            self._send_count += 1
            self._history.append({
                "type": "send",
                "data": data[:1000],
                "bytes": len(payload),
            })
            return f"Sent {len(payload)} bytes"
        except Exception as e:
            self._connected = False
            return f"Send error: {e}"

    async def send_bytes(self, data_hex: str) -> str:
        """Send raw bytes (given as hex string) over the TCP connection."""
        if not self.connected:
            return "Not connected. Call connect() first."

        assert self._writer is not None

        try:
            raw = bytes.fromhex(data_hex)
            self._writer.write(raw)
            await self._writer.drain()
            self._send_count += 1
            self._history.append({
                "type": "send_bytes",
                "hex": data_hex[:200],
                "bytes": len(raw),
            })
            return f"Sent {len(raw)} raw bytes"
        except Exception as e:
            self._connected = False
            return f"Send error: {e}"

    async def recv(self, timeout: float = DEFAULT_TIMEOUT, size: int = 4096) -> str:
        """Receive data from the TCP connection.

        Args:
            timeout: Seconds to wait for data.
            size: Maximum bytes to read.

        Returns:
            Received text, or error message.
        """
        if not self.connected:
            return "Not connected. Call connect() first."

        assert self._reader is not None

        try:
            data = await asyncio.wait_for(
                self._reader.read(size),
                timeout=timeout,
            )
            if not data:
                self._connected = False
                return "Connection closed by remote."

            self._recv_count += 1
            self._recv_buffer += data

            # Try to decode as UTF-8, fall back to repr for binary
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = repr(data)

            self._history.append({
                "type": "recv",
                "bytes": len(data),
                "text": text[:2000],
            })

            if len(text) > MAX_RECV_SIZE:
                text = text[:MAX_RECV_SIZE] + f"\n... [truncated, {len(text)} total chars]"
            return text
        except TimeoutError:
            return "Receive timed out (no data available)."
        except Exception as e:
            self._connected = False
            return f"Receive error: {e}"

    async def recv_until(self, delimiter: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Receive data until a delimiter string is found.

        Useful for menu-style interactions where you wait for a prompt.
        """
        if not self.connected:
            return "Not connected. Call connect() first."

        assert self._reader is not None

        try:
            delim_bytes = delimiter.encode("utf-8")
            data = await asyncio.wait_for(
                self._reader.readuntil(delim_bytes),
                timeout=timeout,
            )
            self._recv_count += 1
            text = data.decode("utf-8", errors="replace")
            self._history.append({
                "type": "recv_until",
                "delimiter": delimiter[:100],
                "bytes": len(data),
                "text": text[:2000],
            })
            if len(text) > MAX_RECV_SIZE:
                text = text[:MAX_RECV_SIZE] + "\n... [truncated]"
            return text
        except asyncio.IncompleteReadError as e:
            # Connection closed before delimiter found
            text = e.partial.decode("utf-8", errors="replace") if e.partial else ""
            self._connected = False
            return f"Connection closed before delimiter. Partial data:\n{text}"
        except TimeoutError:
            return f"Timed out waiting for delimiter: {delimiter!r}"
        except Exception as e:
            self._connected = False
            return f"recv_until error: {e}"

    async def send_recv(self, data: str, timeout: float = DEFAULT_TIMEOUT, newline: bool = True) -> str:
        """Send data and immediately receive the response.

        This is the most common pattern for interactive challenges:
        send a command, read the response.
        """
        send_result = await self.send(data, newline=newline)
        if "error" in send_result.lower():
            return send_result
        recv_result = await self.recv(timeout=timeout)
        return recv_result

    async def interactive_menu(self, steps: list[dict[str, Any]], timeout: float = DEFAULT_TIMEOUT) -> str:
        """Execute a sequence of send/recv steps for menu-style interaction.

        Args:
            steps: List of {"send": "data"}, {"send_hex": "00ff"}, {"sleep": 0.5},
                {"recv": true} or {"recv_until": "prompt"} dicts.
            timeout: Per-step timeout.

        Returns:
            All received data concatenated.
        """
        results: list[str] = []
        for i, step in enumerate(steps, 1):
            r = ""
            if "send" in step:
                r = await self.send(step["send"])
                results.append(f"[Step {i}] Sent: {step['send'][:200]}")
            elif "send_hex" in step:
                r = await self.send_bytes(step["send_hex"])
                results.append(f"[Step {i}] Sent hex: {step['send_hex'][:200]}")
            elif "sleep" in step:
                await asyncio.sleep(float(step["sleep"]))
                results.append(f"[Step {i}] Slept {step['sleep']}s")
                continue
            elif "recv_until" in step:
                r = await self.recv_until(step["recv_until"], timeout=timeout)
                results.append(f"[Step {i}] Received: {r[:500]}")
            elif step.get("recv"):
                r = await self.recv(timeout=timeout)
                results.append(f"[Step {i}] Received: {r[:500]}")
            else:
                results.append(f"[Step {i}] Unknown step: {step}")
                continue

            if "error" in r.lower() or "closed" in r.lower():
                results.append(f"[Step {i}] ABORTED: connection lost")
                break

        return "\n".join(results)

    async def disconnect(self) -> str:
        """Close the TCP connection."""
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False
        self._writer = None
        self._reader = None
        self._history.append({"type": "disconnect"})
        return "Disconnected."

    def get_history(self) -> list[dict[str, Any]]:
        return self._history

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "host": self.host,
            "port": self.port,
            "connected": self.connected,
            "sends": self._send_count,
            "recvs": self._recv_count,
            "history_entries": len(self._history),
        }


# ── High-level API functions ───────────────────────────────────────────────


async def tcp_connect(host: str, port: int, session_id: str = "default", timeout: float = DEFAULT_TIMEOUT, scope: str = "") -> str:
    """Connect to a TCP service and return initial banner/data if any.

    Creates a persistent session for multi-round interaction.
    """
    key = _skey(scope, session_id)
    if key not in _sessions:
        _sessions[key] = TcpSession(session_id=session_id)
    session = _sessions[key]

    connect_result = await session.connect(host, port, timeout=timeout)
    if "failed" in connect_result.lower():
        return connect_result

    # Try to receive initial banner/data
    try:
        banner = await session.recv(timeout=min(timeout, 5.0), size=4096)
        return f"{connect_result}\n\nBanner:\n{banner}"
    except Exception:
        return connect_result


async def tcp_send_recv(data: str, session_id: str = "default", timeout: float = DEFAULT_TIMEOUT, newline: bool = True, scope: str = "") -> str:
    """Send data to a TCP session and receive the response.

    Use tcp_connect first to establish the session.
    """
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}'. Call tcp_connect first."
    session = _sessions[key]
    if not session.connected:
        return f"Session '{session_id}' is not connected."
    return await session.send_recv(data, timeout=timeout, newline=newline)


async def tcp_send(data: str, session_id: str = "default", newline: bool = True, scope: str = "") -> str:
    """Send data to a TCP session without receiving."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}'. Call tcp_connect first."
    return await _sessions[key].send(data, newline=newline)


async def tcp_recv(session_id: str = "default", timeout: float = DEFAULT_TIMEOUT, size: int = 4096, scope: str = "") -> str:
    """Receive data from a TCP session without sending."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}'. Call tcp_connect first."
    return await _sessions[key].recv(timeout=timeout, size=size)


async def tcp_recv_until(delimiter: str, session_id: str = "default", timeout: float = DEFAULT_TIMEOUT, scope: str = "") -> str:
    """Receive data until a delimiter is found (e.g. a prompt like '> ' or '$ ')."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}'. Call tcp_connect first."
    return await _sessions[key].recv_until(delimiter, timeout=timeout)


async def tcp_send_bytes(data_hex: str, session_id: str = "default", scope: str = "") -> str:
    """Send raw hex bytes to a TCP session."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}'. Call tcp_connect first."
    return await _sessions[key].send_bytes(data_hex)


async def tcp_interactive(steps: str, session_id: str = "default", timeout: float = DEFAULT_TIMEOUT, scope: str = "") -> str:
    """Execute interactive steps (JSON array of send/recv steps).

    Example steps: [{"send": "1"}, {"recv": true}, {"send": "flag"}, {"recv": true}]
    """
    import json
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}'. Call tcp_connect first."
    try:
        steps_list = json.loads(steps)
    except (json.JSONDecodeError, ValueError) as e:
        return f"Invalid JSON steps: {e}"
    return await _sessions[key].interactive_menu(steps_list, timeout=timeout)


async def tcp_disconnect(session_id: str = "default", scope: str = "") -> str:
    """Disconnect a TCP session."""
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}' found."
    result = await _sessions[key].disconnect()
    del _sessions[key]
    return result


async def tcp_status(session_id: str = "default", scope: str = "") -> str:
    """Show TCP session status and stats."""
    import json
    key = _skey(scope, session_id)
    if key not in _sessions:
        return f"No session '{session_id}' found."
    return json.dumps(_sessions[key].stats, indent=2)


async def cleanup_all_sessions(scope: str | None = None) -> None:
    """Disconnect TCP sessions. With scope, only that solver's sessions are closed."""
    prefix = f"{scope}\x1f" if scope is not None else ""
    for key in list(_sessions.keys()):
        if scope is not None and not key.startswith(prefix):
            continue
        await _sessions[key].disconnect()
        del _sessions[key]
