"""
Settings manager for 8mb.local
Handles reading and writing configuration at runtime
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


ENV_FILE = Path("/app/.env")
SETTINGS_FILE = Path("/app/settings.json")


def _read_settings() -> Dict[str, Any]:
    """Read JSON settings file (persistent across updates when volume-mounted)."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        with SETTINGS_FILE.open('r') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_settings(data: Dict[str, Any]):
    """Write JSON settings file safely."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS_FILE.open('w') as f:
            json.dump(data, f, indent=2)
        os.chmod(SETTINGS_FILE, 0o600)
    except Exception as e:
        raise RuntimeError(f"Failed to write settings.json: {e}")


def _ensure_defaults() -> Dict[str, Any]:
    """Ensure settings.json exists with sane defaults and return it."""
    data = _read_settings()
    changed = False
    if 'size_buttons' not in data:
        data['size_buttons'] = [4, 5, 8, 9.7, 20, 50, 100]
        changed = True
    if 'preset_profiles' not in data:
        data['preset_profiles'] = [
            # QSV (Intel Quick Sync) -- best quality/speed on Intel iGPUs
            {"name": "HEVC 9.7MB (QSV)", "target_mb": 9.7, "video_codec": "hevc_qsv", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            {"name": "H264 8MB (QSV)", "target_mb": 8, "video_codec": "h264_qsv", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            {"name": "HEVC 50MB HQ (QSV)", "target_mb": 50, "video_codec": "hevc_qsv", "audio_codec": "aac", "preset": "p7", "audio_kbps": 192, "container": "mp4", "tune": "hq"},
            # VAAPI -- fallback HW encoder
            {"name": "HEVC 9.7MB (VAAPI)", "target_mb": 9.7, "video_codec": "hevc_vaapi", "audio_codec": "libopus", "preset": "p5", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            {"name": "H264 8MB (VAAPI)", "target_mb": 8, "video_codec": "h264_vaapi", "audio_codec": "libopus", "preset": "p5", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            # NVENC (NVIDIA) -- kept for systems with NVIDIA GPU
            {"name": "AV1 9.7MB (NVENC)", "target_mb": 9.7, "video_codec": "av1_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            {"name": "HEVC 9.7MB (NVENC)", "target_mb": 9.7, "video_codec": "hevc_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            {"name": "H264 8MB (NVENC)", "target_mb": 8, "video_codec": "h264_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            # CPU
            {"name": "AV1 8MB (CPU)", "target_mb": 8, "video_codec": "libsvtav1", "audio_codec": "libopus", "preset": "p4", "audio_kbps": 128, "container": "mkv", "tune": "hq"},
        ]
        changed = True
    if 'default_preset' not in data:
        data['default_preset'] = _pick_initial_default(data.get('preset_profiles', []))
        changed = True
    if 'codec_visibility' not in data:
        data['codec_visibility'] = {
            'h264_qsv': True,
            'hevc_qsv': True,
            'av1_qsv': True,
            'h264_vaapi': True,
            'hevc_vaapi': True,
            'av1_vaapi': True,
            'h264_nvenc': True,
            'hevc_nvenc': True,
            'av1_nvenc': True,
            'libx264': True,
            'libx265': True,
            'libsvtav1': True,
        }
        changed = True
    if 'retention_hours' not in data:
        env_vars = read_env_file()
        try:
            data['retention_hours'] = int(os.getenv('FILE_RETENTION_HOURS', env_vars.get('FILE_RETENTION_HOURS', '1')))
        except Exception:
            data['retention_hours'] = 1
        changed = True
    if changed:
        _write_settings(data)
    return data


def _pick_initial_default(profiles: List[Dict[str, Any]]) -> str:
    """Pick the best initial default preset name from available profiles.

    Priority: HEVC QSV > H264 QSV > HEVC VAAPI > H264 VAAPI > AV1 NVENC > HEVC NVENC > H264 NVENC > CPU > first profile.
    """
    codec_priority = ['hevc_qsv', 'h264_qsv', 'hevc_vaapi', 'h264_vaapi',
                       'av1_nvenc', 'hevc_nvenc', 'h264_nvenc',
                       'libsvtav1', 'libx265', 'libx264']
    for codec in codec_priority:
        for p in profiles:
            if p.get('video_codec') == codec:
                return p.get('name', 'Default')
    if profiles:
        return profiles[0].get('name', 'Default')
    return 'Default'


def read_env_file() -> dict:
    """Read current .env file and return as dict"""
    if not ENV_FILE.exists():
        return {}
    
    # Check if it's a directory (common Docker mount issue)
    if ENV_FILE.is_dir():
        logger.warning(f"WARNING: {ENV_FILE} is a directory, not a file. Falling back to environment variables only.")
        logger.warning("To fix: Remove the directory and mount a proper .env file, or don't mount .env at all.")
        return {}
    
    env_vars = {}
    try:
        with open(ENV_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    except Exception as e:
        logger.warning(f"WARNING: Failed to read {ENV_FILE}: {e}")
        return {}
    
    return env_vars


def write_env_file(env_vars: dict):
    """Write env vars to .env file"""
    # Check if it's a directory (common Docker mount issue)
    if ENV_FILE.exists() and ENV_FILE.is_dir():
        raise RuntimeError(f"{ENV_FILE} is a directory. Cannot write settings. Remove the directory or fix your Docker mount.")
    
    # Create parent directory if needed
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(ENV_FILE, 'w') as f:
            for key, value in env_vars.items():
                f.write(f"{key}={value}\n")
        os.chmod(ENV_FILE, 0o600)
    except Exception as e:
        # Gracefully handle read-only filesystems or permission issues when .env is mounted :ro
        msg = str(e)
        if isinstance(e, PermissionError) or 'Read-only file system' in msg or 'EROFS' in msg:
            # Don't fail the request – settings that are JSON-backed will still persist
            logger.warning(f"WARNING: Failed to write {ENV_FILE}: {e}. The file may be mounted read-only. Skipping .env write.")
            return
        raise RuntimeError(f"Failed to write {ENV_FILE}: {e}")


def get_auth_settings() -> dict:
    """Get current auth settings"""
    env_vars = read_env_file()
    
    # Also check environment variables (higher priority)
    auth_enabled = os.getenv('AUTH_ENABLED', env_vars.get('AUTH_ENABLED', 'false'))
    auth_user = os.getenv('AUTH_USER', env_vars.get('AUTH_USER', ''))
    
    return {
        'auth_enabled': auth_enabled.lower() in ('true', '1', 'yes'),
        'auth_user': auth_user if auth_user else None
    }


def update_auth_settings(auth_enabled: bool, auth_user: Optional[str] = None, auth_pass: Optional[str] = None):
    """Update auth settings in .env file"""
    env_vars = read_env_file()
    
    # Update auth enabled
    env_vars['AUTH_ENABLED'] = 'true' if auth_enabled else 'false'
    
    # Update username if provided
    if auth_user is not None:
        env_vars['AUTH_USER'] = auth_user
    
    # Update password if provided
    if auth_pass is not None:
        env_vars['AUTH_PASS'] = auth_pass
    
    # Ensure other defaults exist
    env_vars.setdefault('FILE_RETENTION_HOURS', '1')
    env_vars.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/0')
    env_vars.setdefault('BACKEND_HOST', '0.0.0.0')
    env_vars.setdefault('BACKEND_PORT', '8001')
    # Enable history by default
    env_vars.setdefault('HISTORY_ENABLED', 'true')
    
    write_env_file(env_vars)
    
    # Update environment variables for current process
    os.environ['AUTH_ENABLED'] = 'true' if auth_enabled else 'false'
    if auth_user:
        os.environ['AUTH_USER'] = auth_user
    if auth_pass:
        os.environ['AUTH_PASS'] = auth_pass


def verify_password(password: str) -> bool:
    """Verify if password matches current AUTH_PASS"""
    env_vars = read_env_file()
    current_pass = os.getenv('AUTH_PASS', env_vars.get('AUTH_PASS', 'changeme'))
    return password == current_pass


def initialize_env_if_missing():
    """Initialize .env file with defaults if it doesn't exist"""
    if not ENV_FILE.exists():
        default_env = {
            'AUTH_ENABLED': 'false',
            'FILE_RETENTION_HOURS': '1',
            'REDIS_URL': 'redis://127.0.0.1:6379/0',
            'BACKEND_HOST': '0.0.0.0',
            'BACKEND_PORT': '8001',
            # History on by default
            'HISTORY_ENABLED': 'true'
        }
        try:
            write_env_file(default_env)
        except Exception as e:
            logger.warning(f"WARNING: Could not initialize {ENV_FILE}: {e}")


