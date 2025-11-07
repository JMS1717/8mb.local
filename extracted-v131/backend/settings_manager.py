"""
Settings manager for 8mb.local
Handles reading and writing configuration at runtime
"""
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
import json


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
            {"name":"H265 9.7MB (NVENC)", "target_mb":9.7, "video_codec":"hevc_nvenc", "audio_codec":"libopus", "preset":"p6", "audio_kbps":128, "container":"mp4", "tune":"hq"},
            {"name":"H264 8MB (NVENC)", "target_mb":8, "video_codec":"h264_nvenc", "audio_codec":"libopus", "preset":"p6", "audio_kbps":128, "container":"mp4", "tune":"hq"},
            {"name":"AV1 8MB (CPU)", "target_mb":8, "video_codec":"libaom-av1", "audio_codec":"libopus", "preset":"p6", "audio_kbps":128, "container":"mkv", "tune":"hq"},
            {"name":"H265 50MB HQ", "target_mb":50, "video_codec":"hevc_nvenc", "audio_codec":"aac", "preset":"p7", "audio_kbps":192, "container":"mp4", "tune":"hq"},
            {"name":"H264 25MB Fast", "target_mb":25, "video_codec":"h264_nvenc", "audio_codec":"aac", "preset":"p3", "audio_kbps":128, "container":"mp4", "tune":"ll"}
        ]
        changed = True
    # Ensure an AV1 NVENC profile exists even if preset_profiles already seeded earlier
    try:
        profiles = data.get('preset_profiles', [])
        has_av1_nvenc = any(p.get('video_codec') == 'av1_nvenc' for p in profiles)
        if not has_av1_nvenc:
            # Add a sensible default AV1 NVENC profile
            profiles.append({
                "name": "AV1 9.7MB (NVENC)",
                "target_mb": 9.7,
                "video_codec": "av1_nvenc",
                "audio_codec": "aac",
                "preset": "p6",
                "audio_kbps": 128,
                "container": "mp4",
                "tune": "hq"
            })
            data['preset_profiles'] = profiles
            changed = True
    except Exception:
        pass
    if 'default_preset' not in data:
        data['default_preset'] = 'H265 9.7MB (NVENC)'
        changed = True
    if 'retention_hours' not in data:
        # fallback to env if present
        env_vars = read_env_file()
        try:
            data['retention_hours'] = int(os.getenv('FILE_RETENTION_HOURS', env_vars.get('FILE_RETENTION_HOURS', '1')))
        except Exception:
            data['retention_hours'] = 1
        changed = True
    if changed:
        _write_settings(data)
    return data


def read_env_file() -> dict:
    """Read current .env file and return as dict"""
    if not ENV_FILE.exists():
        return {}
    
    # Check if it's a directory (common Docker mount issue)
    if ENV_FILE.is_dir():
        print(f"WARNING: {ENV_FILE} is a directory, not a file. Falling back to environment variables only.")
        print("To fix: Remove the directory and mount a proper .env file, or don't mount .env at all.")
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
        print(f"WARNING: Failed to read {ENV_FILE}: {e}")
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
            print(f"WARNING: Failed to write {ENV_FILE}: {e}. The file may be mounted read-only. Skipping .env write.")
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
            print(f"WARNING: Could not initialize {ENV_FILE}: {e}")


def get_default_presets() -> dict:
    """Get default preset values"""
    # Prefer settings.json default preset if present
    data = _ensure_defaults()
    default_name = data.get('default_preset')
    for p in data.get('preset_profiles', []):
        if p.get('name') == default_name:
            return {
                'target_mb': float(p.get('target_mb', 9.7)),
                'video_codec': p.get('video_codec', 'hevc_nvenc'),
                'audio_codec': p.get('audio_codec', 'libopus'),
                'preset': p.get('preset', 'p6'),
                'audio_kbps': int(p.get('audio_kbps', 128)),
                'container': p.get('container', 'mp4'),
                'tune': p.get('tune', 'hq')
            }
    # Fallback to legacy .env
    env_vars = read_env_file()
    return {
        'target_mb': float(os.getenv('DEFAULT_TARGET_MB', env_vars.get('DEFAULT_TARGET_MB', '9.7'))),
        'video_codec': os.getenv('DEFAULT_VIDEO_CODEC', env_vars.get('DEFAULT_VIDEO_CODEC', 'hevc_nvenc')),
        'audio_codec': os.getenv('DEFAULT_AUDIO_CODEC', env_vars.get('DEFAULT_AUDIO_CODEC', 'libopus')),
        'preset': os.getenv('DEFAULT_PRESET', env_vars.get('DEFAULT_PRESET', 'p6')),
        'audio_kbps': int(os.getenv('DEFAULT_AUDIO_KBPS', env_vars.get('DEFAULT_AUDIO_KBPS', '128'))),
        'container': os.getenv('DEFAULT_CONTAINER', env_vars.get('DEFAULT_CONTAINER', 'mp4')),
        'tune': os.getenv('DEFAULT_TUNE', env_vars.get('DEFAULT_TUNE', 'hq'))
    }


