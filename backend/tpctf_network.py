"""Host-side WireGuard lifecycle and connection checks for assigned instances."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import platform
from pathlib import Path


def routes_in_config(config: str):
    routes = []
    for line in config.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() in {"preup", "postup", "predown", "postdown", "saveconfig"}:
            raise ValueError("VPN config contains executable hooks or SaveConfig; review it outside the Agent")
        if key.strip().lower() == "allowedips":
            routes.extend(ipaddress.ip_network(v.strip(), strict=False) for v in value.split(","))
    if not routes or any(route.prefixlen == 0 for route in routes):
        raise ValueError("VPN config must contain target routes, never a default internet route")
    return routes


class WireGuardManager:
    def __init__(self, root: Path, mode: str = "auto", command=None):
        self.root, self.mode = root, mode
        self.active: dict[str, tuple[Path, list]] = {}
        self.lock = asyncio.Lock()
        self.command = command or self._command

    async def _command(self, *args):
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        try:
            code = await asyncio.wait_for(proc.wait(), 30)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("WireGuard command timed out") from None
        if code:
            raise RuntimeError(f"WireGuard command failed ({code}); inspect host setup, not credentials in logs")

    async def connect(self, instance_id: str, config: str):
        if self.mode == "external":
            return
        if platform.system() != "Linux":
            raise RuntimeError("Automatic WireGuard requires Linux; use external mode with an established VPN")
        routes = routes_in_config(config)
        async with self.lock:
            if instance_id in self.active:
                return
            if any(a.version == b.version and a.overlaps(b)
                   for _, networks in self.active.values() for a in networks for b in routes):
                raise RuntimeError("VPN routes overlap; keep concurrency at 1 until namespace isolation is configured")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root.chmod(0o700)
            # Linux interface names are limited to 15 characters.
            name = "tp" + hashlib.sha256(instance_id.encode()).hexdigest()[:10]
            path = (self.root / (name + ".conf")).resolve()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(config)
            # Recover an interface left by this same instance after a crash.
            if Path("/sys/class/net", name).exists():
                await self.command("wg-quick", "down", str(path))
            try:
                await self.command("wg-quick", "up", str(path))
            except BaseException:
                try:
                    await self.command("wg-quick", "down", str(path))
                except Exception:
                    pass
                path.unlink(missing_ok=True)
                raise
            self.active[instance_id] = (path, routes)

    async def disconnect(self, instance_id: str):
        async with self.lock:
            entry = self.active.get(instance_id)
            if entry:
                await self.command("wg-quick", "down", str(entry[0]))
                entry[0].unlink(missing_ok=True)
                del self.active[instance_id]


async def probe_services(instance: dict, sandbox=None):
    """Check only published endpoints; never scan a subnet or choose external hosts."""
    import shlex
    services = instance.get("services") or []
    if not services:
        raise RuntimeError("Ready instance has no published services")
    failures = []
    for service in services:
        host, port = service["host"], int(service["port"])
        if service.get("service_type") == "udp":
            continue  # TCP probes say nothing about UDP reachability.
        if sandbox is None:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
                writer.close()
                await writer.wait_closed()
            except (OSError, TimeoutError):
                failures.append(f"{host}:{port}")
        else:
            # The same host/port must be reachable from the shell tool's network.
            code = f"import socket; s=socket.create_connection(({host!r},{port}),5); s.close()"
            result = await sandbox.exec("python3 -c " + shlex.quote(code), timeout_s=10)
            if result.exit_code:
                failures.append(f"{host}:{port}")
    if failures:
        raise RuntimeError("Assigned targets unreachable from " + ("sandbox: " if sandbox else "Agent: ") + ", ".join(failures))


def connection_info(instance: dict):
    lines = []
    for service in instance.get("services") or []:
        host, port = service["host"], service["port"]
        kind = service["service_type"]
        if kind in {"http", "https"}:
            lines.append(f"{kind}://{host}:{port}{service.get('path') or '/'}")
        elif kind == "tcp":
            access = service.get("access")
            if access == "tls_sni":
                lines.append(f"TCP TLS: {host}:{port}; use tcp_connect(host={host!r}, port={port}, use_tls=True, server_hostname={host!r})")
            else:
                lines.append(f"nc {host} {port}")
        else:
            lines.append(f"{kind}: {host}:{port}")
    return "\n".join(lines)
