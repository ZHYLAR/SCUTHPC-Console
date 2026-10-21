"""SCUTHPC Console: live Slurm snapshots from the SCUT Kapok clusters over SSH."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
DISK_TTL = 1800
QUOTA_BYTES = 1024 ** 4

_lock = threading.Lock()
_cache: dict = {"t": 0.0, "data": None}
_disk: dict = {}
_disk_lock = threading.Lock()
_disk_running = False

REMOTE = r"""
import json, os, re, subprocess, datetime

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

SEP = "\x1f"

def job_anchor(value):
    match = re.match(r"\s*(\d+)", value or "")
    return match.group(1) if match else ""

def is_job_row(value):
    # Agent 提交的作业号无法约定：数组、异构、打包都可能带后缀。
    # 能从行首取出数字作业号就纳入；步（.batch/.extern）不是独立作业。
    text = (value or "").strip()
    if not text or text.upper().startswith("JOBID"):
        return False
    if "." in text:
        return False
    return bool(job_anchor(text))

def slurm_lookup_id(value):
    return job_anchor(value) or (value or "").strip()

def split_job(line, n, name_index=1):
    if SEP in line:
        bits = line.split(SEP)
    else:
        bits = line.split("|")
        if len(bits) > n:
            tail_n = n - name_index - 1
            name = "|".join(bits[name_index:len(bits) - tail_n])
            bits = bits[:name_index] + [name] + bits[-tail_n:]
    if len(bits) < n:
        bits = bits + [""] * (n - len(bits))
    return bits[:n]

def srun_into(jid, nodelist):
    # Slurm 20.11 只接受数字作业号。数组、异构组件用主号，并钉在该任务节点上。
    base = slurm_lookup_id(jid)
    cmd = ["srun", "--jobid=" + base, "--overlap", "--job-name=scuthpc-probe", "-N1", "-n1", "--ntasks=1"]
    node = (nodelist or "").split(",")[0]
    if base and jid.strip() != base and node and node not in ("None", "(null)"):
        cmd.extend(["-w", node])
    return cmd

def tres_gpu(tres):
    if not tres or tres == "(null)":
        return 0
    plain = re.findall(r"(?:^|,)gres/(?:gpu|dcu)=(\d+)", tres)
    if plain:
        return sum(int(x) for x in plain)
    nums = [int(x) for x in re.findall(r"gres/(?:gpu|dcu):[^=,]+=(\d+)", tres)]
    return sum(nums)

def tres_mem_mb(tres):
    m = re.search(r"(?:^|,)mem=(\d+)([KMGTP]?)", tres or "")
    if not m:
        return 0
    value = float(m.group(1))
    scale = {"": 1/1024, "K": 1/1024, "M": 1, "G": 1024, "T": 1024*1024}[m.group(2).upper()]
    return int(value * scale)

def parse_test_start(text):
    m = re.search(r"to start at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", text or "")
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return None

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
    mem_alloc = tres_mem_mb(alloc)
    sched_mem_free = max(mem - mem_alloc, 0)
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
        "sched_mem_free_mb": sched_mem_free if not bad else 0,
    })
out["nodes"] = nodes

# A node-capacity calculation cannot account for Slurm priority, reservations,
# account/QOS limits, or backfill. Ask Slurm's scheduler for the actual start
# time of the default one-GPU request and expose it separately.
probe_cores = 9 if "kapok1" in cluster_id.lower() else 8
probe = ["srun", "--test-only", "-p", "gpuA800", "--gres=gpu:1", "-c", str(probe_cores),
         "--mem=24G", "-t", "00:10:00", "true"]
probe_rc, probe_text, probe_err = run(probe, 40)
probe_start = parse_test_start((probe_text or "") + "\n" + (probe_err or ""))
now = datetime.datetime.now().timestamp()
probe_delay = max(0, int(probe_start - now)) if probe_start else None
# Distant dates usually mean Slurm cannot form a finite estimate, for example
# when an unlimited-time job blocks the next usable node.
probe_reliable = probe_delay is not None and probe_delay <= 7 * 86400
out["placement_probe"] = {
    "request": {"partition": "gpuA800", "gpus": 1, "cpus": probe_cores, "mem_mb": 24576, "time_sec": 600},
    "ok": probe_rc == 0,
    "start_time": datetime.datetime.fromtimestamp(probe_start).isoformat(timespec="seconds") if probe_start else None,
    "observed_at": datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
    "delay_seconds": probe_delay,
    "immediate": bool(probe_start is not None and probe_start <= now + 120),
    "reliable": probe_reliable,
    "confidence": "high" if probe_start and probe_start <= now + 120 else ("medium" if probe_reliable else "low"),
    "message": ((probe_text or "") + "\n" + (probe_err or "")).strip()[-400:],
}

