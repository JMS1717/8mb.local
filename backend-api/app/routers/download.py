"""Download and job-status route handlers."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import zipfile
from pathlib import Path

import orjson
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import basic_auth
from ..celery_app import celery_app
from ..deps import (
    OUTPUTS_DIR,
    load_batch_payload,
    redis,
    refresh_batch_payload,
)
from ..models import StatusResponse
from .. import history_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["download"])


@router.get("/api/jobs/{task_id}/status", response_model=StatusResponse, dependencies=[Depends(basic_auth)])
async def job_status(task_id: str):
    res = celery_app.AsyncResult(task_id)
    state = res.state
    meta = res.info if isinstance(res.info, dict) else {}
    return StatusResponse(state=state, progress=meta.get("progress"), detail=meta.get("detail"))


@router.get("/api/jobs/{task_id}/download", dependencies=[Depends(basic_auth)])
async def download(task_id: str, wait: float | None = None):
    res = celery_app.AsyncResult(task_id)
    state = res.state or "UNKNOWN"
    meta = res.info if isinstance(res.info, dict) else {}
    path = meta.get("output_path")
    if not path:
        try:
            cached = await redis.get(f"ready:{task_id}")
            if cached:
                path = cached
        except Exception:
            pass

    if wait and (not path or not os.path.isfile(str(path))):
        try:
            deadline = time.time() + max(0.1, min(float(wait), 5.0))
        except Exception:
            deadline = time.time() + 1.0
        while time.time() < deadline:
            try:
                res = celery_app.AsyncResult(task_id)
                meta = res.info if isinstance(res.info, dict) else meta
                p2 = (meta or {}).get("output_path")
                if p2:
                    path = p2
            except Exception:
                pass
            if not path:
                try:
                    cached = await redis.get(f"ready:{task_id}")
                    if cached:
                        path = cached
                except Exception:
                    pass
            if path and os.path.isfile(str(path)):
                break
            await asyncio.sleep(0.2)

    if path and os.path.isfile(path):
        filename = os.path.basename(path)
        media_type = "video/mp4" if filename.lower().endswith(".mp4") else "video/x-matroska"
        return FileResponse(path, filename=filename, media_type=media_type)

    try:
        entry = history_manager.get_history_entry(task_id)
    except AttributeError:
        entry = None
        try:
            for e in history_manager.get_history(limit=200):
                if e.get("task_id") == task_id:
                    entry = e
                    break
        except Exception:
            entry = None
    except Exception:
        entry = None

    if entry:
        try:
            uploaded_name = entry.get("filename") or ""
            container = (entry.get("container") or "mp4").lower()
            ext = ".mp4" if container == "mp4" else ".mkv"
            stem = Path(uploaded_name).stem
            if len(stem) > 37 and len(stem) >= 37 and stem[36] == '_':
                stem = stem[37:]
            output_name = stem + "_8mblocal" + ext
            candidate = OUTPUTS_DIR / output_name
            if candidate.is_file():
                filename = os.path.basename(candidate)
                media_type = "video/mp4" if filename.lower().endswith(".mp4") else "video/x-matroska"
                return FileResponse(str(candidate), filename=filename, media_type=media_type)
        except Exception:
            pass

    detail = {
        "error": "file_not_ready",
        "state": state,
    }
    if isinstance(meta, dict):
        if "progress" in meta:
            detail["progress"] = meta.get("progress")
        if "detail" in meta:
            detail["detail"] = meta.get("detail")
        if meta.get("output_path"):
            detail["expected_path"] = meta.get("output_path")
    try:
        cached = await redis.get(f"ready:{task_id}")
        if cached and not os.path.isfile(cached):
            detail["ready_cache"] = "present_but_missing_file"
        elif cached:
            detail["ready_cache"] = "present"
        else:
            detail["ready_cache"] = "absent"
    except Exception:
        pass

    headers = {"Retry-After": "1", "Cache-Control": "no-store"}
    raise HTTPException(status_code=404, detail=detail, headers=headers)


@router.get("/api/batches/{batch_id}/download.zip", dependencies=[Depends(basic_auth)])
async def download_batch_zip(batch_id: str):
    batch_payload = await load_batch_payload(batch_id)
    batch_payload = await refresh_batch_payload(batch_payload)

    files_to_zip: list[Path] = []
    for item in (batch_payload.get("items") or []):
        output_path = item.get("output_path")
        if output_path and Path(output_path).is_file():
            files_to_zip.append(Path(output_path))

    if not files_to_zip:
        raise HTTPException(status_code=404, detail="No completed files available for zip download")

    zip_path = OUTPUTS_DIR / f"batch_{batch_id}.zip"
    seen_names: set[str] = set()
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for src in files_to_zip:
            arcname = src.name
            if arcname in seen_names:
                stem = src.stem
                suffix = src.suffix
                n = 2
                while f"{stem}_{n}{suffix}" in seen_names:
                    n += 1
                arcname = f"{stem}_{n}{suffix}"
            seen_names.add(arcname)
            archive.write(src, arcname=arcname)

    filename = f"8mblocal_batch_{batch_id[:8]}.zip"
    return FileResponse(str(zip_path), filename=filename, media_type="application/zip")
