from __future__ import annotations

from .config import AllocationConfig


def render_keepalive_script(config: AllocationConfig) -> str:
    lines = [
        "#!/bin/bash",
        f"#SBATCH -J {config.job_name}",
        f"#SBATCH -p {config.partition}",
        f"#SBATCH --cpus-per-task={config.cpus_per_task}",
        f"#SBATCH --time={config.time}",
        "#SBATCH --propagate=NONE",
        "#SBATCH -o slurmbox-%j.out",
        "#SBATCH -e slurmbox-%j.out",
        "",
        "set -u",
        "trap 'exit 0' TERM INT HUP",
        'echo "SlurmBox allocation alive: job=${SLURM_JOB_ID:-unknown}"',
        "while :; do",
        '  sleep 60 & wait $! || true',
        "done",
        "",
    ]
    if config.gres:
        lines.insert(3, f"#SBATCH --gres={config.gres}")
    if config.mem:
        lines.insert(7 if config.gres else 6, f"#SBATCH --mem={config.mem}")
    return "\n".join(lines)

