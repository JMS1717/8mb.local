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


def _write_batch_zip(files_to_zip: list[Path], temporary_zip_path: Path, zip_path: Path) -> None:
    """Build and atomically publish a batch archive outside the event loop."""
    seen_names: set[str] = set()
    try:
        with zipfile.ZipFile(temporary_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
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
        # Publish a complete archive atomically so concurrent downloads never
        # receive a partially-written ZIP.
        os.replace(temporary_zip_path, zip_path)
    except Exception:
        try:
            temporary_zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@router.get("/api/jobs/{task_id}/status", response_model=StatusResponse, dependencies=[Depends(basic_auth)])
async def job_status(task_id: str):
    res = celery_app.AsyncResult(task_id)
    try:
        state = res.state
        meta = res.info if isinstance(res.info, dict) else {}
    except (KeyError, TypeError, ValueError) as exc:
        # Older/corrupted Celery result records can contain a plain dict in a
        # REVOKED/FAILURE result slot where Celery expects exception metadata.
        # A cancellation flag plus the durable job record is enough to expose
        # a safe terminal cancellation instead of returning HTTP 500 forever.
        cancel_requested = False
        try:
            cancel_requested = str(await redis.get(f"cancel:{task_id}")) == "1"
        except Exception:
            pass
        if not cancel_requested:
            raise
        logger.warning("Using durable canceled state for undecodable Celery result %s: %s", task_id, exc)
        state = "CANCELED"
        meta = {}
    # The worker mirrors encoder telemetry into the existing durable queue
    # record. Prefer that snapshot over an older Celery result metadata value
    # so a runtime fallback cannot be hidden by stale progress state.
    telemetry_keys = (
        "requested_encoder", "resolved_encoder", "actual_encoder",
        "hardware_used", "hardware_type", "hardware_device", "render_device",
        "fallback_occurred", "fallback_stage", "fallback_reason", "decoder",
    )
    try:
        durable_raw = await redis.get(f"job:{task_id}")
        durable = orjson.loads(durable_raw) if durable_raw else {}
        if isinstance(durable, dict):
            meta = dict(meta)
            for key in telemetry_keys:
                if durable.get(key) is not None:
                    meta[key] = durable[key]
    except Exception as exc:
        logger.debug("Could not merge durable telemetry for %s: %s", task_id[:8], exc)
    # Celery reports unknown task IDs as PENDING. Check the durable queue
    # record so a browser restored after a Redis/container reset does not treat
    # a random stale localStorage ID as an active job forever.
    if state == "PENDING":
        try:
            if not await redis.exists(f"job:{task_id}"):
                raise HTTPException(status_code=404, detail="Job no longer exists")
        except HTTPException:
            raise
        except Exception:
            # A temporary Redis outage is not proof that the task is stale.
            pass
    return StatusResponse(
        state=state,
        progress=meta.get("progress"),
        detail=meta.get("detail"),
        encoder=meta.get("encoder"),
        phase=meta.get("phase"),
        requested_encoder=meta.get("requested_encoder"),
        resolved_encoder=meta.get("resolved_encoder"),
        actual_encoder=meta.get("actual_encoder"),
        hardware_used=meta.get("hardware_used"),
        fallback_occurred=meta.get("fallback_occurred"),
        fallback_stage=meta.get("fallback_stage"),
        fallback_reason=meta.get("fallback_reason"),
        hardware_type=meta.get("hardware_type"),
        render_device=meta.get("render_device"),
        hardware_device=meta.get("hardware_device", meta.get("render_device")),
        decoder=meta.get("decoder"),
    )


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
    temporary_zip_path = OUTPUTS_DIR / f".batch_{batch_id}.{uuid.uuid4().hex}.tmp"
    await asyncio.to_thread(_write_batch_zip, files_to_zip, temporary_zip_path, zip_path)

    filename = f"8mblocal_batch_{batch_id[:8]}.zip"
    return FileResponse(str(zip_path), filename=filename, media_type="application/zip")
