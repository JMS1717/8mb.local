import asyncio
import contextlib
import sys
import time
import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import AsyncGenerator

import orjson
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
import psutil

from .auth import basic_auth
from .config import settings
from .celery_app import celery_app
from .models import UploadResponse, CompressRequest, StatusResponse, AuthSettings, AuthSettingsUpdate, PasswordChange, DefaultPresets, AvailableCodecsResponse, CodecVisibilitySettings, PresetProfile, PresetProfilesResponse, SetDefaultPresetRequest, SizeButtons, RetentionHours, JobMetadata, QueueStatusResponse
from .cleanup import start_scheduler
from . import settings_manager
from . import history_manager

logger = logging.getLogger(__name__)

UPLOADS_DIR = Path("/app/uploads")
OUTPUTS_DIR = Path("/app/outputs")

app = FastAPI(title="8mb.local API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)

# Cache for one-time hardware detection and system capabilities
HW_INFO_CACHE: dict | None = None
SYSTEM_CAPS_CACHE: dict | None = None


def _get_hw_info_cached() -> dict:
    """Get hardware info from cache or compute once via worker."""
    global HW_INFO_CACHE
    if HW_INFO_CACHE is not None:
        return HW_INFO_CACHE
    try:
        result = celery_app.send_task("worker.worker.get_hardware_info")
        HW_INFO_CACHE = result.get(timeout=5) or {"type": "cpu", "available_encoders": {}}
    except Exception:
        HW_INFO_CACHE = {"type": "cpu", "available_encoders": {}}
    return HW_INFO_CACHE


def _get_hw_info_fresh(timeout: int = 10) -> dict:
    """Force-refresh hardware info from worker, updating cache if successful."""
    global HW_INFO_CACHE
    try:
        result = celery_app.send_task("worker.worker.get_hardware_info")
        info = result.get(timeout=timeout) or {"type": "cpu", "available_encoders": {}}
        # Update cache with fresh info
        HW_INFO_CACHE = info
        return info
    except Exception:
        # Return existing cache if present, else CPU fallback
        return HW_INFO_CACHE or {"type": "cpu", "available_encoders": {}}


def _ffprobe(input_path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=index,codec_type,bit_rate",
        "-of", "json",
        str(input_path)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    data = json.loads(proc.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    v_bitrate = None
    a_bitrate = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and s.get("bit_rate"):
            v_bitrate = float(s["bit_rate"]) / 1000.0
        if s.get("codec_type") == "audio" and s.get("bit_rate"): 
            a_bitrate = float(s["bit_rate"]) / 1000.0
    return {"duration": duration, "video_bitrate_kbps": v_bitrate, "audio_bitrate_kbps": a_bitrate}


def _calc_bitrates(target_mb: float, duration_s: float, audio_kbps: int) -> tuple[float, float, bool]:
    if duration_s <= 0:
        return 0.0, 0.0, True
    total_kbps = (target_mb * 8192.0) / duration_s
    video_kbps = max(total_kbps - float(audio_kbps), 0.0)
    warn = video_kbps < 100
    return total_kbps, video_kbps, warn


def _get_system_capabilities() -> dict:
    """Gather system capabilities: CPU, memory, GPUs, driver versions."""
    info: dict = {
        "cpu": {
            "cores_logical": psutil.cpu_count(logical=True) or 0,
            "cores_physical": psutil.cpu_count(logical=False) or 0,
        },
        "memory": {
            "total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
        },
        "gpus": [],
        "nvidia_driver": None,
    }

    # CPU model (best-effort)
    try:
        if hasattr(os, 'uname'):
            # Linux: read /proc/cpuinfo
            try:
                with open('/proc/cpuinfo','r') as f:
                    for line in f:
                        if 'model name' in line:
                            info["cpu"]["model"] = line.split(':',1)[1].strip()
                            break
            except Exception:
                pass
    except Exception:
        pass

    # NVIDIA GPUs via nvidia-smi (if available) - run once by caller that caches
    try:
        q = "index,name,memory.total,memory.used,driver_version,uuid"
        res = subprocess.run(
            ["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
            for ln in lines:
                parts = [p.strip() for p in ln.split(',')]
                if len(parts) >= 6:
                    idx, name, mem_total, mem_used, drv, uuid = parts[:6]
                    info["gpus"].append({
                        "index": int(idx),
                        "name": name,
                        "memory_total_gb": round(float(mem_total)/1024.0, 2),
                        "memory_used_gb": round(float(mem_used)/1024.0, 2),
                        "uuid": uuid,
                    })
                    info["nvidia_driver"] = drv
    except Exception:
        pass

    return info


@app.on_event("startup")
async def on_startup():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    start_scheduler()
    # Kick off background sync to apply codec visibility settings from worker startup tests
    try:
        # Record a new boot id on each API start so the UI can detect "first boot"
        boot_id = str(uuid.uuid4())
        try:
            await redis.set("startup:boot_id", boot_id)
            await redis.set("startup:boot_ts", str(int(time.time())))
        except Exception as e:
            logger.warning(f"Failed to set boot_id in Redis: {e}")
        asyncio.create_task(_sync_codec_settings_from_tests())
    except Exception as e:
        logger.warning(f"Startup initialization failed: {e}")


async def _sync_codec_settings_from_tests(timeout_s: int = 60):
    """Initialize codec visibility based primarily on detected hardware.

    Changes from prior behavior:
    - Do NOT gate visibility on startup test pass/fail (tests can fail due to
      permissions even when hardware exists). This avoids hiding NVIDIA options
      when GPUs are present.
    - CPU codecs are always enabled and are not tested at startup.
    """
    try:
        # Poll briefly for hardware info to avoid writing CPU-only when worker isn't ready
        hw_info: dict = {}
        avail: dict = {}
        deadline = time.time() + max(5, timeout_s)
        while time.time() < deadline:
            try:
                # Force refresh to avoid stale CPU cache on early startup
                hw_info = _get_hw_info_fresh(timeout=5) or {}
                avail = hw_info.get("available_encoders", {}) or {}
                if avail:  # Got concrete encoders like h264_nvenc, etc.
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        # Start from current settings to avoid clobbering when hardware isn't detected yet
        from . import settings_manager as _sm
        current = _sm.get_codec_visibility_settings()
        payload: dict[str, bool] = dict(current)

        # CPU codecs always enabled
        payload["libx264"] = True
        payload["libx265"] = True
        payload["libaom_av1"] = True

        hardware_keys = [
            "h264_nvenc","hevc_nvenc","av1_nvenc",
            "h264_qsv","hevc_qsv","av1_qsv",
            "h264_vaapi","hevc_vaapi","av1_vaapi",
            "h264_amf","hevc_amf","av1_amf",
        ]

        if avail:
            # If we have a definitive list, disable all HW keys then enable reported ones
            for k in hardware_keys:
                payload[k] = False
            for v in avail.values():
                key = v.replace('-', '_')
                if key in payload:
                    payload[key] = True
        else:
            # No hardware info yet; do not change existing HW visibility
            pass

        # Persist and flag banner
        _sm.update_codec_visibility_settings(payload)
        logger.info("Applied codec visibility from detected hardware: %s", ', '.join([k for k, v in payload.items() if v]))

        try:
            await redis.set("startup:codec_visibility_synced", "1")
            await redis.set("startup:codec_visibility_synced_at", str(int(time.time())))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Failed to apply codec visibility from hardware: {e}")
        try:
            await redis.set("startup:codec_visibility_synced", "0")
        except Exception:
            pass


@app.post("/api/upload", response_model=UploadResponse, dependencies=[Depends(basic_auth)])
async def upload(file: UploadFile = File(...), target_size_mb: float = 25.0, audio_bitrate_kbps: int = 128):
    # File size limit to prevent OOM (default 50GB)
    MAX_FILE_SIZE = int(os.getenv("MAX_UPLOAD_SIZE_MB", "51200")) * 1024 * 1024
    
    job_id = str(uuid.uuid4())
    dest = UPLOADS_DIR / f"{job_id}_{file.filename}"
    
    # Save file with size check
    total_size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(8192):  # Read in chunks
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                dest.unlink(missing_ok=True)  # Clean up partial file
                raise HTTPException(status_code=413, detail=f"File too large. Max size: {MAX_FILE_SIZE // (1024*1024)}MB")
            out.write(chunk)
    
    # ffprobe
    info = _ffprobe(dest)
    total_kbps, video_kbps, warn = _calc_bitrates(target_size_mb, info["duration"], audio_bitrate_kbps)
    return UploadResponse(
        job_id=job_id,
        filename=dest.name,
        duration_s=info["duration"],
        original_video_bitrate_kbps=info["video_bitrate_kbps"],
        original_audio_bitrate_kbps=info["audio_bitrate_kbps"],
        estimate_total_kbps=total_kbps,
        estimate_video_kbps=video_kbps,
        warn_low_quality=warn,
    )


@app.get("/api/startup/info")
async def startup_info():
    """Expose container boot id and codec sync status for lightweight UI banners."""
    try:
        boot_id = await redis.get("startup:boot_id")
        boot_ts = await redis.get("startup:boot_ts")
        synced = await redis.get("startup:codec_visibility_synced")
        synced_at = await redis.get("startup:codec_visibility_synced_at")
        return {
            "boot_id": boot_id,
            "boot_ts": int(boot_ts) if boot_ts else None,
            "codec_visibility_synced": (synced == "1"),
            "codec_visibility_synced_at": int(synced_at) if synced_at else None,
        }
    except Exception:
        # Best-effort fallback
        return {
            "boot_id": None,
            "boot_ts": None,
            "codec_visibility_synced": False,
            "codec_visibility_synced_at": None,
        }


@app.post("/api/compress", dependencies=[Depends(basic_auth)])
async def compress(req: CompressRequest):
    input_path = UPLOADS_DIR / req.filename
    if not input_path.exists():
        raise HTTPException(status_code=404, detail="Input not found")
    ext = ".mp4" if req.container == "mp4" else ".mkv"
    # Strip job_id prefix from filename (UUID + underscore) to get original name
    # e.g. "36f1e5e1-fe77-48ab-8e3c-f8061f670d9f_demo.mp4" -> "demo_8mblocal.mp4"
    stem = input_path.stem
    # Remove job_id prefix if present (36 char UUID + underscore = 37 chars)
    if len(stem) > 37 and stem[36] == '_':
        stem = stem[37:]
    
    # Generate a unique task_id first
    import uuid
    task_id = str(uuid.uuid4())
    
    # Use task_id in output filename to prevent concurrent jobs from overwriting each other
    # Format: originalname_8mblocal_taskid.ext (e.g., "video_8mblocal_abc123.mp4")
    output_name = f"{stem}_8mblocal_{task_id[:8]}{ext}"
    output_path = OUTPUTS_DIR / output_name
    
    task = celery_app.send_task(
        "worker.worker.compress_video",
        task_id=task_id,
        kwargs=dict(
            job_id=req.job_id,
            input_path=str(input_path),
            output_path=str(output_path),
            target_size_mb=req.target_size_mb,
            video_codec=req.video_codec,
            audio_codec=req.audio_codec,
            audio_bitrate_kbps=req.audio_bitrate_kbps,
            preset=req.preset,
            tune=req.tune,
            max_width=req.max_width,
            max_height=req.max_height,
            start_time=req.start_time,
            end_time=req.end_time,
            force_hw_decode=bool(req.force_hw_decode or False),
            fast_mp4_finalize=bool(req.fast_mp4_finalize or False),
        ),
    )
    # Proactively publish a queued message so UI shows activity even if worker startup is delayed
    try:
        await redis.publish(f"progress:{task.id}", orjson.dumps({"type":"log","message":"Job queued – waiting for worker…"}).decode())
    except Exception:
        pass
    
    # Store job metadata in Redis for queue tracking
    try:
        job_meta = JobMetadata(
            task_id=task.id,
            job_id=req.job_id,
            filename=req.filename,
            target_size_mb=req.target_size_mb,
            video_codec=req.video_codec,
            state='queued',
            progress=0.0,
            created_at=time.time()
        )
        await redis.setex(f"job:{task.id}", 86400, orjson.dumps(job_meta.dict()).decode())  # 24h TTL
        # Add to active jobs set
        await redis.zadd("jobs:active", {task.id: time.time()})
    except Exception as e:
        logger.warning(f"Failed to store job metadata: {e}")
    
    return {"task_id": task.id}


@app.get("/api/queue/status", response_model=QueueStatusResponse, dependencies=[Depends(basic_auth)])
async def queue_status():
    """Get current queue status showing all active, queued, and recently completed jobs."""
    try:
        # Get all active job IDs from sorted set (sorted by creation time)
        job_ids = await redis.zrange("jobs:active", 0, -1)
        
        jobs = []
        for task_id in job_ids:
            try:
                job_data = await redis.get(f"job:{task_id}")
                if job_data:
                    job_meta = JobMetadata(**orjson.loads(job_data))
                    # Update state from Celery if still running
                    if job_meta.state in ('queued', 'running'):
                        try:
                            res = celery_app.AsyncResult(task_id)
                            celery_state = res.state
                            meta = res.info if isinstance(res.info, dict) else {}
                            
                            if celery_state == 'PENDING':
                                job_meta.state = 'queued'
                                job_meta.phase = 'queued'
                            elif celery_state in ('STARTED', 'PROGRESS'):
                                job_meta.state = 'running'
                                old_progress = job_meta.progress
                                job_meta.progress = meta.get('progress', job_meta.progress)
                                
                                # Update phase from meta if available
                                if 'phase' in meta:
                                    job_meta.phase = meta['phase']
                                elif job_meta.progress < 95:
                                    job_meta.phase = 'encoding'
                                elif job_meta.progress < 100:
                                    job_meta.phase = 'finalizing'
                                else:
                                    job_meta.phase = 'done'
                                
                                if not job_meta.started_at:
                                    job_meta.started_at = time.time()
                                
                                # Calculate time estimation when progress changes
                                now_ts = time.time()
                                if job_meta.progress > old_progress and job_meta.progress > 0:
                                    job_meta.last_progress_update = now_ts
                                    elapsed = now_ts - job_meta.started_at
                                    if job_meta.progress < 100:
                                        # Estimate total time based on current progress rate
                                        estimated_total_time = elapsed / (job_meta.progress / 100.0)
                                        job_meta.estimated_completion_time = job_meta.started_at + estimated_total_time
                                
                            elif celery_state == 'SUCCESS':
                                job_meta.state = 'completed'
                                job_meta.progress = 100.0
                                job_meta.phase = 'done'
                                if not job_meta.completed_at:
                                    job_meta.completed_at = time.time()
                                job_meta.output_path = meta.get('output_path')
                                if 'final_size_mb' in meta:
                                    job_meta.final_size_mb = meta.get('final_size_mb')
                            elif celery_state == 'FAILURE':
                                job_meta.state = 'failed'
                                job_meta.phase = 'done'
                                if not job_meta.completed_at:
                                    job_meta.completed_at = time.time()
                                job_meta.error = str(meta) if meta else 'Unknown error'
                            
                            # Update Redis with current state
                            await redis.setex(f"job:{task_id}", 86400, orjson.dumps(job_meta.dict()).decode())
                        except Exception:
                            pass
                    
                    jobs.append(job_meta)
            except Exception as e:
                logger.warning(f"Failed to load job {task_id}: {e}")
                continue
        
        # Clean up completed/failed jobs older than 1 hour from active set
        now = time.time()
        for job in jobs:
            if job.state in ('completed', 'failed', 'canceled') and job.completed_at:
                if now - job.completed_at > 3600:
                    try:
                        await redis.zrem("jobs:active", job.task_id)
                    except Exception:
                        pass
        
        # Count by state
        queued = sum(1 for j in jobs if j.state == 'queued')
        running = sum(1 for j in jobs if j.state == 'running')
        completed = sum(1 for j in jobs if j.state == 'completed' and j.completed_at and (now - j.completed_at) < 3600)
        
        return QueueStatusResponse(
            active_jobs=jobs,
            queued_count=queued,
            running_count=running,
            completed_count=completed
        )
    except Exception as e:
        logger.error(f"Queue status error: {e}")
        return QueueStatusResponse(active_jobs=[], queued_count=0, running_count=0, completed_count=0)


@app.get("/api/jobs/{task_id}/status", response_model=StatusResponse, dependencies=[Depends(basic_auth)])
async def job_status(task_id: str):
    res = celery_app.AsyncResult(task_id)
    state = res.state
    meta = res.info if isinstance(res.info, dict) else {}
    return StatusResponse(state=state, progress=meta.get("progress"), detail=meta.get("detail"))


@app.get("/api/jobs/{task_id}/download", dependencies=[Depends(basic_auth)])
async def download(task_id: str, wait: float | None = None):
    res = celery_app.AsyncResult(task_id)
    state = res.state or "UNKNOWN"
    meta = res.info if isinstance(res.info, dict) else {}
    path = meta.get("output_path")
    # Fallback: if Celery meta isn't populated yet, check Redis 'ready' key
    if not path:
        try:
            cached = await redis.get(f"ready:{task_id}")
            if cached:
                path = cached
        except Exception:
            pass

    # Optional short wait window to reduce races when the user clicks immediately at 100%
    if wait and (not path or not os.path.isfile(str(path))):
        try:
            deadline = time.time() + max(0.1, min(float(wait), 5.0))
        except Exception:
            deadline = time.time() + 1.0
        while time.time() < deadline:
            # Re-check Celery meta
            try:
                res = celery_app.AsyncResult(task_id)
                meta = res.info if isinstance(res.info, dict) else meta
                p2 = (meta or {}).get("output_path")
                if p2:
                    path = p2
            except Exception:
                pass
            # Re-check Redis ready cache
            if not path:
                try:
                    cached = await redis.get(f"ready:{task_id}")
                    if cached:
                        path = cached
                except Exception:
                    pass
            # If file now exists, break
            if path and os.path.isfile(str(path)):
                break
            await asyncio.sleep(0.2)
    # If the file exists, serve it immediately
    if path and os.path.isfile(path):
        filename = os.path.basename(path)
        media_type = "video/mp4" if filename.lower().endswith(".mp4") else "video/x-matroska"
        return FileResponse(path, filename=filename, media_type=media_type)

    # History-based fallback: reconstruct expected output path from saved history
    # This enables downloads from the History page even after Celery metadata expires.
    try:
        entry = history_manager.get_history_entry(task_id)  # type: ignore[attr-defined]
    except AttributeError:
        # Older module without helper: scan limited history list
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
            # Derive output extension from stored container (default mp4)
            container = (entry.get("container") or "mp4").lower()
            ext = ".mp4" if container == "mp4" else ".mkv"
            # Reconstruct output filename like in /api/compress
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
            # Ignore and fall through to 404 detail
            pass

    # Otherwise, return a more descriptive error payload
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

    # Suggest client retry timing to improve UX when polling
    headers = {"Retry-After": "1", "Cache-Control": "no-store"}
    # Keep status code as 404 for backward compatibility with current UI
    raise HTTPException(status_code=404, detail=detail, headers=headers)


@app.post("/api/jobs/{task_id}/cancel")
async def cancel_job(task_id: str):
    """Signal a running job to cancel and attempt to stop ffmpeg."""
    try:
        # Set a short-lived cancel flag the worker checks
        await redis.set(f"cancel:{task_id}", "1", ex=3600)
        # Notify listeners via SSE channel immediately
        await redis.publish(f"progress:{task_id}", orjson.dumps({"type":"log","message":"Cancellation requested"}).decode())
        # Best-effort: also ask Celery to revoke/terminate (in case worker is stuck)
        try:
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            pass
        return {"status": "cancellation_requested"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/queue/clear")
async def clear_queue():
    """Clear all jobs from the queue (cancel running, remove pending/completed)."""
    try:
        # Get all job IDs from active set
        job_ids = await redis.zrange("jobs:active", 0, -1)
        
        cancelled_count = 0
        removed_count = 0
        
        for task_id in job_ids:
            try:
                # Get job metadata to check state
                job_data = await redis.get(f"job:{task_id}")
                if job_data:
                    job_meta = JobMetadata(**orjson.loads(job_data))
                    
                    # Cancel if running or queued
                    if job_meta.state in ('queued', 'running'):
                        # Set cancel flag
                        await redis.set(f"cancel:{task_id}", "1", ex=3600)
                        # Notify via SSE
                        await redis.publish(
                            f"progress:{task_id}", 
                            orjson.dumps({"type": "log", "message": "Queue cleared - job cancelled"}).decode()
                        )
                        # Revoke from Celery
                        try:
                            celery_app.control.revoke(task_id, terminate=True)
                        except Exception:
                            pass
                        cancelled_count += 1
                    
                    # Remove from active set
                    await redis.zrem("jobs:active", task_id)
                    # Remove job metadata
                    await redis.delete(f"job:{task_id}")
                    removed_count += 1
            except Exception as e:
                logger.warning(f"Failed to clear job {task_id}: {e}")
                continue
        
        return {
            "status": "cleared",
            "cancelled": cancelled_count,
            "removed": removed_count,
            "total": len(job_ids)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _sse_event_generator(task_id: str) -> AsyncGenerator[bytes, None]:
    """SSE stream combining Redis pubsub messages with periodic heartbeats.

    Heartbeats help keep connections alive across proxies that drop idle SSE.
    """
    channel = f"progress:{task_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    queue: asyncio.Queue[str] = asyncio.Queue()
    
    # Send initial connection message
    await queue.put(orjson.dumps({"type": "connected", "task_id": task_id, "ts": time.time()}).decode())

    async def reader():
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                logger.info(f"[SSE {task_id[:8]}] Received Redis message: {data[:100] if isinstance(data, str) else data}")
                sys.stdout.flush()  # Force flush
                # push raw json string from publisher
                await queue.put(str(data))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[SSE {task_id[:8]}] pubsub error: {e}")
            sys.stdout.flush()
            # Emit an error log and exit; outer loop will close
            try:
                await queue.put(orjson.dumps({"type": "error", "message": f"[SSE] pubsub error: {e}"}).decode())
            except Exception:
                pass

    async def heartbeater():
        try:
            while True:
                await asyncio.sleep(20)
                try:
                    await queue.put(orjson.dumps({"type": "ping", "ts": time.time()}).decode())
                except Exception:
                    # Best-effort heartbeat
                    pass
        except asyncio.CancelledError:
            pass

    reader_task = asyncio.create_task(reader())
    hb_task = asyncio.create_task(heartbeater())
    try:
        logger.info(f"[SSE {task_id[:8]}] Stream started")
        while True:
            data = await queue.get()
            logger.info(f"[SSE {task_id[:8]}] Yielding: {data[:100] if len(data) > 100 else data}")
            yield f"data: {data}\n\n".encode()
    finally:
        logger.info(f"[SSE {task_id[:8]}] Stream closing")
        reader_task.cancel()
        hb_task.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.close()


@app.get("/api/stream/{task_id}")
async def stream(task_id: str):
    return StreamingResponse(
        _sse_event_generator(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Connection": "keep-alive",
        }
    )


@app.get("/healthz")
async def health():
    return {"ok": True}


@app.get("/api/version")
async def api_version():
    """Return application version baked at build time."""
    ver = os.getenv("APP_VERSION", "123")
    return {"version": ver}


@app.get("/api/hardware")
async def get_hardware_info():
    """Get available hardware acceleration info from worker."""
    # Serve cached hardware info (computed once)
    return _get_hw_info_cached()


@app.get("/api/codecs/available")
async def get_available_codecs() -> AvailableCodecsResponse:
    """Get available codecs based on hardware detection, user settings, and encoder tests."""
    try:
        # Use cached hardware info
        hw_info = _get_hw_info_cached()

        # Get user codec visibility settings
        codec_settings = settings_manager.get_codec_visibility_settings()
        
        # Build list of enabled codecs based solely on user settings (which are
        # initialized from detected hardware at startup). We do not gate UI by
        # startup test results to avoid hiding options due to transient failures.
        enabled_codecs = []
        codec_map = {
            'h264_nvenc': codec_settings.get('h264_nvenc', True),
            'hevc_nvenc': codec_settings.get('hevc_nvenc', True),
            'av1_nvenc': codec_settings.get('av1_nvenc', True),
            'h264_qsv': codec_settings.get('h264_qsv', True),
            'hevc_qsv': codec_settings.get('hevc_qsv', True),
            'av1_qsv': codec_settings.get('av1_qsv', True),
            'h264_vaapi': codec_settings.get('h264_vaapi', True),
            'hevc_vaapi': codec_settings.get('hevc_vaapi', True),
            'av1_vaapi': codec_settings.get('av1_vaapi', True),
            'h264_amf': codec_settings.get('h264_amf', True),
            'hevc_amf': codec_settings.get('hevc_amf', True),
            'av1_amf': codec_settings.get('av1_amf', True),
            'libx264': codec_settings.get('libx264', True),
            'libx265': codec_settings.get('libx265', True),
            'libaom-av1': codec_settings.get('libaom_av1', True),
        }
        for codec, is_enabled in codec_map.items():
            if is_enabled:
                enabled_codecs.append(codec)

        # Always include encoders the worker reports as available, regardless of settings
        try:
            avail_map = hw_info.get("available_encoders", {}) or {}
            for enc in avail_map.values():
                if enc not in enabled_codecs:
                    enabled_codecs.append(enc)
        except Exception:
            pass
        
        return AvailableCodecsResponse(
            hardware_type=hw_info.get("type", "cpu"),
            available_encoders=hw_info.get("available_encoders", {}),
            enabled_codecs=enabled_codecs
        )
    except Exception as e:
        # Fallback
        return AvailableCodecsResponse(
            hardware_type="cpu",
            available_encoders={"h264": "libx264", "hevc": "libx265", "av1": "libaom-av1"},
            enabled_codecs=["libx264", "libx265", "libaom-av1"]
        )


@app.get("/api/system/capabilities")
async def system_capabilities():
    """Return detailed system capabilities including CPU, memory, GPUs and worker HW type."""
    global SYSTEM_CAPS_CACHE
    if SYSTEM_CAPS_CACHE is None:
        caps = _get_system_capabilities()
        caps["hardware"] = _get_hw_info_cached()
        SYSTEM_CAPS_CACHE = caps
    return SYSTEM_CAPS_CACHE


@app.get("/api/system/encoder-tests")
async def system_encoder_tests():
    """Return encoder startup test results and a simple summary.

    Reads cached results from Redis written by the worker at startup.
    """
    try:
        hw_info = _get_hw_info_cached()
    except Exception:
        hw_info = {"type": "cpu", "available_encoders": {}}

    # Candidate codecs to report (union of common HW and CPU encoders)
    test_codecs = [
        "h264_nvenc","hevc_nvenc","av1_nvenc",
        "h264_qsv","hevc_qsv","av1_qsv",
        "h264_vaapi","hevc_vaapi","av1_vaapi",
        "h264_amf","hevc_amf","av1_amf",
        "libx264","libx265","libaom-av1",
    ]

    results = []
    any_hw_passed = False
    try:
        for codec in test_codecs:
            # Get encode result
            encode_detail_raw = await redis.get(f"encoder_test_json:{codec}")
            encode_passed = False
            encode_msg = "Unknown"
            actual_encoder = codec
            
            if encode_detail_raw:
                try:
                    encode_detail = json.loads(encode_detail_raw)
                    encode_passed = bool(encode_detail.get("passed"))
                    encode_msg = encode_detail.get("message") or ("OK" if encode_passed else "Failed")
                    actual_encoder = encode_detail.get("actual_encoder", codec)
                except Exception:
                    pass
            else:
                # Fallback to boolean flag
                flag = await redis.get(f"encoder_test:{codec}")
                if flag is not None:
                    encode_passed = (str(flag) == "1")
                    encode_msg = "OK" if encode_passed else "Failed"
            
            # Get decode result (if hardware codec)
            decode_detail_raw = await redis.get(f"encoder_test_decode_json:{codec}")
            decode_passed = None
            decode_msg = None
            
            if decode_detail_raw:
                try:
                    decode_detail = json.loads(decode_detail_raw)
                    decode_passed = bool(decode_detail.get("passed"))
                    decode_msg = decode_detail.get("message") or ("OK" if decode_passed else "Failed")
                except Exception:
                    pass
            
            # Overall passed = encode passed AND (no decode test OR decode passed)
            overall_passed = encode_passed and (decode_passed is None or decode_passed)
            
            results.append({
                "codec": codec,
                "actual_encoder": actual_encoder,
                "passed": overall_passed,
                "encode_passed": encode_passed,
                "encode_message": encode_msg,
                "decode_passed": decode_passed,
                "decode_message": decode_msg,
            })
            
            # Only count hardware encoders for GPU availability check
            is_hardware = any(actual_encoder.endswith(suffix) for suffix in ["_nvenc", "_qsv", "_amf", "_vaapi"])
            if overall_passed and is_hardware:
                any_hw_passed = True

        # Filter: only include encoders relevant to this hardware type plus CPUs
        hw_type = (hw_info.get("type") or "cpu").lower()
        def _matches_hw(c: str) -> bool:
            if c.startswith("lib"):
                return True
            if hw_type == "nvidia":
                return c.endswith("_nvenc")
            if hw_type == "intel":
                return c.endswith("_qsv")
            if hw_type in ("amd","vaapi"):
                return c.endswith("_amf") or c.endswith("_vaapi")
            return False
        filtered = [r for r in results if _matches_hw(r["codec"])]

        return {
            "hardware_type": hw_info.get("type", "cpu"),
            "any_hardware_passed": any_hw_passed,
            "results": filtered or results,
        }
    except Exception as e:
        logger.warning(f"encoder-tests endpoint error: {e}")
        return {
            "hardware_type": hw_info.get("type", "cpu"),
            "any_hardware_passed": False,
            "results": [],
        }


@app.post("/api/system/encoder-tests/rerun", dependencies=[Depends(basic_auth)])
async def rerun_encoder_tests():
    """Trigger a fresh run of encoder/decoder startup tests on the worker and return updated results."""
    try:
        # Kick off worker-side tests and wait for completion (bounded timeout)
        task = celery_app.send_task("worker.worker.run_hardware_tests")
        try:
            _ = task.get(timeout=90)
        except Exception:
            # Continue even if we time out; we will still return current cached results
            pass
        # Return the same payload as GET /api/system/encoder-tests
        return await system_encoder_tests()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/diagnostics/gpu", dependencies=[Depends(basic_auth)])
async def gpu_diagnostics():
    """Run basic GPU checks inside the container to validate NVIDIA and NVENC.

    Returns structured results for:
    - nvidia-smi presence and GPU list
    - FFmpeg hwaccels listing
    - FFmpeg encoders listing (nvenc presence)
    - Quick NVENC encode smoke test using a synthetic color source
    - Presence of NVIDIA device files
    """
    def run_cmd(cmd: list[str], timeout: int = 6):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "cmd": " ".join(cmd),
                "rc": p.returncode,
                "stdout": (p.stdout or "")[-4000:],
                "stderr": (p.stderr or "")[-4000:]
            }
        except FileNotFoundError:
            return {"cmd": " ".join(cmd), "rc": 127, "stdout": "", "stderr": "command not found"}
        except subprocess.TimeoutExpired:
            return {"cmd": " ".join(cmd), "rc": 124, "stdout": "", "stderr": "timeout"}
        except Exception as e:
            return {"cmd": " ".join(cmd), "rc": 1, "stdout": "", "stderr": str(e)}

    # Collect checks
    checks: dict[str, any] = {}

    # Device files
    try:
        devs = []
        for d in ("/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-modeset"):
            try:
                devs.append({"path": d, "exists": os.path.exists(d)})
            except Exception:
                devs.append({"path": d, "exists": False})
        checks["device_files"] = devs
    except Exception:
        checks["device_files"] = []

    # nvidia-smi (GPU presence)
    checks["nvidia_smi_L"] = run_cmd(["nvidia-smi", "-L"], timeout=4)
    # ffmpeg hardware listing
    checks["ffmpeg_hwaccels"] = run_cmd(["ffmpeg", "-hide_banner", "-hwaccels"], timeout=4)
    # ffmpeg encoders list (look for *_nvenc)
    checks["ffmpeg_encoders"] = run_cmd(["ffmpeg", "-hide_banner", "-encoders"], timeout=6)

    # NVENC smoke test: encode 0.1s black 720p to null using h264_nvenc
    nvenc_test = run_cmd([
        "ffmpeg", "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=0.1",
        "-c:v", "h264_nvenc",
        "-f", "null", "-"
    ], timeout=8)
    checks["nvenc_smoke_test"] = nvenc_test

    # Summarize pass/fail heuristics
    summary = {
        "nvidia_device_present": any(x.get("exists") for x in checks.get("device_files", [])),
        "nvidia_smi_ok": checks["nvidia_smi_L"]["rc"] == 0 and bool(checks["nvidia_smi_L"].get("stdout")),
        "ffmpeg_sees_cuda": "cuda" in (checks["ffmpeg_hwaccels"].get("stdout", "") + checks["ffmpeg_hwaccels"].get("stderr", "")).lower(),
        "ffmpeg_has_nvenc": any(tok in checks["ffmpeg_encoders"].get("stdout", "") for tok in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]),
        "nvenc_encode_ok": nvenc_test["rc"] == 0 and "error" not in (nvenc_test.get("stderr", "").lower()),
    }

    return {"summary": summary, "checks": checks}


# Settings management endpoints
@app.get("/api/settings/auth")
async def get_auth_settings() -> AuthSettings:
    """Get current authentication settings (no auth required to check status)"""
    settings_data = settings_manager.get_auth_settings()
    return AuthSettings(**settings_data)


@app.put("/api/settings/auth")
async def update_auth_settings(
    settings_update: AuthSettingsUpdate,
    _auth=Depends(basic_auth)  # Require auth to change settings
):
    """Update authentication settings"""
    try:
        settings_manager.update_auth_settings(
            auth_enabled=settings_update.auth_enabled,
            auth_user=settings_update.auth_user,
            auth_pass=settings_update.auth_pass
        )
        return {"status": "success", "message": "Settings updated. Changes will take effect immediately."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings/password")
async def change_password(
    password_change: PasswordChange,
    _auth=Depends(basic_auth)  # Require current auth
):
    """Change the admin password"""
    # Verify current password
    if not settings_manager.verify_password(password_change.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    
    try:
        # Update only the password
        settings_manager.update_auth_settings(
            auth_enabled=True,  # Keep enabled
            auth_pass=password_change.new_password
        )
        return {"status": "success", "message": "Password changed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/settings/presets")
async def get_default_presets():
    """Get default preset values (no auth required for loading defaults)"""
    try:
        presets = settings_manager.get_default_presets()
        return presets
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/settings/presets")
async def update_default_presets(
    presets: DefaultPresets,
    _auth=Depends(basic_auth)  # Require auth to change defaults
):
    """Update default preset values"""
    try:
        settings_manager.update_default_presets(
            target_mb=presets.target_mb,
            video_codec=presets.video_codec,
            audio_codec=presets.audio_codec,
            preset=presets.preset,
            audio_kbps=presets.audio_kbps,
            container=presets.container,
            tune=presets.tune
        )
        return {"status": "success", "message": "Default presets updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Preset profiles CRUD
@app.get("/api/settings/preset-profiles")
async def get_preset_profiles() -> PresetProfilesResponse:
    try:
        data = settings_manager.get_preset_profiles()
        return PresetProfilesResponse(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings/preset-profiles")
async def add_preset_profile(profile: PresetProfile, _auth=Depends(basic_auth)):
    try:
        settings_manager.add_preset_profile(profile.dict())
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/settings/preset-profiles/default")
async def set_default_preset(req: SetDefaultPresetRequest, _auth=Depends(basic_auth)):
    try:
        settings_manager.set_default_preset(req.name)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/settings/preset-profiles/{name}")
async def update_preset_profile(name: str, updates: PresetProfile, _auth=Depends(basic_auth)):
    try:
        settings_manager.update_preset_profile(name, updates.dict())
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/settings/preset-profiles/{name}")
async def delete_preset_profile(name: str, _auth=Depends(basic_auth)):
    try:
        settings_manager.delete_preset_profile(name)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/settings/codecs")
async def get_codec_visibility_settings() -> CodecVisibilitySettings:
    """Get codec visibility settings (no auth required)"""
    try:
        settings_data = settings_manager.get_codec_visibility_settings()
        return CodecVisibilitySettings(**settings_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/settings/codecs")
async def update_codec_visibility_settings(
    codec_settings: CodecVisibilitySettings,
    _auth=Depends(basic_auth)  # Require auth to change settings
):
    """Update individual codec visibility settings"""
    try:
        settings_manager.update_codec_visibility_settings(codec_settings.dict())
        return {"status": "success", "message": "Codec visibility settings updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings/codecs/sync-from-hardware")
async def sync_codecs_from_hardware(_auth=Depends(basic_auth)):
    """Manually trigger a codec visibility sync based on detected hardware.

    Useful if startup order caused the initial sync to miss GPU availability.
    """
    try:
        await _sync_codec_settings_from_tests(timeout_s=15)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# History endpoints
@app.get("/api/settings/history")
async def get_history_settings():
    """Get history enabled setting (no auth required)"""
    return {"enabled": settings_manager.get_history_enabled()}


@app.put("/api/settings/history")
async def update_history_settings(
    data: dict,
    _auth=Depends(basic_auth)
):
    """Update history enabled setting"""
    try:
        enabled = data.get("enabled", False)
        settings_manager.update_history_enabled(enabled)
        return {"status": "success", "enabled": enabled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def get_history(limit: int = 50, _auth=Depends(basic_auth)):
    """Get compression history"""
    if not settings_manager.get_history_enabled():
        return {"entries": [], "enabled": False}
    
    entries = history_manager.get_history(limit=limit)
    return {"entries": entries, "enabled": True}


@app.delete("/api/history")
async def clear_history(_auth=Depends(basic_auth)):
    """Clear all history"""
    history_manager.clear_history()
    return {"status": "success", "message": "History cleared"}


@app.delete("/api/history/{task_id}")
async def delete_history_entry(task_id: str, _auth=Depends(basic_auth)):
    """Delete a specific history entry"""
    success = history_manager.delete_history_entry(task_id)
    if success:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="History entry not found")


# Initialize .env file on startup if it doesn't exist
@app.on_event("startup")
async def startup_event():
    settings_manager.initialize_env_if_missing()
    # Start cleanup scheduler
    start_scheduler()
    # Initialize hardware and system capabilities cache once
    try:
        _ = _get_hw_info_cached()
        # Warm system capabilities cache
        _ = system_capabilities  # function ref to avoid linter warning
    except Exception:
        pass


# Size buttons settings
@app.get("/api/settings/size-buttons")
async def get_size_buttons() -> SizeButtons:
    try:
        buttons = settings_manager.get_size_buttons()
        return SizeButtons(buttons=buttons)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/settings/size-buttons")
async def update_size_buttons(size_buttons: SizeButtons, _auth=Depends(basic_auth)):
    try:
        settings_manager.update_size_buttons(size_buttons.buttons)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Retention settings
@app.get("/api/settings/retention-hours")
async def get_retention_hours() -> RetentionHours:
    try:
        return RetentionHours(hours=settings_manager.get_retention_hours())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/settings/retention-hours")
async def update_retention_hours(req: RetentionHours, _auth=Depends(basic_auth)):
    try:
        settings_manager.update_retention_hours(req.hours)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/settings/worker-concurrency")
async def get_worker_concurrency(_auth=Depends(basic_auth)):
    """Get worker concurrency setting"""
    return {"concurrency": settings_manager.get_worker_concurrency()}


@app.put("/api/settings/worker-concurrency")
async def update_worker_concurrency_endpoint(req: dict, _auth=Depends(basic_auth)):
    """Update worker concurrency (requires container restart to take effect)"""
    try:
        concurrency = int(req.get("concurrency", 4))
        settings_manager.update_worker_concurrency(concurrency)
        return {
            "status": "success",
            "message": "Concurrency updated. Restart container for changes to take effect.",
            "concurrency": concurrency
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Serve pre-built frontend (for unified container deployment)
frontend_build = Path("/app/frontend-build")
if frontend_build.exists():
    # Serve static assets
    app.mount("/_app", StaticFiles(directory=frontend_build / "_app"), name="static-assets")
    
    # SPA fallback: serve index.html for all other routes
    from fastapi.responses import FileResponse
    
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve SPA - return index.html for all non-API routes"""
        # Check if a static file exists in the build directory (favicons, etc.)
        file_path = frontend_build / full_path
        if file_path.is_file():
            # Determine media type based on extension
            media_type = None
            if full_path.endswith('.svg'):
                media_type = "image/svg+xml"
            elif full_path.endswith('.png'):
                media_type = "image/png"
            elif full_path.endswith('.ico'):
                media_type = "image/x-icon"
            elif full_path.endswith('.jpg') or full_path.endswith('.jpeg'):
                media_type = "image/jpeg"
            return FileResponse(file_path, media_type=media_type)
        
        # For everything else, serve index.html (SPA routing)
        return FileResponse(frontend_build / "index.html")
