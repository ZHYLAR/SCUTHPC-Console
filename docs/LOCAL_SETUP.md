# Local Setup

- URL: http://127.0.0.1:8765
- Python: Python 3.11 or newer (a project virtual environment is recommended)
- Settings: `config.json` (ignored by Git; credentials stay in `~/.ssh`)
- SSH: `scut-hpc1` / `scut-hpc1-backup`, `scut-hpc` / `scut-hpc-backup`
- Login service: `~/Library/LaunchAgents/com.zhylar.scuthpc-console.plist` (uses the root `app.py` compatibility entry point)
- Logs: `console.log`, `console.error.log`

The service starts at login and restarts after a crash. It listens only on
127.0.0.1. The browser page polls Slurm and GPU metrics while it is open;
browser notifications and GPU history require keeping the page open.

The service defaults to `127.0.0.1`. When Tailscale is available, it also
listens on the local Tailscale IPv4 address for devices on the same Tailnet.

Disk usage reads only
the existing `/tmp/scuthpc-du-$USER.txt` cache and shows its timestamp. It does
not run a home-directory scan on a login node. If no cache exists, disk usage
is unavailable; the 1 TB figure is a free-tier reference, not a measured quota.

Restart:

```sh
launchctl kickstart -k gui/$(id -u)/com.zhylar.scuthpc-console
```

Stop and disable login startup:

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.zhylar.scuthpc-console.plist
```

Enable again:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zhylar.scuthpc-console.plist
```
