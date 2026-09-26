"""SCUTHPC Console: live Slurm snapshots from the SCUT Kapok clusters over SSH."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFAULT_HOSTS = (
    ("scut-hpc1", "hpckapok1", "集群 1 · hpckapok1"),
    ("scut-hpc", "hpckapok2", "集群 2 · hpckapok2"),
)

DEFAULT_LINKS = {
    "jobs": "https://hpckapok.scut.edu.cn/mis/user/historyJobs",
    "files": "https://hpckapok.scut.edu.cn/files/cluster_login2/public/home/{user}",
}


def load_settings() -> tuple[tuple[tuple[str, str, str], ...], dict[str, str]]:
    path = ROOT / "config.json"
    if not path.is_file():
        return DEFAULT_HOSTS, dict(DEFAULT_LINKS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_HOSTS, dict(DEFAULT_LINKS)
    hosts = []
    for item in raw.get("hosts") or []:
        ssh = str(item.get("ssh") or "").strip()
        cid = str(item.get("id") or "").strip()
        label = str(item.get("label") or cid or ssh).strip()
        if ssh and cid:
            hosts.append((ssh, cid, label))
    links = dict(DEFAULT_LINKS)
    for key in ("jobs", "files"):
        value = (raw.get("links") or {}).get(key)
        if isinstance(value, str) and value.strip():
            links[key] = value.strip()
    return tuple(hosts) or DEFAULT_HOSTS, links

TTL_SEC = 12
SSH_TIMEOUT = 80

_lock = threading.Lock()
_cache: dict = {"t": 0.0, "data": None}

REMOTE = r"""
import json, os, re, subprocess

def run(args, timeout=30):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as exc:
        return 1, "", str(exc)

def field(line, key):
    m = re.search(r"(?:^|\s)%s=(\S+)" % re.escape(key), line)
    return m.group(1) if m else ""

def tres_gpu(tres):
    if not tres or tres == "(null)":
        return 0
    plain = re.findall(r"(?:^|,)gres/(?:gpu|dcu)=(\d+)", tres)
    if plain:
        return sum(int(x) for x in plain)
    nums = [int(x) for x in re.findall(r"gres/(?:gpu|dcu):[^=,]+=(\d+)", tres)]
    return sum(nums)

def gpu_label(gres):
    if not gres or gres == "(null)":
        return ""
    name = gres
    m = re.search(r"(?:gpu|dcu):([^:,]+)", gres, re.I)
    if m:
        name = m.group(1)
    u = name.upper()
    if "A800" in u:
        return "A800 80GB"
    if "V100" in u:
        return "V100"
    if "MI210" in u:
        return "MI210"
    if "Z100" in u or "HYGON" in u:
        return "DCU Z100"
    if name.isdigit():
        return "GPU"
    return name

def slurm_seconds(text):
    text = (text or "").strip()
    if not text or text in ("UNLIMITED", "INVALID", "N/A", "Unknown"):
        return None
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        try:
            days = int(d)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        nums = [int(float(p)) for p in parts]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.insert(0, 0)
    h, m, s = nums[-3], nums[-2], nums[-1]
    return days * 86400 + h * 3600 + m * 60 + s

def unavailable(state):
    s = (state or "").upper()
    keys = ("DOWN", "DRAIN", "FAIL", "UNKNOWN", "MAINT", "POWER", "ERROR", "FUTURE", "REBOOT")
    return any(k in s for k in keys)

def short_state(state):
    s = (state or "").upper()
    if "DOWN" in s:
        return "down"
    if "DRAIN" in s:
        return "drain"
    if "MIX" in s:
        return "mix"
    if "ALLOC" in s:
        return "alloc"
    if "COMPLET" in s:
        return "comp"
    if "IDLE" in s:
        return "idle"
    if "RESERV" in s:
        return "resv"
    return s.lower() or "unknown"

user = os.environ.get("USER") or ""
cluster_id = os.environ.get("CLUSTER_ID") or ""
out = {"ok": True, "id": cluster_id, "user": user, "host": os.uname()[1], "nodes": [], "jobs": [], "recent": [], "disk": None, "error": ""}

rc, text, err = run(["scontrol", "-o", "show", "node"], 40)
if rc != 0 and not text:
    out["ok"] = False
    out["error"] = (err or "scontrol failed")[:400]
    print(json.dumps(out))
    raise SystemExit(0)

