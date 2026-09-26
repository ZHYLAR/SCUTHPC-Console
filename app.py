from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

import collector

app = FastAPI(title="SCUTHPC Console")
ROOT = Path(__file__).resolve().parent


@app.get("/api/snapshot")
def snapshot():
    return collector.snapshot()


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
