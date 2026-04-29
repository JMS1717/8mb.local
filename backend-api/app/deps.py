"""Shared dependencies, state, and helpers used by route handlers.

All routers import from here rather than reaching into ``main`` so that
``main.py`` stays thin (app creation, middleware, startup, router mounting).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path

import orjson
import psutil
from fastapi import HTTPException, UploadFile
from redis.asyncio import Redis

from .celery_app import celery_app
from .config import settings
from .models import JobMetadata

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
UPLOADS_DIR = Path("/app/uploads")
OUTPUTS_DIR = Path("/app/outputs")

# ---------------------------------------------------------------------------
# Redis async client (shared across all routers)
# ---------------------------------------------------------------------------
redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)

# ---------------------------------------------------------------------------
# Upload / batch limits (read once at import time from settings)
# ---------------------------------------------------------------------------
MAX_UPLOAD_SIZE_BYTES: int = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_BATCH_FILES: int = settings.MAX_BATCH_FILES
BATCH_TTL_SECONDS: int = settings.BATCH_METADATA_TTL_HOURS * 3600

VIDEO_EXTENSIONS: set[str] = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv",
    ".mpeg", ".mpg", ".ts", ".m2ts", ".3gp", ".3g2", ".mts", ".mxf", ".ogv", ".vob",
}

# ---------------------------------------------------------------------------
# Caches (populated lazily, invalidated only on explicit refresh)
# ---------------------------------------------------------------------------
HW_INFO_CACHE: dict | None = None
HW_INFO_CACHE_TS: float = 0.0
SYSTEM_CAPS_CACHE: dict | None = None

# TTL after which get_hw_info_cached() will trigger a fresh worker probe.
HW_INFO_TTL_SECONDS: int = 60

# Guard against multiple concurrent async refreshes.
_hw_info_refresh_lock: asyncio.Lock | None = None


# ---------------------------------------------------------------------------
# Hardware info helpers
# ---------------------------------------------------------------------------
def _fetch_hw_info_blocking(timeout: int = 5) -> dict:
    """Synchronously call the Celery get_hardware_info task.

    Blocks the caller for up to ``timeout`` seconds; never call from an async
    endpoint — use get_hw_info_cached_async instead.
    """
    try:
        result = celery_app.send_task("worker.worker.get_hardware_info")
        info = result.get(timeout=timeout) or {"type": "cpu", "available_encoders": {}}
        return info
    except Exception as e:
        logger.warning("hw-info: celery task failed: %s", e)
        return {"type": "cpu", "available_encoders": {}}


def get_hw_info_cached() -> dict:
    """Return cached hardware info, refreshing synchronously on first miss.

    SAFE TO CALL FROM SYNC CONTEXTS ONLY. For async endpoints prefer
    get_hw_info_cached_async().
    """
    global HW_INFO_CACHE, HW_INFO_CACHE_TS
    now = time.time()
    if HW_INFO_CACHE is not None and (now - HW_INFO_CACHE_TS) < HW_INFO_TTL_SECONDS:
        return HW_INFO_CACHE

    fresh = _fetch_hw_info_blocking(timeout=5)
    if fresh:
        HW_INFO_CACHE = fresh
        HW_INFO_CACHE_TS = now
    return HW_INFO_CACHE or {"type": "cpu", "available_encoders": {}}


async def get_hw_info_cached_async() -> dict:
    """Async variant — never blocks the event loop on the celery RPC.

    Returns the cached value immediately if fresh. On a miss, offloads the
    blocking celery call to a worker thread with a lock to prevent stampede.
    """
    global HW_INFO_CACHE, HW_INFO_CACHE_TS, _hw_info_refresh_lock
    now = time.time()
    if HW_INFO_CACHE is not None and (now - HW_INFO_CACHE_TS) < HW_INFO_TTL_SECONDS:
        return HW_INFO_CACHE

    if _hw_info_refresh_lock is None:
        _hw_info_refresh_lock = asyncio.Lock()

    async with _hw_info_refresh_lock:
        now = time.time()
        if HW_INFO_CACHE is not None and (now - HW_INFO_CACHE_TS) < HW_INFO_TTL_SECONDS:
            return HW_INFO_CACHE
        fresh = await asyncio.to_thread(_fetch_hw_info_blocking, 5)
        if fresh:
            HW_INFO_CACHE = fresh
            HW_INFO_CACHE_TS = time.time()
    return HW_INFO_CACHE or {"type": "cpu", "available_encoders": {}}


def get_hw_info_fresh(timeout: int = 10) -> dict:
    """Force-refresh hardware info (sync). Use get_hw_info_fresh_async for endpoints."""
    global HW_INFO_CACHE, HW_INFO_CACHE_TS
    info = _fetch_hw_info_blocking(timeout=timeout)
    if info:
        HW_INFO_CACHE = info
        HW_INFO_CACHE_TS = time.time()
        return info
    return HW_INFO_CACHE or {"type": "cpu", "available_encoders": {}}


async def get_hw_info_fresh_async(timeout: int = 10) -> dict:
    """Async force-refresh; offloads the Celery RPC to a worker thread."""
    global HW_INFO_CACHE, HW_INFO_CACHE_TS
    info = await asyncio.to_thread(_fetch_hw_info_blocking, timeout)
    if info:
        HW_INFO_CACHE = info
        HW_INFO_CACHE_TS = time.time()
        return info
    return HW_INFO_CACHE or {"type": "cpu", "available_encoders": {}}


def invalidate_hw_info_cache() -> None:
    """Drop the cached hw-info so the next access performs a fresh probe."""
    global HW_INFO_CACHE, HW_INFO_CACHE_TS
    HW_INFO_CACHE = None
    HW_INFO_CACHE_TS = 0.0


# ---------------------------------------------------------------------------
# ffprobe / bitrate helpers
# ---------------------------------------------------------------------------
def _parse_fps_fraction(s: str | None) -> float | None:
    if not s or s in ("0/0", "N/A"):
        return None
    s = str(s).strip()
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            num, den = float(a), float(b)
            if den == 0:
                return None
            return num / den
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def ffprobe(input_path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,bit_rate,width,height,avg_frame_rate,r_frame_rate",
        "-of", "json",
        str(input_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    data = json.loads(proc.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    v_bitrate = None
    a_bitrate = None
    v_width = None
    v_height = None
    v_fps = None
    video_seen = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            if s.get("bit_rate"):
                v_bitrate = float(s["bit_rate"]) / 1000.0
            if s.get("width"):
                v_width = int(s["width"])
            if s.get("height"):
                v_height = int(s["height"])
            if not video_seen:
                v_fps = _parse_fps_fraction(s.get("avg_frame_rate"))
                if v_fps is None or v_fps <= 0:
                    v_fps = _parse_fps_fraction(s.get("r_frame_rate"))
                video_seen = True
        if s.get("codec_type") == "audio" and s.get("bit_rate"):
            a_bitrate = float(s["bit_rate"]) / 1000.0
    return {
        "duration": duration,
        "video_bitrate_kbps": v_bitrate,
        "audio_bitrate_kbps": a_bitrate,
        "width": v_width,
        "height": v_height,
        "video_fps": v_fps,
    }


def calc_bitrates(target_mb: float, duration_s: float, audio_kbps: int) -> tuple[float, float, bool]:
    if duration_s <= 0:
        return 0.0, 0.0, True
    total_kbps = (target_mb * 8192.0) / duration_s
    video_kbps = max(total_kbps - float(audio_kbps), 0.0)
    warn = video_kbps < 100
    return total_kbps, video_kbps, warn


# ---------------------------------------------------------------------------
# File-name / upload helpers
# ---------------------------------------------------------------------------
def safe_filename(filename: str | None) -> str:
    if not filename:
        return "upload.bin"
    safe = Path(filename).name
    return safe or "upload.bin"


async def save_upload_file(upload: UploadFile, destination: Path) -> None:
    total_size = 0
    with destination.open("wb") as out:
        while chunk := await upload.read(8192):
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE_BYTES:
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max size: {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB",
                )
            out.write(chunk)


def is_video_upload(upload: UploadFile) -> bool:
    content_type = (upload.content_type or "").lower()
    if content_type.startswith("video/"):
        return True
    ext = Path(safe_filename(upload.filename)).suffix.lower()
    return ext in VIDEO_EXTENSIONS


def build_output_name(input_path: Path, task_id: str, container: str, audio_only: bool = False) -> str:
    ext = ".m4a" if audio_only else (".mp4" if container == "mp4" else ".mkv")
    stem = input_path.stem
    if len(stem) > 37 and stem[36] == '_':
        stem = stem[37:]
    return f"{stem}_8mblocal_{task_id[:8]}{ext}"


# ---------------------------------------------------------------------------
# Job metadata helpers
# ---------------------------------------------------------------------------
async def store_job_metadata(task_id: str, job_id: str, filename: str, target_size_mb: float, video_codec: str) -> None:
    try:
        job_meta = JobMetadata(
            task_id=task_id,
            job_id=job_id,
            filename=filename,
            target_size_mb=target_size_mb,
            video_codec=video_codec,
            state='queued',
            progress=0.0,
            created_at=time.time(),
        )
        await redis.setex(f"job:{task_id}", 86400, orjson.dumps(job_meta.dict()).decode())
        await redis.zadd("jobs:active", {task_id: time.time()})
    except Exception as e:
        logger.warning(f"Failed to store job metadata for {task_id}: {e}")


# ---------------------------------------------------------------------------
# System capabilities
# ---------------------------------------------------------------------------
def get_system_capabilities() -> dict:
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

    try:
        if hasattr(os, 'uname'):
            try:
                with open('/proc/cpuinfo', 'r') as f:
                    for line in f:
                        if 'model name' in line:
                            info["cpu"]["model"] = line.split(':', 1)[1].strip()
                            break
            except Exception:
                pass
    except Exception:
        pass

    try:
        q = "index,name,memory.total,memory.used,driver_version,uuid"
        res = subprocess.run(
            ["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
            for ln in lines:
                parts = [p.strip() for p in ln.split(',')]
                if len(parts) >= 6:
                    idx, name, mem_total, mem_used, drv, gpu_uuid = parts[:6]
                    info["gpus"].append({
                        "index": int(idx),
                        "name": name,
                        "memory_total_gb": round(float(mem_total) / 1024.0, 2),
                        "memory_used_gb": round(float(mem_used) / 1024.0, 2),
                        "uuid": gpu_uuid,
                    })
                    info["nvidia_driver"] = drv
    except Exception:
        pass

    return info


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------
async def load_batch_payload(batch_id: str) -> dict:
    raw = await redis.get(f"batch:{batch_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Batch not found")
    try:
        payload = orjson.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to decode batch metadata: {e}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Invalid batch metadata")
    return payload


async def sync_codec_settings_from_tests(timeout_s: int = 60) -> None:
    """Initialize codec visibility and default preset based on detected hardware.

    - CPU codecs are always enabled.
    - NVENC codecs are enabled when the worker reports them available and tests pass.
    - After updating visibility, ensures the default_preset points to the best
      available codec (NVENC AV1 > HEVC > H264 > CPU).
    """
    import asyncio
    import json as _json

    try:
        hw_info: dict = {}
        avail: dict = {}
        deadline = time.time() + max(5, timeout_s)
        while time.time() < deadline:
            try:
                hw_info = get_hw_info_fresh(timeout=5) or {}
                avail = hw_info.get("available_encoders", {}) or {}
                if avail:
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        from . import settings_manager as _sm

        payload: dict[str, bool] = {
            "libx264": True,
            "libx265": True,
            "libsvtav1": True,
            "h264_nvenc": False,
            "hevc_nvenc": False,
            "av1_nvenc": False,
        }

        hardware_keys = ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]

        if avail:
            for codec in hardware_keys:
                default_enabled = codec in avail.values() or codec.replace('_', '-') in avail.values()

                try:
                    encode_detail_raw = await redis.get(f"encoder_test_json:{codec}")
                    decode_detail_raw = await redis.get(f"encoder_test_decode_json:{codec}")
                    flag = await redis.get(f"encoder_test:{codec}")
                except Exception:
                    encode_detail_raw = decode_detail_raw = flag = None

                encode_passed = None
                if encode_detail_raw:
                    try:
                        encode_passed = bool(_json.loads(encode_detail_raw).get("passed"))
                    except Exception:
                        pass
                elif flag is not None:
                    encode_passed = (str(flag) == "1")

                decode_passed = None
                if decode_detail_raw:
                    try:
                        decode_passed = bool(_json.loads(decode_detail_raw).get("passed"))
                    except Exception:
                        pass

                if encode_passed is not None:
                    payload[codec] = encode_passed and (decode_passed is None or decode_passed)
                else:
                    payload[codec] = bool(default_enabled)

        _sm.update_codec_visibility_settings(payload)
        logger.info("Codec visibility synced: %s", ', '.join(k for k, v in payload.items() if v))

        _ensure_default_preset_matches_hardware(_sm, payload)

        try:
            await redis.set("startup:codec_visibility_synced", "1")
            await redis.set("startup:codec_visibility_synced_at", str(int(time.time())))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Failed to sync codec settings from hardware: {e}")
        try:
            await redis.set("startup:codec_visibility_synced", "0")
        except Exception:
            pass


def _ensure_default_preset_matches_hardware(_sm, visibility: dict[str, bool]) -> None:
    """If the current default_preset uses a codec that isn't available,
    switch it to the best available codec's profile."""
    try:
        data = _sm._read_settings()
        profiles = data.get('preset_profiles', [])
        default_name = data.get('default_preset')
        if not profiles:
            return

        current_codec = None
        for p in profiles:
            if p.get('name') == default_name:
                current_codec = p.get('video_codec')
                break

        vis_key = current_codec
        if current_codec in ('libsvtav1', 'libaom-av1'):
            vis_key = 'libsvtav1'
        if vis_key and visibility.get(vis_key, True):
            return

        codec_priority = ['av1_nvenc', 'hevc_nvenc', 'h264_nvenc',
                          'libsvtav1', 'libx265', 'libx264']
        codec_to_vis = {'libsvtav1': 'libsvtav1', 'libaom-av1': 'libsvtav1'}
        for codec in codec_priority:
            vk = codec_to_vis.get(codec, codec)
            if not visibility.get(vk, True):
                continue
            for p in profiles:
                if p.get('video_codec') == codec:
                    data['default_preset'] = p['name']
                    _sm._write_settings(data)
                    logger.info("Default preset auto-switched to '%s' based on available hardware", p['name'])
                    return
    except Exception as e:
        logger.warning(f"Failed to auto-switch default preset: {e}")