nodes = []
for line in text.splitlines():
    if "NodeName=" not in line:
        continue
    state = field(line, "State")
    cfg = field(line, "CfgTRES")
    alloc = field(line, "AllocTRES")
    try:
        cpu_tot = int(field(line, "CPUTot") or 0)
        cpu_alloc = int(field(line, "CPUAlloc") or 0)
    except ValueError:
        cpu_tot, cpu_alloc = 0, 0
    try:
        load = float(field(line, "CPULoad") or 0)
    except ValueError:
        load = 0.0
    try:
        mem = int(field(line, "RealMemory") or 0)
        free_mem = int(field(line, "FreeMem") or 0)
    except ValueError:
        mem, free_mem = 0, 0
    parts = field(line, "Partitions")
    part = parts.split(",")[0].rstrip("*") if parts and parts != "(null)" else "unknown"
    gpu_tot = tres_gpu(cfg)
    gpu_used = min(tres_gpu(alloc), gpu_tot) if gpu_tot else tres_gpu(alloc)
    bad = unavailable(state)
    nodes.append({
        "name": field(line, "NodeName"),
        "partition": part,
        "state": short_state(state),
        "raw_state": state,
        "offline": bad,
        "cpu_total": cpu_tot,
        "cpu_used": cpu_alloc if not bad else 0,
        "cpu_free": max(cpu_tot - cpu_alloc, 0) if not bad else 0,
        "gpu_total": gpu_tot,
        "gpu_used": gpu_used if not bad else 0,
        "gpu_free": max(gpu_tot - gpu_used, 0) if not bad else 0,
        "gpu_offline": gpu_tot if bad else 0,
        "gpu_label": gpu_label(field(line, "Gres")),
        "load": round(load, 2),
        "mem_mb": mem,
        "free_mem_mb": free_mem if not bad else 0,
    })
out["nodes"] = nodes

rc, text, err = run(["squeue", "-u", user, "-h", "-o", "%i|%j|%P|%T|%M|%l|%D|%C|%m|%b|%R|%N"], 25)
jobs = []
for line in text.splitlines():
    bits = line.split("|")
    if len(bits) < 12:
        continue
    jid, name, part, state, elapsed, limit, nodes_n, cpus, mem, gres, reason, nodelist = bits[:12]
    if not jid.isdigit():
        continue
    jobs.append({
        "id": jid,
        "name": name,
        "partition": part.rstrip("*"),
        "state": state,
        "elapsed": elapsed,
        "elapsed_sec": slurm_seconds(elapsed),
        "limit": limit,
        "limit_sec": slurm_seconds(limit),
        "nodes": nodes_n,
        "cpus": int(cpus) if cpus.isdigit() else 0,
        "mem": mem,
        "gres": gres if gres != "N/A" else "",
        "reason": reason,
        "nodelist": nodelist if nodelist != "None" else "",
        "workdir": "",
        "stdout": "",
        "gpus": [],
        "cpu_pct": None,
        "rss_mb": None,
        "procs": None,
        "log": [],
        "metrics_error": "",
    })

for job in jobs:
    rc, text, err = run(["scontrol", "-o", "show", "job", job["id"]], 20)
    line = text.strip().splitlines()[0] if text.strip() else ""
    if line:
        job["workdir"] = field(line, "WorkDir")
        stdout = field(line, "StdOut")
        job["stdout"] = stdout
        if job["state"] == "RUNNING" and stdout.startswith("/") and os.path.isfile(stdout):
            try:
                if os.path.getsize(stdout) < 30000000:
                    rc2, tail, _ = run(["tail", "-n", "12", stdout], 10)
                    job["log"] = [ln[:240] for ln in tail.splitlines() if ln.strip()][-12:]
            except OSError:
                pass
        rc2, st, _ = run(["sstat", "-n", "-p", "-j", job["id"] + ".batch", "-o", "MaxRSS"], 15)
        rss = (st or "").strip().split("|")[0].strip()
        m = re.match(r"([\d.]+)([KMG])?", rss)
        if m:
            val = float(m.group(1))
            unit = m.group(2) or "K"
            mult = {"K": 1.0 / 1024, "M": 1.0, "G": 1024.0}[unit]
            job["rss_mb"] = round(val * mult, 1)

    wants_gpu = "gpu" in (job["gres"] or "").lower() or job["partition"].lower().startswith("gpu")
    if job["state"] == "RUNNING" and wants_gpu:
        probe = (
            "nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw "
            "--format=csv,noheader,nounits; echo @@; "
            "ps -u \"$USER\" -o pcpu=,rss= --no-headers | "
            "awk '{c+=$1;r+=$2;n++} END{printf \"%.1f %.0f %d\\n\", c+0, r+0, n+0}'"
        )
        rc, text, err = run([
            "srun", "--jobid=" + job["id"], "--overlap", "-N1", "-n1", "--ntasks=1",
            "bash", "-lc", probe,
        ], 45)
        if rc != 0 and "@@" not in text:
            job["metrics_error"] = (err or text or "srun failed").strip().splitlines()[-1][:240]
        else:
            gpu_txt, _, cpu_txt = text.partition("@@")
            gpus = []
            for row in gpu_txt.splitlines():
                cols = [c.strip() for c in row.split(",")]
                if len(cols) < 6 or not cols[0].isdigit():
                    continue
                try:
                    gpus.append({
                        "index": int(cols[0]),
                        "util": float(cols[1]),
                        "mem_used": float(cols[2]),
                        "mem_total": float(cols[3]),
                        "temp": float(cols[4]),
                        "power": float(cols[5]),
                    })
                except ValueError:
                    continue
            job["gpus"] = gpus
            bits = cpu_txt.strip().split()
            if len(bits) >= 3:
                try:
                    cpu_sum = float(bits[0])
                    rss_kb = float(bits[1])
                    procs = int(float(bits[2]))
                    if job["cpus"]:
                        job["cpu_pct"] = round(min(cpu_sum / job["cpus"], 100.0), 1)
                    job["procs"] = procs
                    if rss_kb > 0:
                        job["rss_mb"] = round(rss_kb / 1024.0, 1)
                except ValueError:
                    pass

