"""Upload route handlers."""
from __future__ import annotations

import logging
import math
import time
import uuid
import asyncio
from pathlib import Path

import orjson
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..auth import basic_auth
from ..celery_app import celery_app, group
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

_VALID_VIDEO_CODECS = frozenset({
    "av1_nvenc", "hevc_nvenc", "h264_nvenc",
    "av1_qsv", "hevc_qsv", "h264_qsv",
    "av1_vaapi", "hevc_vaapi", "h264_vaapi",
    "av1_amf", "hevc_amf", "h264_amf",
    "libx264", "libx265", "libsvtav1", "libaom-av1",
})
_VALID_AUDIO_CODECS = frozenset({"libopus", "aac", "none"})
_VALID_PRESETS = frozenset({"p1", "p2", "p3", "p4", "p5", "p6", "p7", "extraquality"})
_VALID_CONTAINERS = frozenset({"mp4", "mkv"})
_VALID_TUNES = frozenset({"hq", "ll", "ull", "lossless"})


def _validate_batch_options(
    video_codec: str,
    audio_codec: str,
    preset: str,
    container: str,
    tune: str,
    target_size_mb: float,
    audio_bitrate_kbps: int,
    max_width: int | None,
    max_height: int | None,
    min_auto_resolution: int,
    target_resolution: int | None,
    target_video_bitrate_kbps: float | None,
    max_output_fps: float | None,
) -> None:
    """Apply the same finite/range checks to multipart batch fields as JSON jobs."""
    if video_codec not in _VALID_VIDEO_CODECS:
        raise HTTPException(status_code=422, detail="Unsupported video_codec")
    if audio_codec not in _VALID_AUDIO_CODECS:
        raise HTTPException(status_code=422, detail="Unsupported audio_codec")
    if preset not in _VALID_PRESETS:
        raise HTTPException(status_code=422, detail="Unsupported preset")
    if container not in _VALID_CONTAINERS:
        raise HTTPException(status_code=422, detail="Unsupported container")
    if tune not in _VALID_TUNES:
        raise HTTPException(status_code=422, detail="Unsupported tune")
    if not math.isfinite(target_size_mb) or target_size_mb <= 0 or target_size_mb > 51200:
        raise HTTPException(status_code=422, detail="target_size_mb must be between 0 and 51200")
    if audio_bitrate_kbps < 0 or audio_bitrate_kbps > 2000:
        raise HTTPException(status_code=422, detail="audio_bitrate_kbps must be between 0 and 2000")

    for name, value in (("max_width", max_width), ("max_height", max_height), ("min_auto_resolution", min_auto_resolution), ("target_resolution", target_resolution)):
        if value is not None and (value <= 0 or value > 16384):
            raise HTTPException(status_code=422, detail=f"{name} must be between 1 and 16384")
    if target_video_bitrate_kbps is not None and (
        not math.isfinite(target_video_bitrate_kbps)
        or target_video_bitrate_kbps < 0
        or target_video_bitrate_kbps > 2_000_000
    ):
        raise HTTPException(status_code=422, detail="target_video_bitrate_kbps must be between 0 and 2000000")
    if max_output_fps is not None and (
        not math.isfinite(max_output_fps) or max_output_fps < 0 or max_output_fps > 1000
    ):
        raise HTTPException(status_code=422, detail="max_output_fps must be between 0 and 1000")


