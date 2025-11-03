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

from .auth import basic_auth
from .config import settings
from .celery_app import celery_app
from .models import UploadResponse, CompressRequest, StatusResponse, AuthSettings, AuthSettingsUpdate, PasswordChange, DefaultPresets, AvailableCodecsResponse, CodecVisibilitySettings
from .cleanup import start_scheduler
from . import settings_manager

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


@app.get("/api/stream/{task_id}", dependencies=[Depends(basic_auth)])
async def stream(task_id: str):
    return StreamingResponse(_sse_event_generator(task_id), media_type="text/event-stream")


@app.get("/healthz")
async def health():
    return {"ok": True}


@app.get("/api/hardware")
async def get_hardware_info():
    """Get available hardware acceleration info from worker."""
    try:
        # Query worker for hardware capabilities
        # This is a lightweight check via a Celery task
        from .celery_app import celery_app
        result = celery_app.send_task("worker.worker.get_hardware_info")
        hw_info = result.get(timeout=5)
        return hw_info
    except Exception as e:
        # Fallback to CPU-only if worker unavailable
        return {
            "type": "cpu",
            "available_encoders": {
                "h264": "libx264",
                "hevc": "libx265",
                "av1": "libaom-av1"
            }
        }


@app.get("/api/codecs/available")
async def get_available_codecs() -> AvailableCodecsResponse:
    """Get available codecs based on hardware detection and user settings."""
    try:
        # Get hardware info from worker
        from .celery_app import celery_app
        result = celery_app.send_task("worker.worker.get_hardware_info")
        hw_info = result.get(timeout=5)
        
        # Get user codec visibility settings
        codec_settings = settings_manager.get_codec_visibility_settings()
        
        # Determine which codec groups are enabled
        enabled_groups = []
        if codec_settings.get("show_nvidia", True):
            enabled_groups.append("nvidia")
        if codec_settings.get("show_intel", True):
            enabled_groups.append("intel")
        if codec_settings.get("show_amd", True):
            enabled_groups.append("amd")
        if codec_settings.get("show_cpu", True):
            enabled_groups.append("cpu")
        
        return AvailableCodecsResponse(
            hardware_type=hw_info.get("type", "cpu"),
            available_encoders=hw_info.get("available_encoders", {}),
            enabled_groups=enabled_groups
        )
    except Exception as e:
        # Fallback
        return AvailableCodecsResponse(
            hardware_type="cpu",
            available_encoders={"h264": "libx264", "hevc": "libx265", "av1": "libaom-av1"},
            enabled_groups=["cpu"]
        )


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
    """Update codec visibility settings"""
    try:
        settings_manager.update_codec_visibility_settings(
            show_nvidia=codec_settings.show_nvidia,
            show_intel=codec_settings.show_intel,
            show_amd=codec_settings.show_amd,
            show_cpu=codec_settings.show_cpu
        )
        return {"status": "success", "message": "Codec visibility settings updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Initialize .env file on startup if it doesn't exist
@app.on_event("startup")
async def startup_event():
    settings_manager.initialize_env_if_missing()
    # Start cleanup scheduler
    start_scheduler()


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
