import asyncio
import json
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


@app.post("/api/upload", response_model=UploadResponse, dependencies=[Depends(basic_auth)])
async def upload(file: UploadFile = File(...), target_size_mb: float = 25.0, audio_bitrate_kbps: int = 128):
    job_id = str(uuid.uuid4())
    dest = UPLOADS_DIR / f"{job_id}_{file.filename}"
    # save file
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
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


@app.post("/api/compress", dependencies=[Depends(basic_auth)])
async def compress(req: CompressRequest):
    input_path = UPLOADS_DIR / req.filename
    if not input_path.exists():
        raise HTTPException(status_code=404, detail="Input not found")
    ext = ".mp4" if req.container == "mp4" else ".mkv"
    output_name = input_path.stem + "_8mb" + ext
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
    meta = res.info if isinstance(res.info, dict) else {}
    path = meta.get("output_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not ready")
    filename = os.path.basename(path)
    media_type = "video/mp4" if filename.lower().endswith(".mp4") else "video/x-matroska"
    return FileResponse(path, filename=filename, media_type=media_type)


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
    channel = f"progress:{task_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            yield f"data: {data}\n\n".encode()
    finally:
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
    """Get available codecs based on hardware detection and user settings."""
    try:
        # Use cached hardware info
        hw_info = _get_hw_info_cached()

        # Get user codec visibility settings
        codec_settings = settings_manager.get_codec_visibility_settings()
        
        # Build list of enabled codecs based on user settings
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
            'libx264': codec_settings.get('libx264', True),
            'libx265': codec_settings.get('libx265', True),
            'libaom-av1': codec_settings.get('libaom_av1', True),
        }
        
        for codec, is_enabled in codec_map.items():
            if is_enabled:
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