def update_default_presets(
    target_mb: float,
    video_codec: str,
    audio_codec: str,
    preset: str,
    audio_kbps: int,
    container: str,
    tune: str
):
    """Update default preset values by updating the default_preset profile or creating one."""
    data = _ensure_defaults()
    new_profile = {
        'name': data.get('default_preset', 'Custom Default'),
        'target_mb': float(target_mb),
        'video_codec': video_codec,
        'audio_codec': audio_codec,
        'preset': preset,
        'audio_kbps': int(audio_kbps),
        'container': container,
        'tune': tune,
    }
    # Replace if exists by name; else append and set as default
    replaced = False
    for i, p in enumerate(data['preset_profiles']):
        if p.get('name') == data['default_preset']:
            data['preset_profiles'][i] = new_profile
            replaced = True
            break
    if not replaced:
        data['preset_profiles'].append(new_profile)
        data['default_preset'] = new_profile['name']
    _write_settings(data)


def get_codec_visibility_settings() -> dict:
    """Get individual codec visibility settings"""
    env_vars = read_env_file()
    
    def get_bool(key: str, default: str = 'true') -> bool:
        return os.getenv(key, env_vars.get(key, default)).lower() == 'true'
    
    return {
        # NVIDIA
        'h264_nvenc': get_bool('CODEC_H264_NVENC'),
        'hevc_nvenc': get_bool('CODEC_HEVC_NVENC'),
        'av1_nvenc': get_bool('CODEC_AV1_NVENC'),
        # Intel QSV
        'h264_qsv': get_bool('CODEC_H264_QSV'),
        'hevc_qsv': get_bool('CODEC_HEVC_QSV'),
        'av1_qsv': get_bool('CODEC_AV1_QSV'),
        # AMD VAAPI
        'h264_vaapi': get_bool('CODEC_H264_VAAPI'),
        'hevc_vaapi': get_bool('CODEC_HEVC_VAAPI'),
        'av1_vaapi': get_bool('CODEC_AV1_VAAPI'),
        # AMD AMF
        'h264_amf': get_bool('CODEC_H264_AMF'),
        'hevc_amf': get_bool('CODEC_HEVC_AMF'),
        'av1_amf': get_bool('CODEC_AV1_AMF'),
        # CPU
        'libx264': get_bool('CODEC_LIBX264'),
        'libx265': get_bool('CODEC_LIBX265'),
        'libaom_av1': get_bool('CODEC_LIBAOM_AV1'),
    }


def update_codec_visibility_settings(settings: dict):
    """Update individual codec visibility settings in .env file"""
    env_vars = read_env_file()
    
    codec_keys = {
        'h264_nvenc': 'CODEC_H264_NVENC',
        'hevc_nvenc': 'CODEC_HEVC_NVENC',
        'av1_nvenc': 'CODEC_AV1_NVENC',
        'h264_qsv': 'CODEC_H264_QSV',
        'hevc_qsv': 'CODEC_HEVC_QSV',
        'av1_qsv': 'CODEC_AV1_QSV',
        'h264_vaapi': 'CODEC_H264_VAAPI',
        'hevc_vaapi': 'CODEC_HEVC_VAAPI',
        'av1_vaapi': 'CODEC_AV1_VAAPI',
        'h264_amf': 'CODEC_H264_AMF',
        'hevc_amf': 'CODEC_HEVC_AMF',
        'av1_amf': 'CODEC_AV1_AMF',
        'libx264': 'CODEC_LIBX264',
        'libx265': 'CODEC_LIBX265',
        'libaom_av1': 'CODEC_LIBAOM_AV1',
    }
    
    for codec_name, env_key in codec_keys.items():
        if codec_name in settings:
            value = 'true' if settings[codec_name] else 'false'
            env_vars[env_key] = value
            os.environ[env_key] = value
    
    write_env_file(env_vars)


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