by_name = {n["name"]: n for n in nodes}
for job in jobs:
    node = by_name.get((job.get("nodelist") or "").split(",")[0])
    if node and node["cpu_total"]:
        job["node_load_pct"] = round(min(node["load"] / float(node["cpu_total"]) * 100.0, 100.0), 1)
    else:
        job["node_load_pct"] = None
out["jobs"] = jobs

rc, text, err = run([
    "sacct", "-u", user, "-S", "now-2days", "-X", "-n", "-P",
    "--format=JobID,JobName,Partition,State,Elapsed,AllocCPUS,NodeList,ExitCode,End",
], 25)
recent = []
for line in text.splitlines():
    bits = line.split("|")
    if len(bits) < 9 or not bits[0].isdigit():
        continue
    recent.append({
        "id": bits[0],
        "name": bits[1],
        "partition": bits[2],
        "state": bits[3],
        "elapsed": bits[4],
        "cpus": bits[5],
        "nodelist": bits[6],
        "exit": bits[7],
        "end": bits[8],
        "log": [],
        "hint": "",
    })
out["recent"] = recent[-20:]

def _bad_state(state):
    s = (state or "").upper()
    return any(k in s for k in ("FAIL", "TIMEOUT", "OUT_OF", "NODE_FAIL"))

def _find_log(wd, jid):
    if not wd.startswith("/") or not os.path.isdir(wd):
        return ""
    hits = []
    dirs = [wd] + [os.path.join(wd, sub) for sub in ("reports", "logs", "log", "slurm", "out")]
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            if jid not in fn:
                continue
            path = os.path.join(d, fn)
            if os.path.isfile(path):
                hits.append(path)
    if not hits:
        return ""
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0]

for rec in [r for r in out["recent"] if _bad_state(r["state"])][-6:]:
    rc, text, err = run(["sacct", "-j", rec["id"], "-P", "-n", "-X", "-o", "WorkDir"], 12)
    wd = text.strip().splitlines()[-1].strip() if text.strip() else ""
    stdout = _find_log(wd, rec["id"])
    if not stdout:
        rc, text, err = run(["scontrol", "-o", "show", "job", rec["id"]], 8)
        line = text.strip().splitlines()[-1] if text.strip() else ""
        alt = field(line, "StdOut") if "StdOut=" in line else ""
        if alt.startswith("/") and os.path.isfile(alt):
            stdout = alt
    if not stdout:
        continue
    try:
        if os.path.getsize(stdout) >= 30000000:
            continue
        rc2, tail, _ = run(["tail", "-n", "30", stdout], 8)
        lines = [ln[:240] for ln in tail.splitlines() if ln.strip()][-8:]
        rec["log"] = lines
        hint = ""
        for ln in reversed(lines):
            if re.search(r"Error|Exception|Traceback|Killed|OOM|RC=|CUDA|RuntimeError", ln, re.I):
                hint = ln.strip()
                break
        if not hint and lines:
            hint = lines[-1].strip()
        rec["hint"] = hint[:180]
    except OSError:
        pass

rc, text, err = run(["df", "-B1", os.path.expanduser("~")], 15)
lines = [ln for ln in text.splitlines() if ln.strip()]
if len(lines) >= 2:
    cols = lines[-1].split()
    if len(cols) >= 6:
        try:
            out["disk"] = {
                "total": int(cols[1]),
                "used": int(cols[2]),
                "avail": int(cols[3]),
                "pct": cols[4],
                "mount": cols[5],
                "path": os.path.expanduser("~"),
            }
        except ValueError:
            out["disk"] = None

