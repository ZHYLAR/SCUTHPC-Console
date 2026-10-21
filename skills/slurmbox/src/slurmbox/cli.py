from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Sequence

from .batch import render_keepalive_script
from .config import ClusterConfig, Config, load_config
from .slurm import open_shell, query, query_history, run_step, submit
from .state import read_state, write_state
from .transport import Transport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slurmbox", description="Persistent Slurm allocation sandbox")
    parser.add_argument("--config", type=Path, default=Path("slurmbox.toml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("status")
    attach = sub.add_parser("attach", help="record an existing active Slurm job")
    attach.add_argument("job_id")
    logs = sub.add_parser("logs", help="show the keep-alive batch log")
    logs.add_argument("--lines", type=int, default=40)
    sub.add_parser("shell")
    sub.add_parser("stop")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--dry-run", action="store_true")
    execute = sub.add_parser("exec", help="run one independent command inside the allocation")
    execute.add_argument("exec_command", nargs=argparse.REMAINDER)
    return parser


def _load(args: argparse.Namespace):
    root = Path.cwd()
    config = load_config(args.config)
    state = read_state(root)
    recorded_cluster = state.get("cluster") if state else None
    if recorded_cluster:
        config = Config(
            cluster=ClusterConfig(**recorded_cluster),
            allocation=config.allocation,
        )
    return root, config, state


def _require_state(state: dict | None) -> str:
    if not state or not state.get("job_id"):
        raise RuntimeError("no SlurmBox allocation is recorded; run `slurmbox start` first")
    return str(state["job_id"])


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root, config, state = _load(args)
    transport = Transport(config.cluster)
    try:
        if args.command == "start":
            if state and state.get("job_id") and state.get("status") == "running":
                raise RuntimeError(f"allocation already recorded: {state['job_id']}")
            job_id = submit(transport, config.allocation)
            write_state(
                root,
                {
                    "job_id": job_id,
                    "status": "running",
                    "cluster": asdict(config.cluster),
                    "config": asdict(config.allocation),
                },
            )
            print(job_id)
            return 0
        if args.command == "status":
            job_id = _require_state(state)
            result = query(transport, job_id)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "squeue failed")
            if result.stdout.strip():
                print(result.stdout.strip())
            else:
                history = query_history(transport, job_id)
                if history.returncode:
                    raise RuntimeError(history.stderr.strip() or "sacct failed")
                print(history.stdout.strip() or f"{job_id} not present in Slurm")
            return 0
        if args.command == "attach":
            if state and state.get("status") == "running":
                raise RuntimeError(f"allocation already recorded: {state['job_id']}")
            result = query(transport, args.job_id)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "squeue failed")
            if not result.stdout.strip():
                raise RuntimeError(f"job {args.job_id} is not visible as an active job for this account")
            write_state(
                root,
                {
                    "job_id": str(args.job_id),
                    "status": "running",
                    "cluster": asdict(config.cluster),
                    "config": asdict(config.allocation),
                },
            )
            print(args.job_id)
            return 0
        if args.command == "logs":
            job_id = _require_state(state)
            if args.lines < 1:
                raise ValueError("--lines must be positive")
            result = transport.run(["tail", "-n", str(args.lines), f"slurmbox-{job_id}.out"])
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "batch log is unavailable")
            print(result.stdout, end="")
            return 0
        if args.command == "exec":
            job_id = _require_state(state)
            command = list(args.exec_command)
            if command[:1] == ["--"]:
                command = command[1:]
            return run_step(
                transport, job_id, config.allocation, command, config.cluster.remote_workdir
            )
        if args.command == "shell":
            return open_shell(transport, _require_state(state), config.allocation)
        if args.command == "stop":
            job_id = _require_state(state)
            result = transport.run(["scancel", job_id])
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "scancel failed")
            write_state(root, {**state, "status": "stopped"})
            print(job_id)
            return 0
        if args.command == "doctor":
            print(f"ssh_alias={config.cluster.ssh_alias or '(local)'}")
            print(f"remote_workdir={config.cluster.remote_workdir or '(current directory)'}")
            print(f"allocation={config.allocation}")
            if args.dry_run:
                print("--- keep-alive script ---")
                print(render_keepalive_script(config.allocation), end="")
                return 0
            result = transport.run(["sh", "-lc", "command -v sbatch; command -v srun; command -v squeue; command -v scancel"])
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Slurm command check failed")
            print(result.stdout, end="")
            return 0
    except (RuntimeError, ValueError) as exc:
        print(f"slurmbox: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