rc, text, err = run(["squeue", "-u", user, "-h", "-o", "%i" + SEP + "%j" + SEP + "%P" + SEP + "%T" + SEP + "%M" + SEP + "%l" + SEP + "%D" + SEP + "%C" + SEP + "%m" + SEP + "%b" + SEP + "%R" + SEP + "%N"], 25)
jobs = []
for line in text.splitlines():
    if not line.strip():
        continue
    bits = split_job(line, 12)
    jid, name, part, state, elapsed, limit, nodes_n, cpus, mem, gres, reason, nodelist = bits
    if not is_job_row(jid):
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
        "start_time": None,
        "queue_delay_seconds": None,
        "queue_estimate_reliable": False,
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
    rc, text, err = run(["scontrol", "-o", "show", "job", slurm_lookup_id(job["id"])], 20)
    line = text.strip().splitlines()[0] if text.strip() else ""
    if line:
        job["workdir"] = field(line, "WorkDir")
        start_text = field(line, "StartTime")
        if job["state"] == "PENDING" and start_text not in ("", "Unknown", "N/A", "None"):
            try:
                start_dt = datetime.datetime.strptime(start_text, "%Y-%m-%dT%H:%M:%S")
                start_ts = start_dt.timestamp()
                job["start_time"] = start_dt.isoformat(timespec="seconds")
                job["queue_delay_seconds"] = max(0, int(start_ts - now))
                job["queue_estimate_reliable"] = job["queue_delay_seconds"] <= 7 * 86400
            except ValueError:
                pass
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
        rc, text, err = run(srun_into(job["id"], job.get("nodelist")) + ["bash", "-lc", probe], 45)
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
    if not line.strip():
        continue
    bits = split_job(line, 9)
    if not is_job_row(bits[0]):
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

print(json.dumps(out))
"""


def _run_ssh(host: str, script: bytes, timeout: float) -> subprocess.CompletedProcess:
    try:
        settings = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = {}
    extra = settings.get("ssh_fallbacks", {}).get(host, [])
    if not isinstance(extra, list):
        extra = []
    targets = [host] + [str(item).strip() for item in extra if str(item).strip()]
    deadline = time.monotonic() + timeout
    last = None
    for _attempt in range(2):
        for target in targets:
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                break
            try:
                last = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", target, "bash", "-s"],
                    input=script,
                    capture_output=True,
                    timeout=remaining,
                )
            except subprocess.TimeoutExpired:
                continue
            if last.returncode != 255:
                return last
    if last is not None:
        return last
    raise subprocess.TimeoutExpired("ssh", timeout)


def _ssh(host: str, cluster_id: str) -> dict:
    script = (
        f"export CLUSTER_ID={cluster_id}\n"
        "python3 - <<'HPC_MONITOR_PY'\n"
        + REMOTE.replace("\r\n", "\n").replace("\r", "\n")
        + "\nHPC_MONITOR_PY\n"
    ).encode()
    try:
        proc = _run_ssh(host, script, SSH_TIMEOUT)
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
                "sched_mem_free_mb": 0,
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
        for key in ("cpu_total", "cpu_free", "cpu_used", "gpu_total", "gpu_free", "gpu_used", "gpu_offline", "free_mem_mb", "sched_mem_free_mb"):
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
        "sched_mem_free_mb": sum(p["sched_mem_free_mb"] for p in order),
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


def _query_disk(host: str) -> dict | None:
    script = r"""
out=/tmp/scuthpc-du-$USER.txt
pidf=/tmp/scuthpc-du-$USER.pid
if [ -s "$out" ]; then
  cat "$out"
  printf "SAMPLED_AT=%s\n" "$(stat -c %Y "$out")"
fi
if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
  printf "PATH=%s\n" "$HOME"
  exit 0
fi
need=0
if [ ! -s "$out" ]; then need=1; fi
if [ -s "$out" ] && [ -n "$(find "$out" -mmin +30 2>/dev/null)" ]; then need=1; fi
if [ "$need" = 1 ]; then
  nohup sh -c 'nice -n 19 du -sb "$HOME" > "$1.tmp" && mv "$1.tmp" "$1"' _ "$out" >/dev/null 2>&1 &
  echo $! > "$pidf"
