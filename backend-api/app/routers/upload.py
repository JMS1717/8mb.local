"""Upload route handlers."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

import orjson
from celery import chain
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..auth import basic_auth
from ..celery_app import celery_app
from ..deps import (
    BATCH_TTL_SECONDS,
    MAX_BATCH_FILES,
    OUTPUTS_DIR,
    UPLOADS_DIR,
    build_output_name,
    calc_bitrates,
    ffprobe,
    is_video_upload,
    load_batch_payload,
    redis,
    refresh_batch_payload,
    safe_filename,
    save_upload_file,
    store_job_metadata,
)
from ..models import (
    BatchCreateResponse,
    BatchItemStatus,
    BatchStatusResponse,
    UploadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["upload"])


@router.post("/api/upload", response_model=UploadResponse, dependencies=[Depends(basic_auth)])
async def upload(file: UploadFile = File(...), target_size_mb: float = 25.0, audio_bitrate_kbps: int = 128):
    job_id = str(uuid.uuid4())
    safe_name = safe_filename(file.filename)
    dest = UPLOADS_DIR / f"{job_id}_{safe_name}"
    await save_upload_file(file, dest)
    
    info = ffprobe(dest)
    total_kbps, video_kbps, warn = calc_bitrates(target_size_mb, info["duration"], audio_bitrate_kbps)
    return UploadResponse(
        job_id=job_id,
        filename=dest.name,
        duration_s=info["duration"],
        original_video_bitrate_kbps=info["video_bitrate_kbps"],
        original_audio_bitrate_kbps=info["audio_bitrate_kbps"],
        original_width=info.get("width"),
        original_height=info.get("height"),
        original_video_fps=info.get("video_fps"),
        estimate_total_kbps=total_kbps,
        estimate_video_kbps=video_kbps,
        warn_low_quality=warn,
    )


@router.post("/api/batches/upload", response_model=BatchCreateResponse, dependencies=[Depends(basic_auth)])
async def upload_batch(
    files: list[UploadFile] = File(...),
    target_size_mb: float = Form(25.0),
    video_codec: str = Form("av1_nvenc"),
    audio_codec: str = Form("libopus"),
    audio_bitrate_kbps: int = Form(128),
    preset: str = Form("p6"),
    container: str = Form("mp4"),
    tune: str = Form("hq"),
    max_width: int | None = Form(None),
    max_height: int | None = Form(None),
    start_time: str | None = Form(None),
    end_time: str | None = Form(None),
    force_hw_decode: bool = Form(False),
    fast_mp4_finalize: bool = Form(False),
    auto_resolution: bool = Form(False),
    min_auto_resolution: int = Form(240),
    target_resolution: int | None = Form(None),
    audio_only: bool = Form(False),
    target_video_bitrate_kbps: float | None = Form(None),
    max_output_fps: float | None = Form(None),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"Batch too large. Max files: {MAX_BATCH_FILES}")

    accepted_files = [f for f in files if is_video_upload(f)]
    if not accepted_files:
        raise HTTPException(status_code=400, detail="No video files found in upload")

    batch_id = str(uuid.uuid4())
    batch_items: list[dict] = []
    signatures = []
    saved_files: list[Path] = []

    try:
        for index, upload_file in enumerate(accepted_files):
            original_filename = upload_file.filename or f"file_{index + 1}"
            safe_name = safe_filename(original_filename)

            job_id = str(uuid.uuid4())
            stored_filename = f"{job_id}_{safe_name}"
            input_path = UPLOADS_DIR / stored_filename
            await save_upload_file(upload_file, input_path)
            saved_files.append(input_path)

            task_id = str(uuid.uuid4())
            output_name = build_output_name(input_path, task_id, container, bool(audio_only))
            output_path = OUTPUTS_DIR / output_name

            kwargs = dict(
                job_id=job_id,
                input_path=str(input_path),
                output_path=str(output_path),
                target_size_mb=target_size_mb,
                video_codec=video_codec,
                audio_codec=audio_codec,
                audio_bitrate_kbps=audio_bitrate_kbps,
                preset=preset,
                tune=tune,
                max_width=max_width,
                max_height=max_height,
                start_time=start_time,
                end_time=end_time,
                force_hw_decode=bool(force_hw_decode),
                fast_mp4_finalize=bool(fast_mp4_finalize),
                auto_resolution=bool(auto_resolution),
                min_auto_resolution=min_auto_resolution,
                target_resolution=target_resolution,
                audio_only=bool(audio_only),
                target_video_bitrate_kbps=target_video_bitrate_kbps,
                max_output_fps=max_output_fps,
            )

            signatures.append(
                celery_app.signature(
                    "worker.worker.compress_video",
                    kwargs=kwargs,
                    immutable=True,
                ).set(task_id=task_id)
            )

            item = {
                "index": index,
                "job_id": job_id,
                "task_id": task_id,
                "original_filename": original_filename,
                "stored_filename": stored_filename,
                "output_filename": output_name,
                "output_path": str(output_path),
                "state": "queued",
                "progress": 0.0,
                "error": None,
                "download_url": f"/api/jobs/{task_id}/download",
            }
            batch_items.append(item)

            await store_job_metadata(task_id, job_id, stored_filename, target_size_mb, video_codec)

            try:
                await redis.publish(
                    f"progress:{task_id}",
                    orjson.dumps({"type": "log", "message": f"Batch queued ({index + 1}/{len(accepted_files)})"}).decode(),
                )
            except Exception:
                pass
    except Exception:
        for saved in saved_files:
            try:
                saved.unlink(missing_ok=True)
            except Exception:
                pass
        raise

    if not signatures:
        raise HTTPException(status_code=400, detail="No valid video files to process")

    try:
        chain(*signatures).apply_async()
    except Exception as e:
        for saved in saved_files:
            try:
                saved.unlink(missing_ok=True)
            except Exception:
                pass
        for item in batch_items:
            task_id = str(item.get("task_id") or "")
            if not task_id:
                continue
            try:
                await redis.delete(f"job:{task_id}")
                await redis.zrem("jobs:active", task_id)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to enqueue batch: {e}")

    batch_payload = {
        "batch_id": batch_id,
        "state": "queued",
        "created_at": time.time(),
        "item_count": len(batch_items),
        "target_size_mb": target_size_mb,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "audio_bitrate_kbps": audio_bitrate_kbps,
        "preset": preset,
        "container": container,
        "tune": tune,
        "zip_download_url": f"/api/batches/{batch_id}/download.zip",
        "items": batch_items,
    }
    await redis.setex(f"batch:{batch_id}", BATCH_TTL_SECONDS, orjson.dumps(batch_payload).decode())

    return BatchCreateResponse(
        batch_id=batch_id,
        item_count=len(batch_items),
        state="queued",
        items=[BatchItemStatus(**item) for item in batch_items],
    )


@router.get("/api/batches/{batch_id}/status", response_model=BatchStatusResponse, dependencies=[Depends(basic_auth)])
async def get_batch_status(batch_id: str):
    batch_payload = await load_batch_payload(batch_id)
    batch_payload = await refresh_batch_payload(batch_payload)
    await redis.setex(f"batch:{batch_id}", BATCH_TTL_SECONDS, orjson.dumps(batch_payload).decode())

    items = [BatchItemStatus(**item) for item in (batch_payload.get("items") or [])]
    completed_count = int(batch_payload.get("completed_count") or 0)
    item_count = int(batch_payload.get("item_count") or len(items))
    zip_url = None
    if item_count > 0 and completed_count > 0:
        zip_url = f"/api/batches/{batch_id}/download.zip"

    return BatchStatusResponse(
        batch_id=batch_id,
        state=str(batch_payload.get("state") or "queued"),
        item_count=item_count,
        queued_count=int(batch_payload.get("queued_count") or 0),
        running_count=int(batch_payload.get("running_count") or 0),
        completed_count=completed_count,
        failed_count=int(batch_payload.get("failed_count") or 0),
        overall_progress=float(batch_payload.get("overall_progress") or 0.0),
        items=items,
        zip_download_url=zip_url,
    )