def _profile_to_dict(p: Dict[str, Any]) -> dict:
    """Extract the API-facing fields from a preset profile."""
    return {
        'target_mb': float(p.get('target_mb', 9.7)),
        'video_codec': p.get('video_codec', 'av1_nvenc'),
        'audio_codec': p.get('audio_codec', 'libopus'),
        'preset': p.get('preset', 'p6'),
        'audio_kbps': int(p.get('audio_kbps', 128)),
        'container': p.get('container', 'mp4'),
        'tune': p.get('tune', 'hq'),
    }


def get_default_presets() -> dict:
    """Return the user's saved default preset from settings.json.

    The saved ``default_preset`` (profile name) is the single source of truth.
    Hardware detection only influences the *initial* seed (first boot) via
    ``_pick_initial_default`` inside ``_ensure_defaults``.
    """
    data = _ensure_defaults()
    default_name = data.get('default_preset')
    profiles = data.get('preset_profiles', [])

    # 1) Match by saved default_preset name
    if default_name:
        for p in profiles:
            if p.get('name') == default_name:
                return _profile_to_dict(p)

    # 2) Fallback: pick best available profile (NVENC priority)
    if profiles:
        best = _pick_initial_default(profiles)
        for p in profiles:
            if p.get('name') == best:
                return _profile_to_dict(p)
        return _profile_to_dict(profiles[0])

    # 3) Absolute fallback
    return {
        'target_mb': 9.7,
        'video_codec': 'av1_nvenc',
        'audio_codec': 'libopus',
        'preset': 'p6',
        'audio_kbps': 128,
        'container': 'mp4',
        'tune': 'hq',
    }


