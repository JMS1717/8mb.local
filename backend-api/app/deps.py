"""Shared dependencies, state, and helpers used by route handlers.

All routers import from here rather than reaching into ``main`` so that
``main.py`` stays thin (app creation, middleware, startup, router mounting).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import platform
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
# Path constants. Docker keeps the historical /app defaults; native/local
# launches may override APP_DATA_DIR, UPLOADS_DIR, and OUTPUTS_DIR.
# ---------------------------------------------------------------------------
_APP_DATA_DIR = Path(settings.APP_DATA_DIR)
UPLOADS_DIR = Path(settings.UPLOADS_DIR) if settings.UPLOADS_DIR else _APP_DATA_DIR / "uploads"
OUTPUTS_DIR = Path(settings.OUTPUTS_DIR) if settings.OUTPUTS_DIR else _APP_DATA_DIR / "outputs"

# ---------------------------------------------------------------------------
# Redis async client (shared across all routers)
# ---------------------------------------------------------------------------
if os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}:
    from shared.local_runtime import get_async_redis

    redis = get_async_redis()
else:
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

# TTL after which get_hw_info_cached() will trigger a fresh worker probe in the
# background. Short enough to pick up hardware changes (GPU driver reload,
# encoder tests re-run) without hammering Celery on every hw-info request.
HW_INFO_TTL_SECONDS: int = 60

# Hardware probing runs a real one-frame FFmpeg test for each encoder.  A
# native Windows process can need several seconds to initialize both NVIDIA
# and Intel devices, so the old five-second RPC deadline turned a healthy
# local runtime into repeated timeout warnings and incomplete codec metadata.
# Keep the deadline bounded while allowing the probe to finish; operators can
# override it for unusually slow drivers.
_LOCAL_RUNTIME = os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}
_DEFAULT_HW_INFO_RPC_TIMEOUT = 20 if _LOCAL_RUNTIME else 10
try:
    HW_INFO_RPC_TIMEOUT_SECONDS: int = max(
        5, int(os.getenv("HW_INFO_RPC_TIMEOUT_SECONDS", str(_DEFAULT_HW_INFO_RPC_TIMEOUT)))
    )
except ValueError:
    logger.warning(
        "Invalid HW_INFO_RPC_TIMEOUT_SECONDS; using %ss",
        _DEFAULT_HW_INFO_RPC_TIMEOUT,
    )
    HW_INFO_RPC_TIMEOUT_SECONDS = _DEFAULT_HW_INFO_RPC_TIMEOUT

# Guard against multiple concurrent refreshes kicked off by the same burst of
# requests. Populated lazily on the first async call so the lock attaches to
# the running event loop.
_hw_info_refresh_lock: asyncio.Lock | None = None


# ---------------------------------------------------------------------------
# Hardware info helpers
# ---------------------------------------------------------------------------
def _fetch_hw_info_blocking(timeout: int = 5) -> dict:
    """Synchronously call the Celery ``get_hardware_info`` task.

    Blocks the caller for up to ``timeout`` seconds; never call this directly
    from an async endpoint — wrap it in ``asyncio.to_thread`` or use
    ``get_hw_info_cached_async``. Falls back to a safe default on error.
    """
    t0 = time.time()
    try:
        logger.debug("hw-info: sending celery task (timeout=%ss)", timeout)
        result = celery_app.send_task("worker.worker.get_hardware_info")
        info = result.get(timeout=timeout) or {"type": "cpu", "available_encoders": {}}
        logger.debug(
            "hw-info: celery task returned in %.2fs: type=%s encoders=%s",
            time.time() - t0, info.get("type"), list((info.get("available_encoders") or {}).keys()),
        )
        return info
    except Exception as e:
        logger.warning("hw-info: celery task failed after %.2fs: %s", time.time() - t0, e)
        return {"type": "cpu", "available_encoders": {}}


def get_hw_info_cached() -> dict:
    """Return cached hardware info, refreshing synchronously on first miss.

    SAFE TO CALL FROM SYNC CONTEXTS ONLY (startup hooks, worker helpers).
    For async endpoints prefer ``get_hw_info_cached_async`` which never blocks
    the event loop.
    """
    global HW_INFO_CACHE, HW_INFO_CACHE_TS
    now = time.time()
    if HW_INFO_CACHE is not None and (now - HW_INFO_CACHE_TS) < HW_INFO_TTL_SECONDS:
        logger.debug("hw-info: cache HIT (age=%.1fs)", now - HW_INFO_CACHE_TS)
        return HW_INFO_CACHE

    logger.debug(
        "hw-info: cache MISS (have_cache=%s age=%.1fs ttl=%ss) — refreshing",
        HW_INFO_CACHE is not None, now - HW_INFO_CACHE_TS, HW_INFO_TTL_SECONDS,
    )
    fresh = _fetch_hw_info_blocking(timeout=HW_INFO_RPC_TIMEOUT_SECONDS)
    if fresh:
        HW_INFO_CACHE = fresh
        HW_INFO_CACHE_TS = now
    return HW_INFO_CACHE or {"type": "cpu", "available_encoders": {}}


async def get_hw_info_cached_async() -> dict:
    """Async variant — never blocks the event loop on the celery RPC.

    Returns the cached value immediately if fresh. On a miss, offloads the
    blocking celery call to a worker thread and holds a lock so N concurrent
    requests share a single refresh.
    """
    global HW_INFO_CACHE, HW_INFO_CACHE_TS, _hw_info_refresh_lock
    now = time.time()
    if HW_INFO_CACHE is not None and (now - HW_INFO_CACHE_TS) < HW_INFO_TTL_SECONDS:
        return HW_INFO_CACHE

    if _hw_info_refresh_lock is None:
        _hw_info_refresh_lock = asyncio.Lock()

    async with _hw_info_refresh_lock:
        # Another coroutine may have refreshed while we waited for the lock.
        now = time.time()
        if HW_INFO_CACHE is not None and (now - HW_INFO_CACHE_TS) < HW_INFO_TTL_SECONDS:
            return HW_INFO_CACHE
        logger.debug("hw-info: async refresh starting (offloading celery.get to thread)")
        fresh = await asyncio.to_thread(_fetch_hw_info_blocking, HW_INFO_RPC_TIMEOUT_SECONDS)
        if fresh:
            HW_INFO_CACHE = fresh
            HW_INFO_CACHE_TS = time.time()
    return HW_INFO_CACHE or {"type": "cpu", "available_encoders": {}}


def get_hw_info_fresh(timeout: int = 10) -> dict:
    """Force-refresh hardware info from worker, updating cache if successful.

    Synchronous helper — do not call from an async endpoint (use the async
    variant below). Preserved for back-compat with sync callers such as the
    startup ``sync_codec_settings_from_tests`` bootstrap loop.
    """
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
    logger.debug("hw-info: cache invalidated")
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


def _parse_finite_float(value: object) -> float | None:
    """Parse ffprobe numeric fields without treating ``N/A`` as an error."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def ffprobe(input_path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,bit_rate,width,height,avg_frame_rate,r_frame_rate",
        "-of", "json",
        str(input_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffprobe timed out while analyzing the upload") from exc
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("ffprobe returned an unexpected JSON shape")
    duration = _parse_finite_float((data.get("format") or {}).get("duration"))
    if duration is None or duration <= 0:
        raise RuntimeError("Input has no usable media duration")
    v_bitrate = None
    a_bitrate = None
    v_width = None
    v_height = None
    v_fps = None
    video_seen = False
    audio_seen = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            bitrate = _parse_finite_float(s.get("bit_rate"))
            if bitrate is not None and bitrate >= 0:
                v_bitrate = bitrate / 1000.0
            width = _parse_finite_float(s.get("width"))
            height = _parse_finite_float(s.get("height"))
            if width is not None and width > 0:
                v_width = int(width)
            if height is not None and height > 0:
                v_height = int(height)
            if not video_seen:
                v_fps = _parse_fps_fraction(s.get("avg_frame_rate"))
                if v_fps is None or v_fps <= 0:
                    v_fps = _parse_fps_fraction(s.get("r_frame_rate"))
                video_seen = True
        if s.get("codec_type") == "audio":
            audio_seen = True
            bitrate = _parse_finite_float(s.get("bit_rate"))
            if bitrate is not None and bitrate >= 0:
                a_bitrate = bitrate / 1000.0
    if not video_seen and not audio_seen:
        raise RuntimeError("Input has no usable audio or video stream")
    return {
        "duration": duration,
        "video_bitrate_kbps": v_bitrate,
        "audio_bitrate_kbps": a_bitrate,
        "width": v_width,
        "height": v_height,
        "video_fps": v_fps,
        "has_video": video_seen,
        "has_audio": audio_seen,
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
    try:
        with destination.open("wb") as out:
            while chunk := await upload.read(8192):
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Max size: {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB",
                    )
                out.write(chunk)
    except HTTPException:
        # The file handle must be closed before unlinking on Windows.  Keeping
        # cleanup outside the ``with`` block also guarantees a partial upload
        # cannot be mistaken for a valid input after a 413 response.
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logger.warning("upload: could not remove oversized partial file %s", destination)
        raise


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
    logical_cpus = psutil.cpu_count(logical=True) or 0
    physical_cpus = psutil.cpu_count(logical=False) or 0
    info: dict = {
        "cpu": {
            "cores_logical": logical_cpus,
            "cores_physical": physical_cpus,
        },
        "memory": {
            "total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
        },
        "gpus": [],
        "nvidia_driver": None,
        "dri_devices": [],
    }

    # /proc/cpuinfo is Linux-only. Native Windows builds otherwise rendered
    # a useful CPU count as ``CPU: Unknown`` even though psutil was working.
    try:
        cpu_model = ""
        if os.name == "nt":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                ) as key:
                    cpu_model = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
            except Exception:
                pass
        if not cpu_model and hasattr(os, "uname"):
            try:
                with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
                    for line in handle:
                        if "model name" in line:
                            cpu_model = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass
        if not cpu_model:
            cpu_model = str(platform.processor() or platform.uname().processor or "").strip()
        info["cpu"]["model"] = cpu_model or f"{physical_cpus or logical_cpus}-core CPU"
    except Exception:
        info["cpu"]["model"] = f"{physical_cpus or logical_cpus}-core CPU"

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

    try:
        info["dri_devices"] = [
            f"/dev/dri/{name}"
            for name in sorted(os.listdir("/dev/dri"))
            if name.startswith("renderD")
        ]
    except (FileNotFoundError, PermissionError, OSError):
        info["dri_devices"] = []

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
    - Hardware codecs are enabled when the worker reports them available and tests pass.
    - After updating visibility, ensures the default_preset points to the best
      available codec (NVENC > QSV > VAAPI > CPU).
    """
    import asyncio
    import json as _json

    try:
        hw_info: dict = {}
        avail: dict = {}
        deadline = time.time() + max(5, timeout_s)
        while time.time() < deadline:
            try:
                # This function is already async; keep the blocking Celery
                # compatibility call off the event loop.  The hardware probe
                # itself is bounded by the same runtime-aware deadline used
                # by the API cache.
                hw_info = await get_hw_info_fresh_async(timeout=HW_INFO_RPC_TIMEOUT_SECONDS) or {}
                avail = hw_info.get("available_encoders", {}) or {}
                if avail:
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        from . import settings_manager as _sm

        # SVT-AV1 is the approved CPU AV1 fallback. Legacy libaom settings are
        # intentionally not included in the runtime visibility sync.
        listed_cpu = hw_info.get("available_cpu_encoders")
        if isinstance(listed_cpu, list):
            available_cpu = {str(codec) for codec in listed_cpu}
        else:
            # A failed/old worker must not advertise a codec that the local
            # FFmpeg binary does not contain. x264/x265 are the conservative
            # compatibility baseline; SVT-AV1 is included only when enumerated.
            available_cpu = {"libx264", "libx265"}

        payload: dict[str, bool] = {
            "libx264": "libx264" in available_cpu,
            "libx265": "libx265" in available_cpu,
            "libsvtav1": "libsvtav1" in available_cpu,
            "h264_nvenc": False,
            "hevc_nvenc": False,
            "av1_nvenc": False,
            "h264_qsv": False,
            "hevc_qsv": False,
            "av1_qsv": False,
            "h264_vaapi": False,
            "hevc_vaapi": False,
            "av1_vaapi": False,
            "h264_amf": False,
            "hevc_amf": False,
            "av1_amf": False,
        }

        hardware_keys = [
            "h264_nvenc", "hevc_nvenc", "av1_nvenc",
            "h264_qsv", "hevc_qsv", "av1_qsv",
            "h264_vaapi", "hevc_vaapi", "av1_vaapi",
            "h264_amf", "hevc_amf", "av1_amf",
        ]

        tested_encoders = hw_info.get("tested_encoders") or {}
        runtime_probe_authoritative = bool(tested_encoders)
        if avail or tested_encoders:
            for codec in hardware_keys:
                if runtime_probe_authoritative:
                    # Encoder-test keys in Redis intentionally live much
                    # longer than a worker process. Once this worker has a
                    # fresh probe map, codecs absent from it are unsupported
                    # for this runtime and must not be re-enabled from stale
                    # persisted results.
                    payload[codec] = bool(tested_encoders.get(codec))
                    continue
                # ``available_encoders`` is the preferred encoder per family,
                # not the complete set of working devices. Mixed systems can
                # have NVIDIA plus Intel QSV, so retain every encoder whose
                # runtime probe passed.
                default_enabled = (
                    codec in avail.values()
                    or codec.replace('_', '-') in avail.values()
                    or bool(tested_encoders.get(codec))
                )

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
        if current_codec == 'libaom-av1':
            vis_key = 'libaom_av1'
        _vis_def = False if vis_key == 'libaom_av1' else True
        if vis_key and visibility.get(vis_key, _vis_def):
            return

        codec_priority = [
            'av1_nvenc', 'hevc_nvenc', 'h264_nvenc',
            'av1_qsv', 'hevc_qsv', 'h264_qsv',
            'av1_amf', 'hevc_amf', 'h264_amf',
            'av1_vaapi', 'hevc_vaapi', 'h264_vaapi',
            'libsvtav1', 'libx265', 'libx264',
        ]
        codec_to_vis = {'libaom-av1': 'libaom_av1'}
        for codec in codec_priority:
            vk = codec_to_vis.get(codec, codec)
            _vk_def = False if vk == 'libaom_av1' else True
            if not visibility.get(vk, _vk_def):
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

        updated_items.append({
            **item,
            "state": state,
            "progress": progress,
            "error": error,
            "output_path": output_path,
        })

    # New batches are dispatched as a Celery group, so a failed item must not
    # mark unrelated files as skipped. Keep the old chain behavior for batch
    # records created by older releases that do not carry an execution mode.
    first_failed_index: int | None = None
    if batch_payload.get("execution", "sequential") != "parallel":
        for idx, item in enumerate(updated_items):
            if item.get("state") in ("failed", "canceled"):
                first_failed_index = idx
                break

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
