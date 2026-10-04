"""photo-cull-app — local web UI for the photo-culling engine.

FastAPI backend that reuses cull.py as the engine. Point it at a folder of
photos, it scans + culls them (focus/sharpness/blur/exposure metrics, burst
grouping, keeper flags), and serves the results + thumbnails + a contact sheet
to the browser. Everything runs locally; no photos leave the machine.

Run:
    uvicorn app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cull

app = FastAPI(title="Photo Cull", version="0.1.0")

# Cache dir for generated thumbnails + contact sheets (cleared on each run).
CACHE = Path(tempfile.gettempdir()) / "photo-cull-cache"
CACHE.mkdir(exist_ok=True)


class CullRequest(BaseModel):
    path: str
    recursive: bool = False
    gap: float = 3.0
    min_focus_ratio: float = 0.35


def _clear_cache() -> None:
    for f in CACHE.iterdir():
        if f.is_file():
            f.unlink()


def _thumb_url(run_id: str, name: str) -> str:
    return f"/api/thumb/{run_id}/{name}"


@app.post("/api/cull")
def run_cull(req: CullRequest) -> JSONResponse:
    """Scan a folder, cull it, and return results + thumbnail URLs."""
    if not os.path.isdir(req.path):
        raise HTTPException(400, f"Not a directory: {req.path}")

    _clear_cache()
    run_id = uuid.uuid4().hex[:8]
    run_dir = CACHE / run_id
    run_dir.mkdir(exist_ok=True)

    files = cull.collect([req.path], req.recursive)
    if not files:
        raise HTTPException(400, "No supported image files found in that folder")

    recs = []
    for f in files:
        recs.append(cull.file_metrics(f))

    bad = [r for r in recs if "error" in r]
    groups = cull.cluster(recs, gap=req.gap)
    stats = cull.flag(groups, req.min_focus_ratio)
    stats["unreadable"] = len(bad)

    # Write thumbnails to the cache so the browser can load them.
    for g in groups:
        for r in g:
            t = r.get("_thumb")
            if t is None:
                continue
            stem = Path(r["name"]).stem  # strip original extension
            name = f"{stem}.jpg"
            cv2_ok = cull.cv2.imwrite(str(run_dir / name), t,
                                      [int(cull.cv2.IMWRITE_JPEG_QUALITY), 80])
            if cv2_ok:
                r["thumb"] = _thumb_url(run_id, name)

    # Contact sheet.
    sheet_name = "sheet.jpg"
    sheet_path = run_dir / sheet_name
    cull.contact_sheet(groups, str(sheet_path))
    sheet_url = _thumb_url(run_id, sheet_name) if sheet_path.exists() else None

    payload = {
        "run_id": run_id,
        "path": req.path,
        "stats": stats,
        "groups": [
            [{k: v for k, v in r.items() if not k.startswith("_")} for r in g]
            for g in groups
        ],
        "unreadable": bad,
        "sheet_url": sheet_url,
    }
    return JSONResponse(payload)


@app.get("/api/thumb/{run_id}/{name}")
def get_thumb(run_id: str, name: str) -> FileResponse:
    """Serve a cached thumbnail or contact sheet."""
    # Guard against path traversal.
    safe = Path(name).name
    p = CACHE / run_id / safe
    if not p.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Serve the static UI.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
