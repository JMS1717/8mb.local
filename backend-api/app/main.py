import asyncio
import contextlib
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
from .models import UploadResponse, CompressRequest, StatusResponse, AuthSettings, AuthSettingsUpdate, PasswordChange, DefaultPresets, AvailableCodecsResponse, CodecVisibilitySettings, PresetProfile, PresetProfilesResponse, SetDefaultPresetRequest, SizeButtons, RetentionHours
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
        except Exception:
            pass
        asyncio.create_task(_sync_codec_settings_from_tests())
    except Exception:
        pass


async def _sync_codec_settings_from_tests(timeout_s: int = 60):
    """Poll Redis for worker encoder test results and update codec visibility settings.

    Goal: Only enable codecs that passed startup validation so the Settings UI
    reflects actual support as soon as the container starts.
    """
    all_codecs = [
        'h264_nvenc','hevc_nvenc','av1_nvenc',
        'h264_qsv','hevc_qsv','av1_qsv',
        'h264_vaapi','hevc_vaapi','av1_vaapi',
        'h264_amf','hevc_amf','av1_amf',
        'libx264','libx265','libaom-av1'
    ]
    # Poll until we see at least one encoder_test key or we time out
    deadline = time.time() + max(1, timeout_s)
    seen_any = False
    while time.time() < deadline:
        try:
            values = await asyncio.gather(*[redis.get(f"encoder_test:{c}") for c in all_codecs])
            if any(v is not None for v in values):
                seen_any = True
                break
        except Exception:
            # Redis may not be ready yet
            pass
        await asyncio.sleep(1)

    if not seen_any:
        # Mark that no sync occurred (for UI awareness)
        try:
            await redis.set("startup:codec_visibility_synced", "0")
        except Exception:
            pass
        return

    # Build settings payload: enable only codecs with "1"; always keep CPU codecs enabled
    payload: dict[str, bool] = {}
    try:
        results = {}
        for c in all_codecs:
            try:
                v = await redis.get(f"encoder_test:{c}")
                results[c] = (v == '1') if v is not None else False
            except Exception:
                results[c] = False
        # CPU codecs should default to True even if not tested
        results['libx264'] = True
        results['libx265'] = True
        results['libaom-av1'] = True

        # Map to settings keys and apply
        for c in all_codecs:
            key = c.replace('-', '_')  # libaom-av1 -> libaom_av1
            payload[key] = bool(results.get(c, False))

        # Persist to .env via settings manager (handles write failures gracefully)
        from . import settings_manager as _sm
        _sm.update_codec_visibility_settings(payload)
        logger.info("Applied codec visibility from startup tests: %s", ', '.join([k for k, v in payload.items() if v]))
        # Flag for UI banner: synced from hardware tests
        try:
            await redis.set("startup:codec_visibility_synced", "1")
            await redis.set("startup:codec_visibility_synced_at", str(int(time.time())))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Failed to apply codec visibility from tests: {e}")
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
    output_name = stem + "_8mblocal" + ext
    output_path = OUTPUTS_DIR / output_name
    task = celery_app.send_task(
        "worker.worker.compress_video",
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
    return {"task_id": task.id}


@app.get("/api/jobs/{task_id}/status", response_model=StatusResponse, dependencies=[Depends(basic_auth)])
async def job_status(task_id: str):
    res = celery_app.AsyncResult(task_id)
    state = res.state
    meta = res.info if isinstance(res.info, dict) else {}
    return StatusResponse(state=state, progress=meta.get("progress"), detail=meta.get("detail"))


@app.get("/api/jobs/{task_id}/download", dependencies=[Depends(basic_auth)])
async def download(task_id: str):
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
    # If the file exists, serve it immediately
    if path and os.path.isfile(path):
        filename = os.path.basename(path)
        media_type = "video/mp4" if filename.lower().endswith(".mp4") else "video/x-matroska"
        return FileResponse(path, filename=filename, media_type=media_type)

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

    # Keep status code as 404 for backward compatibility with current UI
    raise HTTPException(status_code=404, detail=detail)


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


async def _sse_event_generator(task_id: str) -> AsyncGenerator[bytes, None]:
    """SSE stream combining Redis pubsub messages with periodic heartbeats.

    Heartbeats help keep connections alive across proxies that drop idle SSE.
    """
    channel = f"progress:{task_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    queue: asyncio.Queue[str] = asyncio.Queue()

    async def reader():
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                # push raw json string from publisher
                await queue.put(str(data))
        except asyncio.CancelledError:
            pass
        except Exception as e:
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
        while True:
            data = await queue.get()
            yield f"data: {data}\n\n".encode()
    finally:
        reader_task.cancel()
        hb_task.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.close()


@app.get("/api/stream/{task_id}")
async def stream(task_id: str):
    return StreamingResponse(_sse_event_generator(task_id), media_type="text/event-stream")


@app.get("/healthz")
async def health():
    return {"ok": True}


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
        
        # Check which encoders actually passed startup tests (from worker)
        tested_codecs = {}
        try:
            all_codecs = ['h264_nvenc', 'hevc_nvenc', 'av1_nvenc', 'h264_qsv', 'hevc_qsv', 
                         'av1_qsv', 'h264_vaapi', 'hevc_vaapi', 'av1_vaapi', 
                         'h264_amf', 'hevc_amf', 'av1_amf',
                         'libx264', 'libx265', 'libaom-av1']
            for codec in all_codecs:
                result = await redis.get(f"encoder_test:{codec}")
                tested_codecs[codec] = (result == "1") if result else None  # None = not tested
        except Exception as e:
            logger.warning(f"Failed to get encoder test results: {e}")
        
        # Build list of enabled codecs based on user settings AND test results
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
            # Only include if: enabled in settings AND (not tested OR passed test)
            test_result = tested_codecs.get(codec)
            if is_enabled and (test_result is None or test_result is True):
                enabled_codecs.append(codec)
        
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
