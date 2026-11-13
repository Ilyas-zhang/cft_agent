"""Local-only Linux container smoke: actual DockerSandbox, HTTP/TCP, attachments."""
import asyncio
import json
import os
from pathlib import Path

from backend.interactive.http_session import HttpSession
from backend.interactive.tcp_session import TcpSession
from backend.sandbox import DockerSandbox, configure_path_mapping
from backend.tpctf_network import probe_services


async def main():
    root = Path(os.environ.get("WORKSPACE_ROOT", "/work"))
    configure_path_mapping(str(root), os.environ.get("SANDBOX_BIND_ROOT", str(root)),
                           str(root / "runtime" / "workspaces"))
    directory = root / "runtime" / "docker-smoke"
    (directory / "distfiles").mkdir(parents=True, exist_ok=True)
    (directory / "metadata.yml").write_text("name: local-network-smoke\n")
    (directory / "distfiles" / "own.txt").write_text("own-instance-attachment")

    async def http(reader, writer):
        try:
            await asyncio.wait_for(reader.read(4096), 2)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 16\r\nConnection: close\r\n\r\nlocal-http-smoke")
            await writer.drain()
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()

    async def tcp(reader, writer):
        try:
            writer.write(b"local-tcp-smoke\n")
            await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    web = await asyncio.start_server(http, "127.0.0.1", 0)
    raw = await asyncio.start_server(tcp, "127.0.0.1", 0)
    ports = [web.sockets[0].getsockname()[1], raw.sockets[0].getsockname()[1]]
    info = {"services": [{"host": "127.0.0.1", "port": p, "service_type": kind}
                         for p, kind in zip(ports, ["http", "tcp"], strict=True)]}
    sandbox = DockerSandbox(image=os.environ.get("SANDBOX_IMAGE", "ctf-sandbox:tpctf-p0"),
        challenge_dir=str(directory), network_mode="host", extra_capabilities=False,
        memory_limit="1g", preserve_workspace=True)
    session, connection = HttpSession("local-only"), TcpSession("local-only")
    try:
        await probe_services(info)
        await sandbox.start()
        await probe_services(info, sandbox)
        response = await session.request("GET", f"http://127.0.0.1:{ports[0]}/")
        assert "local-http-smoke" in response
        assert "Connected" in await connection.connect("127.0.0.1", ports[1])
        assert "local-tcp-smoke" in await connection.recv_until("\n", timeout=2)
        curl = await sandbox.exec(f"curl -fsS http://127.0.0.1:{ports[0]}/")
        assert curl.exit_code == 0 and curl.stdout == "local-http-smoke", curl
        attachment = await sandbox.exec("cat /challenge/distfiles/own.txt")
        assert attachment.exit_code == 0 and attachment.stdout == "own-instance-attachment", attachment
        libraries = await sandbox.exec("python3 -c 'import pwn, Crypto, sympy, gmpy2, requests, z3; print(42)'")
        assert libraries.exit_code == 0 and "42" in libraries.stdout, libraries
        print(json.dumps({"agent_http": True, "agent_tcp": True, "sandbox_host_network": True,
                          "sandbox_http": True, "own_attachment_mount": True, "ctf_libraries": True,
                          "official_vpn_tested": False, "official_scoring_tested": False}, indent=2))
    finally:
        await session.close()
        await connection.disconnect()
        await sandbox.stop()
        web.close()
        raw.close()
        await web.wait_closed()
        await raw.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
