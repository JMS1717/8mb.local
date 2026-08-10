"""Download and job-status route handlers."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
import zipfile
from pathlib import Path

import orjson
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import basic_auth
from ..celery_app import celery_app
from ..deps import (
    OUTPUTS_DIR,
    build_output_name,
    load_batch_payload,
    redis,
    refresh_batch_payload,
)
from ..models import StatusResponse
from .. import history_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["download"])


def _media_type_for_path(path: Path) -> str:
    """Return a useful content type for every output container we create."""
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".m4a"}:
        return "audio/mp4" if suffix == ".m4a" else "video/mp4"
    if suffix == ".mkv":
        return "video/x-matroska"
    return "application/octet-stream"


def _safe_output_path(value: object) -> Path | None:
    """Resolve an internal output reference only if it stays under outputs/."""
    if not isinstance(value, (str, os.PathLike)):
        return None
    try:
        root = OUTPUTS_DIR.resolve()
        candidate = Path(value).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate if candidate.is_file() else None


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
    path = _safe_output_path(meta.get("output_path"))
    if not path:
        try:
            cached = await redis.get(f"ready:{task_id}")
            if cached:
                path = _safe_output_path(cached)
        except Exception:
            pass

    if wait and path is None:
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
                    path = _safe_output_path(p2)
            except Exception:
                pass
            if not path:
                try:
                    cached = await redis.get(f"ready:{task_id}")
                    if cached:
                        path = _safe_output_path(cached)
                except Exception:
                    pass
            if path is not None:
                break
            await asyncio.sleep(0.2)

    if path is not None:
        filename = os.path.basename(path)
        return FileResponse(path, filename=filename, media_type=_media_type_for_path(path))

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
            output_filename = entry.get("output_filename")
            if output_filename:
                candidate = _safe_output_path(OUTPUTS_DIR / Path(str(output_filename)).name)
            else:
                container = (entry.get("container") or "mp4").lower()
                candidate = _safe_output_path(
                    OUTPUTS_DIR / build_output_name(
                        Path(uploaded_name), task_id, container,
                    )
                )
            if candidate is not None:
                filename = os.path.basename(candidate)
                return FileResponse(str(candidate), filename=filename, media_type=_media_type_for_path(candidate))
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
    try:
        # Batch IDs are UUIDs generated by the upload endpoint. Rejecting any
        # other form keeps the ZIP path deterministic and filesystem-safe.
        uuid.UUID(batch_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Batch not found")

    batch_payload = await load_batch_payload(batch_id)
    batch_payload = await refresh_batch_payload(batch_payload)

    files_to_zip: list[Path] = []
    for item in (batch_payload.get("items") or []):
        output_path = _safe_output_path(item.get("output_path"))
        if output_path is not None:
            files_to_zip.append(output_path)

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