fi
printf "PATH=%s\n" "$HOME"
""".encode()
    try:
        proc = _run_ssh(host, script, 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    used = None
    path = ""
    sampled_at = None
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("SAMPLED_AT="):
            value = line.split("=", 1)[1].strip()
            sampled_at = int(value) if value.isdigit() else None
            continue
        if line.startswith("PATH="):
            path = line.split("=", 1)[1].strip()
            continue
        bits = line.split()
        if bits and bits[0].isdigit():
            used = int(bits[0])
    if used is None:
        return None
    pct = min(100.0, round(used / QUOTA_BYTES * 100, 1))
    return {"used": used, "quota": QUOTA_BYTES, "pct": pct, "path": path, "sampled_at": sampled_at}


def _refresh_disk() -> None:
    global _disk_running
    try:
        hosts, _links = load_settings()
        now = time.time()
        for host, cid, _label in hosts:
            rec = _disk.get(cid)
            if rec and now - rec["t"] < DISK_TTL:
                continue
            disk = _query_disk(host)
            if disk:
                _disk[cid] = {"t": time.time(), "disk": disk}
            else:
                _disk[cid] = {"t": time.time() - DISK_TTL + 60, "disk": (_disk.get(cid) or {}).get("disk")}
    finally:
        with _disk_lock:
            _disk_running = False


def _ensure_disk_refresh() -> None:
    global _disk_running
    hosts, _links = load_settings()
    now = time.time()
    stale = any(
        cid not in _disk or now - _disk[cid]["t"] >= DISK_TTL
        for _host, cid, _label in hosts
    )
    if not stale:
        return
    with _disk_lock:
        if _disk_running:
            return
        _disk_running = True
    threading.Thread(target=_refresh_disk, daemon=True).start()


def _attach_disk(data: dict) -> None:
    for cluster in data.get("clusters") or []:
        rec = _disk.get(cluster.get("id"))
        if rec and rec.get("disk"):
            cluster["disk"] = rec["disk"]


def snapshot() -> dict:
    _ensure_disk_refresh()
    now = time.time()
    with _lock:
        if _cache["data"] and now - _cache["t"] < TTL_SEC:
            data = _cache["data"]
        else:
            data = collect()
            _cache["t"] = time.time()
            _cache["data"] = data
    _attach_disk(data)
    return data


_gpu_lock = threading.Lock()
_gpu_cache: dict = {"t": 0.0, "data": None}

GPU_REMOTE = r"""
import json, os, re, subprocess

def run(args, timeout=30):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as exc:
        return 1, "", str(exc)

user = os.environ.get("USER") or ""
jobs = []
SEP = "\x1f"

def job_anchor(value):
    match = re.match(r"\s*(\d+)", value or "")
    return match.group(1) if match else ""

def is_job_row(value):
    text = (value or "").strip()
    if not text or text.upper().startswith("JOBID") or "." in text:
        return False
    return bool(job_anchor(text))

def split_job(line, n, name_index=1):
    if SEP in line:
        bits = line.split(SEP)
    else:
        bits = line.split("|")
        if len(bits) > n:
            tail_n = n - name_index - 1
            name = "|".join(bits[name_index:len(bits) - tail_n])
            bits = bits[:name_index] + [name] + bits[-tail_n:]
    if len(bits) < n:
        bits = bits + [""] * (n - len(bits))
    return bits[:n]

def srun_into(jid, nodelist):
    base = job_anchor(jid) or (jid or "").strip()
    cmd = ["srun", "--jobid=" + base, "--overlap", "--job-name=scuthpc-probe", "-N1", "-n1", "--ntasks=1"]
    node = (nodelist or "").split(",")[0]
    if base and (jid or "").strip() != base and node and node not in ("None", "(null)"):
        cmd.extend(["-w", node])
    return cmd

