"""Settings and history route handlers."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..auth import basic_auth
from .. import settings_manager
from .. import history_manager
from ..models import (
    AuthSettings,
    AuthSettingsUpdate,
    CodecVisibilitySettings,
    DefaultPresets,
    PasswordChange,
    PresetProfile,
    PresetProfilesResponse,
    RetentionHours,
    SetDefaultPresetRequest,
    SizeButtons,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])


@router.get("/api/settings/auth")
async def get_auth_settings() -> AuthSettings:
    """Get current authentication settings (no auth required to check status)"""
    settings_data = settings_manager.get_auth_settings()
    return AuthSettings(**settings_data)


@router.put("/api/settings/auth")
async def update_auth_settings(
    settings_update: AuthSettingsUpdate,
    _auth=Depends(basic_auth),
):
    """Update authentication settings"""
    try:
        settings_manager.update_auth_settings(
            auth_enabled=settings_update.auth_enabled,
            auth_user=settings_update.auth_user,
            auth_pass=settings_update.auth_pass,
        )
        return {"status": "success", "message": "Settings updated. Changes will take effect immediately."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/password")
async def change_password(
    password_change: PasswordChange,
    _auth=Depends(basic_auth),
):
    """Change the admin password"""
    if not settings_manager.verify_password(password_change.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    
    try:
        settings_manager.update_auth_settings(
            auth_enabled=True,
            auth_pass=password_change.new_password,
        )
        return {"status": "success", "message": "Password changed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/settings/presets")
async def get_default_presets():
    """Get default preset values (no auth required for loading defaults)"""
    try:
        presets = settings_manager.get_default_presets()
        return presets
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/settings/presets")
async def update_default_presets(
    presets: DefaultPresets,
    _auth=Depends(basic_auth),
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
            tune=presets.tune,
            max_output_fps=presets.max_output_fps,
        )
        return {"status": "success", "message": "Default presets updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/settings/preset-profiles")
async def get_preset_profiles() -> PresetProfilesResponse:
    try:
        data = settings_manager.get_preset_profiles()
        return PresetProfilesResponse(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/preset-profiles")
async def add_preset_profile(profile: PresetProfile, _auth=Depends(basic_auth)):
    try:
        settings_manager.add_preset_profile(profile.dict())
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/settings/preset-profiles/default")
async def set_default_preset(req: SetDefaultPresetRequest, _auth=Depends(basic_auth)):
    try:
        settings_manager.set_default_preset(req.name)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/settings/preset-profiles/{name}")
async def update_preset_profile(name: str, updates: PresetProfile, _auth=Depends(basic_auth)):
    try:
        settings_manager.update_preset_profile(name, updates.dict())
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/settings/preset-profiles/{name}")
async def delete_preset_profile(name: str, _auth=Depends(basic_auth)):
    try:
        settings_manager.delete_preset_profile(name)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/settings/codecs")
async def get_codec_visibility_settings() -> CodecVisibilitySettings:
    """Get codec visibility settings (no auth required)"""
    try:
        settings_data = settings_manager.get_codec_visibility_settings()
        return CodecVisibilitySettings(**settings_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/settings/codecs")
async def update_codec_visibility_settings(
    codec_settings: CodecVisibilitySettings,
    _auth=Depends(basic_auth),
):
    """Update individual codec visibility settings"""
    try:
        settings_manager.update_codec_visibility_settings(codec_settings.dict())
        return {"status": "success", "message": "Codec visibility settings updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/settings/history")
async def get_history_settings():
    """Get history enabled setting (no auth required)"""
    return {"enabled": settings_manager.get_history_enabled()}


@router.put("/api/settings/history")
async def update_history_settings(
    data: dict,
    _auth=Depends(basic_auth),
):
    """Update history enabled setting"""
    try:
        enabled = data.get("enabled", False)
        settings_manager.update_history_enabled(enabled)
        return {"status": "success", "enabled": enabled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/history")
async def get_history(limit: int = 50, _auth=Depends(basic_auth)):
    """Get compression history"""
    if not settings_manager.get_history_enabled():
        return {"entries": [], "enabled": False}
    
    entries = history_manager.get_history(limit=limit)
    return {"entries": entries, "enabled": True}


@router.delete("/api/history")
async def clear_history(_auth=Depends(basic_auth)):
    """Clear all history"""
    history_manager.clear_history()
    return {"status": "success", "message": "History cleared"}


@router.delete("/api/history/{task_id}")
async def delete_history_entry(task_id: str, _auth=Depends(basic_auth)):
    """Delete a specific history entry"""
    success = history_manager.delete_history_entry(task_id)
    if success:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="History entry not found")


@router.get("/api/settings/size-buttons")
async def get_size_buttons() -> SizeButtons:
    try:
        buttons = settings_manager.get_size_buttons()
        return SizeButtons(buttons=buttons)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/settings/size-buttons")
async def update_size_buttons(size_buttons: SizeButtons, _auth=Depends(basic_auth)):
    try:
        settings_manager.update_size_buttons(size_buttons.buttons)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/settings/retention-hours")
async def get_retention_hours() -> RetentionHours:
    try:
        return RetentionHours(hours=settings_manager.get_retention_hours())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/settings/retention-hours")
async def update_retention_hours(req: RetentionHours, _auth=Depends(basic_auth)):
    try:
        settings_manager.update_retention_hours(req.hours)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/settings/worker-concurrency")
async def get_worker_concurrency(_auth=Depends(basic_auth)):
    """Get worker concurrency setting"""
    return {"concurrency": settings_manager.get_worker_concurrency()}


@router.put("/api/settings/worker-concurrency")
async def update_worker_concurrency_endpoint(req: dict, _auth=Depends(basic_auth)):
    """Update worker concurrency (requires container restart to take effect)"""
    try:
        concurrency = int(req.get("concurrency", 4))
        settings_manager.update_worker_concurrency(concurrency)
        return {
            "status": "success",
            "message": "Concurrency updated. Restart container for changes to take effect.",
            "concurrency": concurrency,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/settings/daemon-port")
async def get_daemon_port_endpoint(_auth=Depends(basic_auth)):
    """Get daemon port setting"""
    return {"port": settings_manager.get_daemon_port()}


@router.put("/api/settings/daemon-port")
async def update_daemon_port_endpoint(req: dict, _auth=Depends(basic_auth)):
    """Update daemon port"""
    try:
        address = str(req.get("port", "8000")).strip()
        settings_manager.update_daemon_port(address)
        return {
            "status": "success",
            "message": "Daemon connection configuration updated.",
            "port": address,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/settings/filename-format")
async def get_filename_settings(_auth=Depends(basic_auth)):
    """Get output filename format settings"""
    try:
        return settings_manager.get_filename_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/settings/filename-format")
async def update_filename_settings(req: dict, _auth=Depends(basic_auth)):
    """Update output filename format settings"""
    try:
        tag = req.get("tag")
        include_id = req.get("include_id")
        settings_manager.update_filename_settings(tag=tag, include_id=include_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
