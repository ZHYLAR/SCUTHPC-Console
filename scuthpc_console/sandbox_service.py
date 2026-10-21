"""Web-facing SlurmBox allocation requests.

The dashboard uses the same SlurmBox batch renderer and state format as the
CLI.  This module only submits an allocation; user commands still run through
``slurmbox exec``/``srun --jobid`` so the keep-alive job never runs user code.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLURMBOX_SRC = ROOT / "skills" / "slurmbox" / "src"
if str(SLURMBOX_SRC) not in sys.path:
    sys.path.insert(0, str(SLURMBOX_SRC))

from slurmbox.batch import render_keepalive_script  # noqa: E402
from slurmbox.config import AllocationConfig, ClusterConfig  # noqa: E402
from slurmbox.state import read_state, write_state  # noqa: E402
from slurmbox.transport import Transport  # noqa: E402

_REQUEST_LOCK = threading.Lock()


def _project(path: Path) -> dict:
    state = read_state(path) or {}
    cluster = state.get("cluster") if isinstance(state.get("cluster"), dict) else {}
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    return {"state": state, "cluster": cluster, "config": config}


def _slurm_time(minutes: int) -> str:
    if minutes < 1:
        raise ValueError("时长必须至少为 1 分钟")
    hours, remainder = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{days}-{hours:02d}:{remainder:02d}:00" if days else f"{hours:02d}:{remainder:02d}:00"


def request_allocation(*, project_root: Path, ssh_alias: str, remote_workdir: str,
                       cluster_id: str, partition: str, gpus: int,
                       duration_minutes: int | None = None,
                       duration_hours: int | None = None) -> dict:
    if gpus < 1 or gpus > 8:
        raise ValueError("卡数必须在 1 到 8 之间")
    if duration_minutes is None:
        duration_minutes = int(duration_hours or 8) * 60
    if duration_minutes < 1 or duration_minutes > 72 * 60:
        raise ValueError("时长必须在 1 分钟到 72 小时之间")
    if not ssh_alias:
        raise ValueError("没有配置集群 SSH 别名")
    if not remote_workdir or any(c in remote_workdir for c in "\r\n\t\x00"):
        raise ValueError("远程工作目录无效")
    cores_per_gpu = 9 if cluster_id.lower() == "hpckapok1" else 8
    cpus = cores_per_gpu * gpus
    mem = f"{24 * gpus}G"
    allocation = AllocationConfig(
        job_name="SlurmBox",
        partition=partition or "gpuA800",
        gres=f"gpu:{gpus}",
        cpus_per_task=cpus,
        time=_slurm_time(duration_minutes),
        mem=mem,
    )
    cluster = ClusterConfig(ssh_alias=ssh_alias, remote_workdir=remote_workdir)
    root = project_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with _REQUEST_LOCK:
        old = read_state(root) or {}
        result = Transport(cluster).run(
            ["sbatch", "--parsable"], input_text=render_keepalive_script(allocation)
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "sbatch 提交失败")
        job_id = result.stdout.strip().split(";", 1)[0]
        if not job_id.isdigit():
            raise RuntimeError(f"sbatch 返回了无效作业号: {result.stdout.strip()!r}")
        record = {
            "job_id": job_id,
            "status": "running",
            "cluster": asdict(cluster),
            "config": asdict(allocation),
            "created_by": "scuthpc-console",
        }
        allocations = old.get("allocations") if isinstance(old.get("allocations"), list) else []
        allocations = [item for item in allocations if isinstance(item, dict)]
        # Migrate a pre-multi-allocation state once, preserving the old CLI fields.
        if old.get("job_id") and not allocations:
            allocations.append({key: old[key] for key in ("job_id", "status", "cluster", "config", "created_by") if key in old})
        allocations.append(record)
        write_state(root, {**old, **record, "schema_version": 2, "allocations": allocations})
    return {"ok": True, "job_id": job_id, "cluster_id": cluster_id,
            "allocation": asdict(allocation), "project_root": str(root)}


def update_allocation_time(*, project_root: Path, job_id: str, duration_minutes: int) -> dict:
    job_id = str(job_id or "").strip()
    if not job_id.isdigit():
        raise ValueError("作业号无效")
    if duration_minutes < 1 or duration_minutes > 72 * 60:
        raise ValueError("时长必须在 1 分钟到 72 小时之间")
    root = project_root.expanduser().resolve()
    with _REQUEST_LOCK:
        state = read_state(root) or {}
        allocations = state.get("allocations") if isinstance(state.get("allocations"), list) else []
        target = next((item for item in allocations if isinstance(item, dict) and str(item.get("job_id")) == job_id), None)
        if target is None and str(state.get("job_id")) == job_id:
            target = state
        if target is None:
            raise ValueError("找不到这个盒作业")
        cluster_data = target.get("cluster") if isinstance(target.get("cluster"), dict) else {}
        cluster = ClusterConfig(
            ssh_alias=str(cluster_data.get("ssh_alias") or ""),
            remote_workdir=str(cluster_data.get("remote_workdir") or ""),
        )
        if not cluster.ssh_alias:
            raise ValueError("盒没有配置集群 SSH 别名")
        time_limit = _slurm_time(duration_minutes)
        result = Transport(cluster).run(["scontrol", "update", f"JobId={job_id}", f"TimeLimit={time_limit}"])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "修改时限失败")
        config = target.get("config") if isinstance(target.get("config"), dict) else {}
        config = {**config, "time": time_limit}
        target["config"] = config
        if target is state or str(state.get("job_id")) == job_id:
            state["config"] = config
        write_state(root, state)
    return {"ok": True, "job_id": job_id, "time": time_limit}


def project_request_defaults(project_root: Path, ssh_alias: str) -> dict:
    info = _project(project_root)
    return {
        "remote_workdir": str(info["cluster"].get("remote_workdir") or "~/SlurmBox"),
        "ssh_alias": str(info["cluster"].get("ssh_alias") or ssh_alias),
        "config": info["config"],
    }