rc, text, err = run(["squeue", "-u", user, "-h", "-o", "%i" + SEP + "%j" + SEP + "%P" + SEP + "%T" + SEP + "%b" + SEP + "%N"], 20)
for line in text.splitlines():
    if not line.strip():
        continue
    bits = split_job(line, 6)
    if not is_job_row(bits[0]):
        continue
    jid, name, part, state, gres, nodelist = bits
    if state != "RUNNING":
        continue
    if "gpu" not in (gres or "").lower() and not part.lower().startswith("gpu"):
        continue
    rc, gpu_txt, err = run(srun_into(jid, nodelist) + [
        "nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ], 25)
    gpus = []
    for row in (gpu_txt or "").splitlines():
        cols = [c.strip() for c in row.split(",")]
        if len(cols) < 4 or not cols[0].isdigit():
            continue
        try:
            gpus.append({
                "index": int(cols[0]),
                "util": float(cols[1]),
                "mem_used": float(cols[2]),
                "mem_total": float(cols[3]),
            })
        except ValueError:
            continue
    jobs.append({"id": jid, "name": name, "partition": part, "gpus": gpus, "error": "" if gpus else (err or "").strip().splitlines()[-1:][:1]})
print(json.dumps({"user": user, "jobs": jobs}))
"""


def gpu_sample() -> dict:
    now = time.time()
    if _gpu_cache["data"] and now - _gpu_cache["t"] < 4:
        return _gpu_cache["data"]
    if not _gpu_lock.acquire(blocking=False):
        return _gpu_cache["data"] or {"fetched_at": now, "jobs": []}
    try:
        hosts, _links = load_settings()
        jobs = []
        from concurrent.futures import ThreadPoolExecutor

        def one(host, cid, label):
            try:
                proc = _run_ssh(
                    host,
                    ("python3 - <<'GPU_REMOTE_PY'\n" + GPU_REMOTE + "\nGPU_REMOTE_PY\n").encode(),
                    40,
                )
            except (OSError, subprocess.TimeoutExpired):
                return []
            raw = proc.stdout.decode("utf-8", "replace").strip()
            if not raw:
                return []
            try:
                payload = json.loads(raw.splitlines()[-1])
            except json.JSONDecodeError:
                return []
            found = []
            for job in payload.get("jobs") or []:
                job["cluster"] = cid
                job["label"] = label
                found.append(job)
            return found

        with ThreadPoolExecutor(max_workers=len(hosts) or 1) as pool:
            futs = [pool.submit(one, host, cid, label) for host, cid, label in hosts]
            for fut in futs:
                jobs.extend(fut.result())
        data = {"fetched_at": time.time(), "jobs": jobs}
        _gpu_cache["t"] = data["fetched_at"]
        _gpu_cache["data"] = data
        return data
    finally:
        _gpu_lock.release()


def load_slurmbox_projects(path: Path | None = None) -> list[dict]:
    path = path or (ROOT / "config.json")
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    found = []
    for item in (raw.get("slurmbox") or {}).get("projects") or []:
        if not isinstance(item, dict):
            continue
        root = str(item.get("root") or "").strip()
        if not root:
            continue
        root_path = Path(root).expanduser()
        if not root_path.is_absolute():
            root = str((ROOT / root_path).resolve())
        entry = {"name": str(item.get("name") or "").strip(), "root": root}
        remote_workdir = str(item.get("remote_workdir") or "").strip()
        if remote_workdir:
            entry["remote_workdir"] = remote_workdir
        found.append(entry)
    return found


def sandbox_options() -> dict:
    """Return safe defaults used by the dashboard allocation form."""
    hosts, _links = load_settings()
    projects = load_slurmbox_projects()
    clusters = []
    for ssh, cid, label in hosts:
        clusters.append({
            "ssh_alias": ssh,
            "id": cid,
            "label": label,
            "partition": "gpuA800",
            "cores_per_gpu": 9 if cid.lower() == "hpckapok1" else 8,
            "mem_gib_per_gpu": 24,
            "max_gpus": 4 if cid.lower() == "hpckapok1" else 8,
        })
    return {
        "clusters": clusters,
        "projects": [{"name": p.get("name") or Path(p["root"]).name, "root": p["root"],
                      **({"remote_workdir": p["remote_workdir"]} if p.get("remote_workdir") else {})}
                     for p in projects],
        "duration_minutes": [3, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440, 2880, 4320],
    }


def split_fields(line: str, n: int, name_index: int = 1) -> list[str]:
    if "\x1f" in line:
        bits = line.split("\x1f")
    else:
        bits = line.split("|")
        if len(bits) > n:
            tail_n = n - name_index - 1
            if tail_n > 0:
                name = "|".join(bits[name_index:len(bits) - tail_n])
                bits = bits[:name_index] + [name] + bits[-tail_n:]
            else:
                bits = bits[:name_index] + ["|".join(bits[name_index:])]
    if len(bits) < n:
        bits = bits + [""] * (n - len(bits))
    return bits[:n]


def step_kind(step_id: str) -> str:
    if "." not in (step_id or ""):
        return "job"
    suffix = step_id.split(".", 1)[1]
    if suffix == "batch":
        return "keepalive"
    if suffix.isdigit():
        return "task"
    return "system"


def slurm_seconds(text: str) -> int | None:
    text = (text or "").strip()
    if not text or text in ("UNLIMITED", "INVALID", "N/A", "Unknown", "NONE"):
        return None
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return None
    try:
        nums = [int(float(part)) for part in text.split(":")]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.insert(0, 0)
    hours, minutes, seconds = nums[-3], nums[-2], nums[-1]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_gpu_csv(text: str) -> list[dict]:
    gpus = []
    for row in (text or "").splitlines():
        cols = [col.strip() for col in row.split(",")]
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
    return gpus


def parse_queue_row(text: str) -> dict | None:
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        job_id, state, elapsed, limit, reason, nodelist, partition, name = split_fields(line, 8, 7)
        if not job_id.strip():
            continue
        return {
            "id": job_id.strip(),
            "state": state,
            "elapsed": elapsed,
            "limit": limit,
            "reason": reason,
            "nodelist": "" if nodelist in ("", "None") else nodelist,
            "partition": partition.rstrip("*"),
            "name": name,
            "elapsed_sec": slurm_seconds(elapsed),
            "limit_sec": slurm_seconds(limit),
        }
    return None


def parse_sacct(text: str) -> tuple[dict | None, list[dict]]:
    job = None
    steps = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        step_id, name, state, elapsed, exit_code, end, nodelist = split_fields(line, 7, 1)
        kind = step_kind(step_id.strip())
        row = {
            "id": step_id.strip(),
            "name": name,
            "command": "",
            "state": state,
            "elapsed": elapsed,
            "exit": exit_code,
            "end": "" if end in ("", "Unknown", "None") else end,
            "nodelist": "" if nodelist in ("", "None", "Unknown") else nodelist,
            "elapsed_sec": slurm_seconds(elapsed),
            "kind": kind,
            "log": [],
        }
        if kind == "job":
            job = row
        elif kind != "system":
            steps.append(row)
    return job, steps


def parse_live_steps(text: str) -> list[dict]:
    found = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        step_id, name, elapsed, nodelist = split_fields(line, 4, 1)
        kind = step_kind(step_id.strip())
        if kind in ("job", "system"):
            continue
        found.append({
            "id": step_id.strip(),
            "name": name,
            "elapsed": elapsed,
            "nodelist": "" if nodelist in ("", "None") else nodelist,
            "kind": kind,
        })
    return found


_STEP_ID_RE = re.compile(r"(?:^|\s)StepId=(\S+)")
_COMMAND_RE = re.compile(r"(?:^|\s)Command=(.*?)(?:\s+[A-Z][A-Za-z0-9]*=|$)")


def parse_step_command(line: str) -> tuple[str, str]:
    step_match = _STEP_ID_RE.search(line or "")
    command_match = _COMMAND_RE.search(line or "")
    command = command_match.group(1).strip() if command_match else ""
    if command == "(null)":
        command = ""
    return (step_match.group(1) if step_match else ""), command


def _step_sort_key(step: dict) -> tuple:
    running = 0 if "RUN" in (step.get("state") or "").upper() else 1
    suffix = str(step.get("id") or "").split(".", 1)[-1]
    number = int(suffix) if suffix.isdigit() else -1
    return (running, -number)


def parse_sandbox_job(raw: dict | None) -> dict:
    raw = raw or {}
    live = parse_queue_row(str(raw.get("queue") or ""))
    job_row, steps = parse_sacct(str(raw.get("sacct") or ""))
    if live is None and job_row:
        live = {
            "id": job_row["id"],
            "state": job_row["state"],
            "elapsed": job_row["elapsed"],
            "limit": "",
            "reason": "",
            "nodelist": job_row["nodelist"],
            "partition": "",
            "name": job_row["name"],
            "elapsed_sec": job_row.get("elapsed_sec"),
            "limit_sec": None,
        }
    by_id = {step["id"]: step for step in steps}
    for live_step in parse_live_steps(str(raw.get("steps") or "")):
        current = by_id.get(live_step["id"])
        if current:
            if live_step["elapsed"]:
                current["elapsed"] = live_step["elapsed"]
                current["elapsed_sec"] = slurm_seconds(live_step["elapsed"])
            if live_step["nodelist"]:
                current["nodelist"] = live_step["nodelist"]
            if not current["state"]:
                current["state"] = "RUNNING"
            continue
        current = {
            "id": live_step["id"],
            "name": live_step["name"],
            "command": "",
            "state": "RUNNING",
            "elapsed": live_step["elapsed"],
            "exit": "",
            "end": "",
            "nodelist": live_step["nodelist"],
            "elapsed_sec": slurm_seconds(live_step["elapsed"]),
            "kind": live_step["kind"],
            "log": [],
        }
        by_id[current["id"]] = current
        steps.append(current)
    for line in raw.get("commands") or []:
        step_id, command = parse_step_command(str(line))
        if step_id in by_id and command:
            by_id[step_id]["command"] = command
    step_logs = raw.get("step_logs") if isinstance(raw.get("step_logs"), dict) else {}
    for step in steps:
        lines = step_logs.get(step["id"]) or []
        step["log"] = [str(line)[:240] for line in lines if str(line).strip()][-12:]
    steps.sort(key=_step_sort_key)
    log = [str(line)[:240] for line in (raw.get("log") or []) if str(line).strip()][-20:]
    return {
        "live": live,
        "steps": steps,
        "log": log,
        "gpus": parse_gpu_csv(str(raw.get("gpu") or "")),
        "gpu_error": str(raw.get("gpu_error") or ""),
        "error": str(raw.get("error") or ""),
    }


def _match_host(alias: str, hosts: tuple) -> tuple[str, str, str]:
    for ssh, cid, label in hosts:
        if ssh == alias:
            return ssh, cid, label
    return alias, "", alias


def _box_from_project(project: dict, hosts: tuple, state_override: dict | None = None) -> dict:
    root = Path(str(project.get("root") or "")).expanduser()
    box = {
        "name": project.get("name") or root.name or "SlurmBox",
        "root": str(root),
        "ok": True,
        "error": "",
        "local_status": "missing",
        "job_id": "",
        "ssh_alias": "",
        "cluster_id": "",
        "cluster_label": "",
        "remote_workdir": "",
        "allocation": {},
        "live": None,
        "steps": [],
        "log": [],
        "gpus": [],
        "gpu_error": "",
    }
    if not root.is_dir():
        box["ok"] = False
        box["error"] = "项目目录不存在"
        return box
    state_file = root / ".slurmbox" / "state.json"
    if state_override is not None:
        state = state_override
    elif not state_file.is_file():
        return box
    else:
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            box["ok"] = False
            box["error"] = "无法读取 .slurmbox/state.json"
            return box
    if not isinstance(state, dict):
        box["ok"] = False
        box["error"] = "沙盒状态不是对象"
        return box
    cluster = state.get("cluster") if isinstance(state.get("cluster"), dict) else {}
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    alias = str(cluster.get("ssh_alias") or "")
    job_id = str(state.get("job_id") or "")
    workdir = str(cluster.get("remote_workdir") or "")
    box["local_status"] = str(state.get("status") or "unknown")
    box["ssh_alias"] = alias
    box["remote_workdir"] = workdir
    box["allocation"] = {
        "job_name": str(config.get("job_name") or ""),
        "partition": str(config.get("partition") or ""),
        "gres": str(config.get("gres") or ""),
        "cpus_per_task": config.get("cpus_per_task") or 0,
        "time": str(config.get("time") or ""),
        "mem": str(config.get("mem") or ""),
    }
    host, cid, label = _match_host(alias, hosts)
    box["cluster_id"] = cid
    box["cluster_label"] = label
    if not job_id:
        return box
    if not job_id.isdigit():
        box["ok"] = False
        box["error"] = "状态里的作业号无效"
        return box
    box["job_id"] = job_id
    if not alias:
        box["error"] = "该沙盒使用本机 Slurm，控制台只读取 SSH 集群上的任务"
        return box
    if any(char in workdir for char in "\n\r\t\x00"):
        box["ok"] = False
        box["error"] = "remote_workdir 含有非法字符"
        return box
    box["query"] = {"host": host, "id": job_id, "workdir": workdir}
    return box


def _boxes_from_project(project: dict, hosts: tuple) -> list[dict]:
    """Expand one project into one box per allocation, with legacy support."""
    root = Path(str(project.get("root") or "")).expanduser()
    state_file = root / ".slurmbox" / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else None
    except (OSError, json.JSONDecodeError):
        return [_box_from_project(project, hosts)]
    if not isinstance(state, dict) or not isinstance(state.get("allocations"), list):
        return [_box_from_project(project, hosts, state)] if isinstance(state, dict) else [_box_from_project(project, hosts)]
    allocations = [item for item in state["allocations"] if isinstance(item, dict)]
    if not allocations:
        return [_box_from_project(project, hosts, state)]
    boxes = []
    for item in allocations:
        merged = dict(state)
        merged.update(item)
        boxes.append(_box_from_project(project, hosts, merged))
    return boxes


SANDBOX_REMOTE = r"""
import base64, json, os, re, subprocess

def run(args, timeout=20):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as exc:
        return 1, "", str(exc)

def expand(path):
    path = path or ""
    if path == "~":
        return os.path.expanduser("~")
    if path.startswith("~/"):
        return os.path.join(os.path.expanduser("~"), path[2:])
    return path

try:
    jobs = json.loads(base64.b64decode(os.environ.get("SLURMBOX_JOBS_B64") or ""))
except Exception:
    jobs = []
if not isinstance(jobs, list):
    jobs = []

SEP = "\x1f"
out = {"jobs": {}}
for job in jobs:
    if not isinstance(job, dict):
        continue
    jid = str(job.get("id") or "")
    item = {"queue": "", "sacct": "", "steps": "", "commands": [], "log": [], "step_logs": {}, "gpu": "", "gpu_error": "", "error": ""}
    if not re.match(r"^\d+$", jid):
        item["error"] = "invalid job id"
        out["jobs"][jid or "?"] = item
        continue
    rc, text, err = run(["squeue", "-h", "-j", jid, "-o",
                         "%i" + SEP + "%T" + SEP + "%M" + SEP + "%l" + SEP + "%R" + SEP + "%N" + SEP + "%P" + SEP + "%j"], 15)
    item["queue"] = text
    if rc != 0 and not text:
        item["error"] = (err or "squeue failed")[:240]
    rc, text, err = run(["sacct", "-j", jid, "-n", "-P",
                         "--format=JobID,JobName,State,Elapsed,ExitCode,End,NodeList"], 20)
    item["sacct"] = text
    if rc != 0 and not text and not item["error"]:
        item["error"] = (err or "sacct failed")[:240]
    rc, text, err = run(["squeue", "-s", "-h", "-j", jid, "-o", "%i" + SEP + "%j" + SEP + "%M" + SEP + "%N"], 15)
    if rc == 0:
        item["steps"] = text
    shown = 0
    for line in (item["steps"] or "").splitlines():
        step = line.split(SEP, 1)[0].strip()
        if not re.match(r"^\d+\.\d+$", step):
            continue
        if shown >= 8:
            break
        shown += 1
        rc, detail, err = run(["scontrol", "-o", "show", "step", step], 10)
        if detail.strip():
            item["commands"].append(detail.strip().splitlines()[0][:500])
    def tail_file(path, limit):
        try:
            if os.path.isfile(path) and os.path.getsize(path) < 30000000:
                rc, tail, err = run(["tail", "-n", str(limit), path], 8)
                return [ln[:240] for ln in tail.splitlines() if ln.strip()][-limit:]
        except OSError:
            return []
        return []

    def field(line, key):
        match = re.search(r"(?:^|\s)%s=(\S+)" % re.escape(key), line or "")
        return match.group(1) if match else ""

    rc, detail, err = run(["scontrol", "-o", "show", "job", jid], 15)
    stdout = ""
    if detail.strip():
        stdout = field(detail.strip().splitlines()[0], "StdOut")
    if stdout.startswith("/"):
        item["log"] = tail_file(stdout, 20)
    workdir = expand(str(job.get("workdir") or ""))
    if not item["log"] and workdir:
        item["log"] = tail_file(os.path.join(workdir, "slurmbox-" + jid + ".out"), 20)
    log_dirs = []
    if workdir:
        log_dirs.append(os.path.join(workdir, "steps"))
    job_dir = field(detail.strip().splitlines()[0], "WorkDir") if detail.strip() else ""
    if job_dir.startswith("/"):
        extra = os.path.join(job_dir, "steps")
        if extra not in log_dirs:
            log_dirs.append(extra)
    logs = {}
    stamps = {}
    for step_dir in log_dirs:
        try:
            names = os.listdir(step_dir) if os.path.isdir(step_dir) else []
        except OSError:
            names = []
        for fn in names:
            if not (fn.startswith(jid + ".") and fn.endswith(".log")):
                continue
            path = os.path.join(step_dir, fn)
            try:
                stamp = os.path.getmtime(path)
            except OSError:
                continue
            key = fn[:-4]
            if key in stamps and stamp < stamps[key]:
                continue
            stamps[key] = stamp
            logs[key] = tail_file(path, 12)
    if len(logs) > 12:
        keep = sorted(stamps, key=lambda key: stamps[key])[-12:]
        logs = dict((key, logs[key]) for key in keep)
    item["step_logs"] = logs
    out["jobs"][jid] = item
print(json.dumps(out))
"""


def _query_sandbox_host(host: str, boxes: list[dict]) -> dict:
    jobs = [{"id": box["query"]["id"], "workdir": box["query"]["workdir"]} for box in boxes]
    payload = base64.b64encode(json.dumps(jobs).encode()).decode()
    script = (
        "export SLURMBOX_JOBS_B64=%s\n" % payload
        + "python3 - <<'SLURMBOX_PY'\n"
        + SANDBOX_REMOTE
        + "\nSLURMBOX_PY\n"
    ).encode()
    try:
        proc = _run_ssh(host, script, 75)
    except subprocess.TimeoutExpired:
        return {"error": "SSH 超时", "jobs": {}}
    except Exception as exc:
        return {"error": str(exc)[:240], "jobs": {}}
    raw = proc.stdout.decode("utf-8", "replace").strip()
    if not raw:
        err = proc.stderr.decode("utf-8", "replace").strip()
        return {"error": err[-400:] or "无输出", "jobs": {}}
    try:
        data = json.loads(raw.splitlines()[-1])
    except json.JSONDecodeError:
        return {"error": raw[-400:], "jobs": {}}
    if not isinstance(data, dict):
        return {"error": "返回格式无效", "jobs": {}}
    jobs_out = data.get("jobs")
    if not isinstance(jobs_out, dict):
        data["jobs"] = {}
    return data


def _collect_sandboxes() -> dict:
    hosts, _links = load_settings()
    boxes = [box for project in load_slurmbox_projects() for box in _boxes_from_project(project, hosts)]
    groups: dict[str, list[dict]] = {}
    for box in boxes:
        query = box.get("query")
        if query and query.get("host"):
            groups.setdefault(query["host"], []).append(box)
    if groups:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(groups)) as pool:
            futures = [pool.submit(_query_sandbox_host, host, items) for host, items in groups.items()]
            results = {host: future.result() for (host, _items), future in zip(groups.items(), futures)}
        for box in boxes:
            query = box.get("query")
            if not query:
                continue
            host_result = results.get(query["host"]) or {"error": "无返回", "jobs": {}}
            raw_job = (host_result.get("jobs") or {}).get(query["id"]) or {}
            if host_result.get("error") and not raw_job:
                raw_job = {"error": host_result["error"]}
            parsed = parse_sandbox_job(raw_job)
            box["live"] = parsed["live"]
            box["steps"] = parsed["steps"]
            box["log"] = parsed["log"]
            box["gpus"] = parsed["gpus"]
            box["gpu_error"] = parsed["gpu_error"]
            if parsed["error"] and not box["error"]:
                box["error"] = parsed["error"]
                box["ok"] = False
    for box in boxes:
        box.pop("query", None)
    return {"fetched_at": time.time(), "sandboxes": boxes}


_sandbox_lock = threading.Lock()
_sandbox_cache: dict = {"t": 0.0, "data": None}


def sandbox_snapshot() -> dict:
    now = time.time()
    with _sandbox_lock:
        cached = _sandbox_cache["data"]
        if cached and now - _sandbox_cache["t"] < TTL_SEC:
            return cached
        data = _collect_sandboxes()
        _sandbox_cache["t"] = time.time()
        _sandbox_cache["data"] = data
        return data


def invalidate_sandbox_cache() -> None:
    with _sandbox_lock:
        _sandbox_cache["t"] = 0.0
        _sandbox_cache["data"] = None


_STEP_TARGET_RE = re.compile(r"^(\d+)\.(\d+)$")


def sandbox_cancel_target(job_id: str, step_id: str) -> str:
    job_id = str(job_id or "").strip()
    step_id = str(step_id or "").strip()
    match = _STEP_TARGET_RE.fullmatch(step_id)
    if not job_id.isdigit() or match is None or match.group(1) != job_id:
        raise ValueError("只能结束该沙盒分配里的用户步骤")
    return step_id


def sandbox_cancel_host(job_id: str) -> str:
    hosts, _links = load_settings()
    for project in load_slurmbox_projects():
        for box in _boxes_from_project(project, hosts):
            if box.get("job_id") == str(job_id) and box.get("ok") and box.get("query"):
                return str(box["query"]["host"])
    raise ValueError("这个作业不是已配置沙盒里的分配")


def cancel_sandbox_step(job_id: str, step_id: str) -> dict:
    step = sandbox_cancel_target(job_id, step_id)
    host = sandbox_cancel_host(job_id)
    try:
        proc = _run_ssh(host, ("scancel " + step + "\n").encode(), 25)
    except subprocess.TimeoutExpired:
        raise ValueError("取消超时") from None
    detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        raise ValueError((detail or "scancel 失败")[:240])
    invalidate_sandbox_cache()
    return {"ok": True, "job_id": str(job_id).strip(), "step_id": step}