def update_default_presets(
    target_mb: float,
    video_codec: str,
    audio_codec: str,
    preset: str,
    audio_kbps: int,
    container: str,
    tune: str,
):
    """Update the current default preset profile's values in settings.json."""
    data = _ensure_defaults()
    default_name = data.get('default_preset', 'Custom Default')
    new_values = {
        'name': default_name,
        'target_mb': float(target_mb),
        'video_codec': video_codec,
        'audio_codec': audio_codec,
        'preset': preset,
        'audio_kbps': int(audio_kbps),
        'container': container,
        'tune': tune,
    }
    replaced = False
    for i, p in enumerate(data['preset_profiles']):
        if p.get('name') == default_name:
            data['preset_profiles'][i] = new_values
            replaced = True
            break
    if not replaced:
        data['preset_profiles'].append(new_values)
        data['default_preset'] = default_name
    _write_settings(data)


def get_codec_visibility_settings() -> dict:
    """Get codec visibility from settings.json (persists reliably)."""
    data = _ensure_defaults()
    vis = data.get('codec_visibility', {})
    return {
        'h264_nvenc': vis.get('h264_nvenc', True),
        'hevc_nvenc': vis.get('hevc_nvenc', True),
        'av1_nvenc': vis.get('av1_nvenc', True),
        'h264_qsv': vis.get('h264_qsv', True),
        'hevc_qsv': vis.get('hevc_qsv', True),
        'av1_qsv': vis.get('av1_qsv', True),
        'h264_vaapi': vis.get('h264_vaapi', True),
        'hevc_vaapi': vis.get('hevc_vaapi', True),
        'av1_vaapi': vis.get('av1_vaapi', True),
        'libx264': vis.get('libx264', True),
        'libx265': vis.get('libx265', True),
        'libsvtav1': vis.get('libsvtav1', True),
    }


def update_codec_visibility_settings(settings: dict):
    """Update codec visibility in settings.json."""
    data = _ensure_defaults()
    vis = data.get('codec_visibility', {})
    valid_keys = {'h264_nvenc', 'hevc_nvenc', 'av1_nvenc', 'h264_qsv', 'hevc_qsv', 'av1_qsv', 'h264_vaapi', 'hevc_vaapi', 'av1_vaapi', 'libx264', 'libx265', 'libsvtav1'}
    for k in valid_keys:
        if k in settings:
            vis[k] = bool(settings[k])
    data['codec_visibility'] = vis
    _write_settings(data)