print(json.dumps(out))
"""


def _ssh(host: str, cluster_id: str) -> dict:
    script = (
        f"export CLUSTER_ID={cluster_id}\n"
        "python3 - <<'HPC_MONITOR_PY'\n"
        + REMOTE.replace("\r\n", "\n").replace("\r", "\n")
        + "\nHPC_MONITOR_PY\n"
    ).encode()
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=20",
                "-o",
                "BatchMode=yes",
                host,
                "bash",
                "-s",
            ],
            input=script,
            capture_output=True,
            timeout=SSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "id": cluster_id, "error": "SSH 超时"}
    except Exception as exc:
        return {"ok": False, "id": cluster_id, "error": str(exc)}
    raw = proc.stdout.decode("utf-8", "replace").strip()
    if not raw:
        err = proc.stderr.decode("utf-8", "replace").strip()
        return {"ok": False, "id": cluster_id, "error": err[-400:] or "无输出"}
    try:
        return json.loads(raw.splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "id": cluster_id, "error": raw[-400:]}


def _summarize(cluster: dict) -> dict:
    parts: dict[str, dict] = {}
    for node in cluster.get("nodes") or []:
        name = node["partition"]
        bucket = parts.setdefault(
            name,
            {
                "name": name,
                "nodes": 0,
                "online": 0,
                "offline": 0,
                "idle_nodes": 0,
                "mix_nodes": 0,
                "alloc_nodes": 0,
                "cpu_total": 0,
                "cpu_free": 0,
                "cpu_used": 0,
                "gpu_total": 0,
                "gpu_free": 0,
                "gpu_used": 0,
                "gpu_offline": 0,
                "gpu_label": "",
                "free_mem_mb": 0,
            },
        )
        bucket["nodes"] += 1
        if node["offline"]:
            bucket["offline"] += 1
        else:
            bucket["online"] += 1
        st = node["state"]
        if st == "idle":
            bucket["idle_nodes"] += 1
        elif st == "mix":
            bucket["mix_nodes"] += 1
        elif st == "alloc":
            bucket["alloc_nodes"] += 1
        for key in ("cpu_total", "cpu_free", "cpu_used", "gpu_total", "gpu_free", "gpu_used", "gpu_offline", "free_mem_mb"):
            src = "free_mem_mb" if key == "free_mem_mb" else key
            bucket[key] += node[src]
        if node["gpu_label"] and not bucket["gpu_label"]:
            bucket["gpu_label"] = node["gpu_label"]
    order = sorted(parts.values(), key=lambda p: (-(p["gpu_total"] > 0), -p["gpu_free"], -p["cpu_free"]))
    order = [p for p in order if p["name"] != "unknown"]
    for part in order:
        if "A800" in part["name"].upper() and part["gpu_label"] in ("", "GPU"):
            part["gpu_label"] = "A800 80GB" if cluster.get("id") == "hpckapok2" else "A800"
    cluster["partitions"] = order
    cluster["totals"] = {
        "cpu_free": sum(p["cpu_free"] for p in order),
        "cpu_used": sum(p["cpu_used"] for p in order),
        "cpu_total": sum(p["cpu_total"] for p in order),
        "gpu_free": sum(p["gpu_free"] for p in order),
        "gpu_used": sum(p["gpu_used"] for p in order),
        "gpu_offline": sum(p["gpu_offline"] for p in order),
        "gpu_total": sum(p["gpu_total"] for p in order),
        "nodes": sum(p["nodes"] for p in order),
        "online": sum(p["online"] for p in order),
        "offline": sum(p["offline"] for p in order),
    }
    return cluster


def collect() -> dict:
    from concurrent.futures import ThreadPoolExecutor

    hosts, links = load_settings()
    clusters = []
    with ThreadPoolExecutor(max_workers=len(hosts) or 1) as pool:
        futs = [pool.submit(_ssh, host, cid) for host, cid, _label in hosts]
        for (host, cid, label), fut in zip(hosts, futs):
            data = fut.result()
            data["label"] = label
            data["host_alias"] = host
            if data.get("ok"):
                _summarize(data)
            clusters.append(data)
    user = next((c.get("user") for c in clusters if c.get("user")), "")
    files = links["files"].format(user=user) if user else ""
    return {
        "user": user,
        "links": {
            "jobs": links["jobs"],
            "files": files,
        },
        "fetched_at": time.time(),
        "clusters": clusters,
    }


def snapshot() -> dict:
    now = time.time()
    with _lock:
        if _cache["data"] and now - _cache["t"] < TTL_SEC:
            return _cache["data"]
        data = collect()
        _cache["t"] = time.time()
        _cache["data"] = data
        return data