@router.post("/api/upload", response_model=UploadResponse, dependencies=[Depends(basic_auth)])
async def upload(
    file: UploadFile = File(...),
    target_size_mb: float = Form(19.7),
    audio_bitrate_kbps: int = Form(128),
):
    if not math.isfinite(target_size_mb) or target_size_mb <= 0 or target_size_mb > 51200:
        raise HTTPException(status_code=422, detail="target_size_mb must be between 0 and 51200")
    if audio_bitrate_kbps < 0 or audio_bitrate_kbps > 2000:
        raise HTTPException(status_code=422, detail="audio_bitrate_kbps must be between 0 and 2000")

    job_id = str(uuid.uuid4())
    safe_name = safe_filename(file.filename)
    dest = UPLOADS_DIR / f"{job_id}_{safe_name}"
    dest = await save_upload_file(file, dest, allow_dynamic_storage=True)

    try:
        saved_bytes = dest.stat().st_size
    except OSError:
        saved_bytes = -1
    logger.info(
        "upload: job_id=%s filename=%r size=%s bytes target_mb=%s audio_kbps=%s",
        job_id, file.filename, saved_bytes, target_size_mb, audio_bitrate_kbps,
    )
    logger.debug("upload: saved %s (%d bytes) — probing with ffprobe", dest.name, saved_bytes)

    try:
        info = await asyncio.to_thread(ffprobe, dest)
    except Exception as exc:
        # Do not leave an unprocessable upload behind until the retention
        # scheduler runs. This is especially important for large files and
        # for clients retrying after a malformed/partial upload.
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            logger.warning("upload: failed to remove invalid input %s", dest)
        logger.warning("upload: ffprobe failed for job_id=%s: %s", job_id, exc)
        raise HTTPException(status_code=400, detail="Unable to analyze the uploaded video") from exc
    logger.debug(
        "upload: ffprobe job_id=%s duration=%.2fs %sx%s v_kbps=%s a_kbps=%s fps=%s",
        job_id, info.get("duration", 0.0),
        info.get("width"), info.get("height"),
        info.get("video_bitrate_kbps"), info.get("audio_bitrate_kbps"),
        info.get("video_fps"),
    )
    total_kbps, video_kbps, warn = calc_bitrates(target_size_mb, info["duration"], audio_bitrate_kbps)
    if warn:
        logger.warning(
            "upload: low-quality warning for job_id=%s target_mb=%s duration=%.2fs -> total=%.1fkbps video=%.1fkbps",
            job_id, target_size_mb, info["duration"], total_kbps, video_kbps,
        )
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
    target_size_mb: float = Form(19.7),
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
    _validate_batch_options(
        video_codec, audio_codec, preset, container, tune,
        target_size_mb, audio_bitrate_kbps, max_width, max_height,
        min_auto_resolution, target_resolution, target_video_bitrate_kbps,
        max_output_fps,
    )

    batch_id = str(uuid.uuid4())
    batch_items: list[dict] = []
    signatures = []
    saved_files: list[Path] = []

    try:
        for index, upload_file in enumerate(files):
            original_filename = upload_file.filename or f"file_{index + 1}"
            safe_name = safe_filename(original_filename)

            job_id = str(uuid.uuid4())
            if not is_video_upload(upload_file):
                batch_items.append({
                    "index": index,
                    "job_id": job_id,
                    "task_id": "",
                    "original_filename": original_filename,
                    "stored_filename": "",
                    "output_filename": "",
                    "output_path": None,
                    "state": "failed",
                    "progress": 100.0,
                    "error": "Unsupported file type",
                    "download_url": "",
                })
                continue

            stored_filename = f"{job_id}_{safe_name}"
            input_path = UPLOADS_DIR / stored_filename
            input_path = await save_upload_file(upload_file, input_path, allow_dynamic_storage=True)
            saved_files.append(input_path)

            try:
                await asyncio.to_thread(ffprobe, input_path)
            except Exception:
                # A corrupt item should not discard valid files in the same
                # batch. Keep a terminal item for the UI, but remove the bad
                # upload immediately and do not dispatch a task for it.
                try:
                    input_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("batch: failed to remove invalid input %s", input_path)
                saved_files.remove(input_path)
                batch_items.append({
                    "index": index,
                    "job_id": job_id,
                    "task_id": "",
                    "original_filename": original_filename,
                    "stored_filename": stored_filename,
                    "output_filename": "",
                    "output_path": None,
                    "state": "failed",
                    "progress": 100.0,
                    "error": "Unable to analyze uploaded video",
                    "download_url": "",
                })
                continue

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
                transient_input=True,
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

            await store_job_metadata(task_id, job_id, stored_filename, target_size_mb, video_codec, str(input_path), str(output_path))

            try:
                await redis.publish(
                    f"progress:{task_id}",
                    orjson.dumps({"type": "log", "message": f"Batch queued ({index + 1}/{len(files)})"}).decode(),
                )
            except Exception:
                pass
    except Exception:
        for saved in saved_files:
            try:
                saved.unlink(missing_ok=True)
            except Exception:
                pass
        # A later file can fail ffprobe after earlier items already wrote
        # queue metadata. Remove those records too; otherwise the queue page
        # shows orphaned jobs that were never dispatched.
        for item in batch_items:
            task_id = str(item.get("task_id") or "")
            if not task_id:
                continue
            try:
                await redis.delete(f"job:{task_id}")
                await redis.zrem("jobs:active", task_id)
            except Exception:
                pass
        raise

    if not signatures:
        raise HTTPException(status_code=400, detail="No valid video files to process")

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
        "execution": "parallel",
        "items": batch_items,
    }

    # Persist the parent record before publishing any tasks. If Redis cannot
    # record the batch, no worker is allowed to start and all staging data can
    # still be rolled back safely.
    try:
        await redis.setex(
            f"batch:{batch_id}", BATCH_TTL_SECONDS,
            orjson.dumps(batch_payload).decode(),
        )
    except Exception as e:
        for saved in saved_files:
            try:
                saved.unlink(missing_ok=True)
            except OSError:
                pass
        for item in batch_items:
            task_id = str(item.get("task_id") or "")
            if task_id:
                try:
                    await redis.delete(f"job:{task_id}")
                    await redis.zrem("jobs:active", task_id)
                except Exception:
                    pass
        raise HTTPException(status_code=500, detail=f"Failed to persist batch: {e}")

    try:
        # Each item is independent. A chain made every file wait for the
        # previous file and also caused one failure to skip the rest, even
        # when the worker had spare concurrency. A group preserves the
        # per-item task IDs while letting Celery schedule items in parallel.
        group(*signatures).apply_async()
    except Exception as e:
        # A broker can accept part of a group before raising. Revoke every
        # preassigned task ID and retain its metadata/staging file until the
        # normal cleanup path confirms workers have stopped. This avoids both
        # racing a live FFmpeg process and creating invisible orphan jobs.
        for item in batch_items:
            task_id = str(item.get("task_id") or "")
            if not task_id:
                continue
            try:
                await redis.set(f"cancel:{task_id}", "1", ex=3600)
                celery_app.control.revoke(task_id, terminate=False)
            except Exception:
                pass
            item["state"] = "failed"
            item["progress"] = 100.0
            item["error"] = "Batch dispatch failed; cancellation requested"
            item["download_url"] = ""
        batch_payload["state"] = "failed"
        batch_payload["items"] = batch_items
        try:
            await redis.setex(
                f"batch:{batch_id}", BATCH_TTL_SECONDS,
                orjson.dumps(batch_payload).decode(),
            )
        except Exception:
            logger.exception("batch: failed to record dispatch failure batch_id=%s", batch_id)
        raise HTTPException(
            status_code=503,
            detail={"message": f"Failed to enqueue batch: {e}", "batch_id": batch_id},
        )

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