def get_history_enabled() -> bool:
    """Get history enabled setting"""
    env_vars = read_env_file()
    # Default ON if not set
    history_enabled = os.getenv('HISTORY_ENABLED', env_vars.get('HISTORY_ENABLED', 'true'))
    return history_enabled.lower() in ('true', '1', 'yes')


def update_history_enabled(enabled: bool):
    """Update history enabled setting in .env file"""
    env_vars = read_env_file()
    env_vars['HISTORY_ENABLED'] = 'true' if enabled else 'false'
    write_env_file(env_vars)
    os.environ['HISTORY_ENABLED'] = 'true' if enabled else 'false'


# New JSON-backed settings accessors
def get_size_buttons() -> List[float]:
    data = _ensure_defaults()
    return [float(x) for x in data.get('size_buttons', [])]


def update_size_buttons(buttons: List[float]):
    if not isinstance(buttons, list) or not all(isinstance(x, (int, float)) for x in buttons):
        raise ValueError("buttons must be a list of numbers")
    data = _ensure_defaults()
    # dedupe & sort ascending
    cleaned = sorted({round(float(x), 2) for x in buttons})
    data['size_buttons'] = list(cleaned)
    _write_settings(data)


def get_preset_profiles() -> Dict[str, Any]:
    data = _ensure_defaults()
    return { 'profiles': data.get('preset_profiles', []), 'default': data.get('default_preset') }


def set_default_preset(name: str):
    data = _ensure_defaults()
    names = {p.get('name') for p in data.get('preset_profiles', [])}
    if name not in names:
        raise ValueError("preset not found")
    data['default_preset'] = name
    _write_settings(data)


def add_preset_profile(profile: Dict[str, Any]):
    required = {'name','target_mb','video_codec','audio_codec','preset','audio_kbps','container','tune'}
    if not required.issubset(profile.keys()):
        raise ValueError("missing fields in preset profile")
    data = _ensure_defaults()
    # prevent duplicate names
    if any(p.get('name') == profile['name'] for p in data['preset_profiles']):
        raise ValueError("preset name already exists")
    data['preset_profiles'].append(profile)
    _write_settings(data)


def update_preset_profile(name: str, updates: Dict[str, Any]):
    data = _ensure_defaults()
    for i, p in enumerate(data['preset_profiles']):
        if p.get('name') == name:
            data['preset_profiles'][i] = { **p, **{k:v for k,v in updates.items() if k != 'name'} }
            _write_settings(data)
            return
    raise ValueError("preset not found")


def delete_preset_profile(name: str):
    data = _ensure_defaults()
    before = len(data['preset_profiles'])
    data['preset_profiles'] = [p for p in data['preset_profiles'] if p.get('name') != name]
    if len(data['preset_profiles']) == before:
        raise ValueError("preset not found")
    # if default removed, reset to first if exists
    if data.get('default_preset') == name:
        data['default_preset'] = data['preset_profiles'][0]['name'] if data['preset_profiles'] else None
    _write_settings(data)


def get_retention_hours() -> int:
    data = _ensure_defaults()
    try:
        return int(data.get('retention_hours', 1))
    except Exception:
        return 1


def update_retention_hours(hours: int):
    if hours < 0:
        raise ValueError("retention hours must be >= 0")
    data = _ensure_defaults()
    data['retention_hours'] = int(hours)
    _write_settings(data)


def get_worker_concurrency() -> int:
    """Get worker concurrency setting"""
    env_vars = read_env_file()
    try:
        return int(os.getenv('WORKER_CONCURRENCY', env_vars.get('WORKER_CONCURRENCY', '4')))
    except Exception:
        return 4


def update_worker_concurrency(concurrency: int):
    """Update worker concurrency setting in .env file"""
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    if concurrency > 20:
        raise ValueError("concurrency should not exceed 20 for stability")
    
    env_vars = read_env_file()
    env_vars['WORKER_CONCURRENCY'] = str(concurrency)
    write_env_file(env_vars)
    os.environ['WORKER_CONCURRENCY'] = str(concurrency)