async def refresh_batch_payload(batch_payload: dict) -> dict:
    items = batch_payload.get("items") or []
    updated_items: list[dict] = []

    queued_count = 0
    running_count = 0
    completed_count = 0
    failed_count = 0
    total_progress = 0.0
    first_failed_index: int | None = None

    for idx, item in enumerate(items):
        task_id = str(item.get("task_id") or "")
        state = str(item.get("state") or "queued")
        progress = float(item.get("progress") or 0.0)
        error = item.get("error")
        output_path = item.get("output_path")

        if task_id:
            res = celery_app.AsyncResult(task_id)
            celery_state = str(res.state or "PENDING")
            meta = res.info if isinstance(res.info, dict) else {}

            if celery_state == "PENDING":
                if state not in ("completed", "failed", "canceled"):
                    state = "queued"
                    progress = 0.0
            elif celery_state in ("STARTED", "PROGRESS"):
                state = "running"
                progress = float(meta.get("progress") or progress or 0.0)
                error = None
            elif celery_state == "SUCCESS":
                state = "completed"
                progress = 100.0
                output_path = meta.get("output_path") or output_path
                error = None
            elif celery_state in ("FAILURE", "REVOKED"):
                state = "failed" if celery_state == "FAILURE" else "canceled"
                progress = 100.0
                if not error:
                    try:
                        error = str(res.result) if res.result else "Compression failed"
                    except Exception:
                        error = "Compression failed"

        progress = max(0.0, min(100.0, float(progress)))

        if state == "queued":
            queued_count += 1
            total_progress += progress
        elif state == "running":
            running_count += 1
            total_progress += progress
        elif state == "completed":
            completed_count += 1
            total_progress += 100.0
        else:
            failed_count += 1
            total_progress += 100.0
            if first_failed_index is None:
                first_failed_index = idx

        updated_items.append({
            **item,
            "state": state,
            "progress": progress,
            "error": error,
            "output_path": output_path,
        })

    if first_failed_index is not None:
        for idx in range(first_failed_index + 1, len(updated_items)):
            item = updated_items[idx]
            if item.get("state") in ("queued", "running"):
                prev_progress = float(item.get("progress") or 0.0)
                if item.get("state") == "queued":
                    queued_count -= 1
                else:
                    running_count -= 1
                failed_count += 1
                total_progress += (100.0 - prev_progress)
                item["state"] = "failed"
                item["progress"] = 100.0
                item["error"] = item.get("error") or "Skipped because a previous batch item failed."
                task_id = str(item.get("task_id") or "")
                if task_id:
                    try:
                        raw_job = await redis.get(f"job:{task_id}")
                        if raw_job:
                            job_meta = orjson.loads(raw_job)
                            job_meta["state"] = "failed"
                            job_meta["phase"] = "done"
                            job_meta["progress"] = 100.0
                            job_meta["completed_at"] = time.time()
                            job_meta["error"] = item["error"]
                            await redis.setex(f"job:{task_id}", 86400, orjson.dumps(job_meta).decode())
                    except Exception:
                        pass

    item_count = len(updated_items)
    if running_count > 0:
        batch_state = "running"
    elif item_count > 0 and completed_count == item_count:
        batch_state = "completed"
    elif item_count > 0 and failed_count == item_count:
        batch_state = "failed"
    elif item_count > 0 and (completed_count + failed_count) == item_count:
        batch_state = "completed_with_errors"
    else:
        batch_state = "queued"

    batch_payload["state"] = batch_state
    batch_payload["queued_count"] = queued_count
    batch_payload["running_count"] = running_count
    batch_payload["completed_count"] = completed_count
    batch_payload["failed_count"] = failed_count
    batch_payload["overall_progress"] = round(total_progress / item_count, 2) if item_count else 0.0
    batch_payload["items"] = updated_items

    return batch_payload
