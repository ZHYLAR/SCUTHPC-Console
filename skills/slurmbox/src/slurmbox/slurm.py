from __future__ import annotations

import re
import shlex

from .batch import render_keepalive_script
from .config import AllocationConfig
from .transport import CommandResult, Transport


def submit(transport: Transport, config: AllocationConfig) -> str:
    result = transport.run(["sbatch", "--parsable"], input_text=render_keepalive_script(config))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "sbatch failed")
    job_id = result.stdout.strip().split(";", 1)[0]
    if not job_id.isdigit():
        raise RuntimeError(f"unexpected sbatch output: {result.stdout.strip()!r}")
    return job_id


def query(transport: Transport, job_id: str) -> CommandResult:
    return transport.run(["squeue", "-h", "-j", job_id, "-o", "%i %T %M %R"])


def query_history(transport: Transport, job_id: str) -> CommandResult:
    return transport.run(
        ["sacct", "-X", "-j", job_id, "--format=JobID,State,Elapsed,ExitCode", "-n", "-P"]
    )


def step_name(command: list[str]) -> str:
    text = " ".join(command)
    match = re.search(r"([\w.-]+\.(?:py|sh))", text)
    raw = match.group(1) if match else (command[0] if command else "step")
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in raw)
    return (cleaned[:48] or "step")


def _step_workdir(path: str) -> str:
    if not path or path == ".":
        return ""
    if path == "~":
        return 'cd "$HOME" && '
    if path.startswith("~/"):
        return 'cd "$HOME"/' + shlex.quote(path[2:]) + " && "
    return "cd " + shlex.quote(path) + " && "


def step_command(command: list[str], workdir: str = "") -> list[str]:
    # srun starts in the allocation WorkDir, which can differ from remote_workdir.
    script = (
        _step_workdir(workdir)
        + "mkdir -p steps && "
        'exec > >(tee -a "steps/${SLURM_JOB_ID}.${SLURM_STEP_ID}.log") 2>&1 && '
        "exec " + shlex.join(command)
    )
    return ["bash", "-lc", script]


def run_step(
    transport: Transport,
    job_id: str,
    config: AllocationConfig,
    command: list[str],
    workdir: str = "",
) -> int:
    if not command:
        raise ValueError("a command is required")
    args = [
        "srun",
        f"--jobid={job_id}",
        "--overlap",
        f"--job-name={step_name(command)}",
        "--ntasks=1",
        f"--cpus-per-task={config.cpus_per_task}",
    ]
    if config.gres:
        args.append(f"--gres={config.gres}")
    args += ["--export=ALL", *step_command(command, workdir)]
    return transport.interactive(args)


def open_shell(transport: Transport, job_id: str, config: AllocationConfig) -> int:
    args = [
        "srun",
        f"--jobid={job_id}",
        "--overlap",
        "--ntasks=1",
        f"--cpus-per-task={config.cpus_per_task}",
    ]
    if config.gres:
        args.append(f"--gres={config.gres}")
    args += ["--export=ALL", "--pty", "bash", "-l"]
    return transport.interactive(args)

