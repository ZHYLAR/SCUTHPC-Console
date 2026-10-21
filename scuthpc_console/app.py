from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import collector, sandbox_service

app = FastAPI(title="SCUTHPC Console")
ROOT = Path(__file__).resolve().parent.parent


@app.get("/api/snapshot")
def snapshot():
    return collector.snapshot()


@app.get("/api/gpu")
def gpu():
    return collector.gpu_sample()


@app.get("/api/sandbox")
def sandbox():
    return collector.sandbox_snapshot()


@app.get("/api/sandbox/options")
def sandbox_options():
    return collector.sandbox_options()


@app.post("/api/sandbox/request")
async def request_sandbox(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "请求无效"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "请求无效"}, status_code=400)
    try:
        options = collector.sandbox_options()
        clusters = {str(item["id"]): item for item in options["clusters"]}
        cluster_id = str(body.get("cluster_id") or "")
        cluster = clusters.get(cluster_id)
        if not cluster:
            raise ValueError("集群选择无效")
        projects = options["projects"]
        if not projects:
            raise ValueError("请先在 config.json 的 slurmbox.projects 配置项目目录")
        wanted = str(body.get("project") or "")
        project = next((p for p in projects if p["root"] == wanted or p["name"] == wanted), projects[0])
        gpus = int(body.get("gpus") or 1)
        if body.get("duration_minutes") not in (None, ""):
            duration_minutes = int(body.get("duration_minutes"))
        else:
            duration_minutes = int(body.get("duration_hours") or 8) * 60
        if gpus > int(cluster.get("max_gpus") or 8):
            raise ValueError(f"{cluster['label']} 单节点最多申请 {cluster['max_gpus']} 张卡")
        defaults = sandbox_service.project_request_defaults(Path(project["root"]), cluster["ssh_alias"])
        remote_workdir = str(project.get("remote_workdir") or defaults["remote_workdir"])
        result = sandbox_service.request_allocation(
            project_root=Path(project["root"]),
            ssh_alias=cluster["ssh_alias"],
            remote_workdir=remote_workdir,
            cluster_id=cluster_id,
            partition=cluster["partition"],
            gpus=gpus,
            duration_minutes=duration_minutes,
        )
        collector.invalidate_sandbox_cache()
        return result
    except (ValueError, RuntimeError, OSError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/sandbox/update-duration")
async def update_sandbox_duration(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求无效")
        job_id = str(body.get("job_id") or "")
        duration_minutes = int(body.get("duration_minutes") or 0)
        projects = collector.sandbox_options()["projects"]
        project = next((p for p in projects if str(p.get("root")) == str(body.get("project") or "")), None)
        if project is None:
            raise ValueError("项目选择无效")
        result = sandbox_service.update_allocation_time(
            project_root=Path(project["root"]), job_id=job_id, duration_minutes=duration_minutes
        )
        collector.invalidate_sandbox_cache()
        return result
    except (ValueError, RuntimeError, OSError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/sandbox/cancel")
async def cancel_sandbox(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "请求无效"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "请求无效"}, status_code=400)
    try:
        return collector.cancel_sandbox_step(str(body.get("job_id") or ""), str(body.get("step_id") or ""))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
