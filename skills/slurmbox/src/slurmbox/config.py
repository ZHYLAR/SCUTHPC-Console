from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class ClusterConfig:
    ssh_alias: str = ""
    remote_workdir: str = ""


@dataclass(frozen=True)
class AllocationConfig:
    job_name: str = "SlurmBox"
    partition: str = "gpuA800"
    gres: str = "gpu:1"
    cpus_per_task: int = 8
    time: str = "08:00:00"
    mem: str = ""


@dataclass(frozen=True)
class Config:
    cluster: ClusterConfig = ClusterConfig()
    allocation: AllocationConfig = AllocationConfig()


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    cluster = raw.get("cluster", {})
    allocation = raw.get("allocation", {})
    return Config(
        cluster=ClusterConfig(
            ssh_alias=str(cluster.get("ssh_alias", "")),
            remote_workdir=str(cluster.get("remote_workdir", "")),
        ),
        allocation=AllocationConfig(
            job_name=str(allocation.get("job_name", "SlurmBox")),
            partition=str(allocation.get("partition", "gpuA800")),
            gres=str(allocation.get("gres", "gpu:1")),
            cpus_per_task=int(allocation.get("cpus_per_task", 8)),
            time=str(allocation.get("time", "08:00:00")),
            mem=str(allocation.get("mem", "")),
        ),
    )

