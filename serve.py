"""Listen on localhost and the Tailscale IPv4 address."""

import asyncio
import subprocess

import uvicorn


def tailscale_ipv4() -> str | None:
    candidates = [
        "tailscale",
        r"C:\Program Files\Tailscale\tailscale.exe",
    ]
    for cmd in candidates:
        try:
            out = subprocess.check_output(
                [cmd, "ip", "-4"],
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        ip = out.strip().splitlines()[0].strip() if out.strip() else ""
        if ip:
            return ip
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Tailscale -ErrorAction Stop).IPAddress",
            ],
            text=True,
            timeout=15,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    ip = out.strip().splitlines()[0].strip() if out.strip() else ""
    return ip or None


async def main() -> None:
    hosts = ["127.0.0.1"]
    ts = tailscale_ipv4()
    if ts and ts not in hosts:
        hosts.append(ts)
    servers = [
        uvicorn.Server(uvicorn.Config("app:app", host=host, port=8765, log_level="info"))
        for host in hosts
    ]
    print("SCUTHPC Console listening:", ", ".join(f"http://{h}:8765" for h in hosts))
    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
