"""Resource sizing shared by the monitor and allocation requests."""

from decimal import Decimal, InvalidOperation, ROUND_CEILING


def cores_per_gpu(cluster_id: str) -> int:
    return 9 if cluster_id.lower() == "hpckapok1" else 8


def positive_memory_mib(value: object) -> int:
    # In particular, never let Slurm's special --mem=0 request all node memory.
    text = str(value)
    if not text.isascii() or not text.isdigit() or int(text) < 1:
        raise ValueError("内存必须是正整数 MiB")
    return int(text)


def gib_to_mib(value: object) -> int:
    try:
        gib = Decimal(str(value))
        if not gib.is_finite() or gib <= 0 or gib > 1024 * 1024:
            raise ValueError("内存必须大于 0，且不超过 1048576 GiB")
        return max(1, int((gib * 1024).to_integral_value(rounding=ROUND_CEILING)))
    except (InvalidOperation, TypeError):
        raise ValueError("请输入有效的内存数值（GiB）") from None


def recommend_memory(cluster: dict, partition: str, gpus: int) -> dict | None:
    """Largest schedulable memory on ONE node with the requested GPU/CPU fit.

    This is a capacity estimate, not a reservation or a scheduler start promise.
    FreeMem is deliberately ignored: unused RAM can already belong to jobs.
    """
    if not cluster.get("ok") or gpus < 1:
        return None
    cpus = cores_per_gpu(str(cluster.get("id") or "")) * gpus
    candidates = []
    for node in cluster.get("nodes") or []:
        partitions = node.get("partitions") or str(node.get("partition") or "").split(",")
        if (node.get("offline") or partition not in partitions
                or node.get("gpu_free", 0) < gpus or node.get("cpu_free", 0) < cpus):
            continue
        memory = int(node.get("sched_mem_free_mb") or 0)
        if memory > 0:
            candidates.append({"memory_mib": memory, "node": node["name"],
                               "gpu_free": node["gpu_free"], "cpu_free": node["cpu_free"]})
    return min(candidates, key=lambda item: (-item["memory_mib"], item["node"])) if candidates else None
