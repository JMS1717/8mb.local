"""
Settings manager for 8mb.local
Handles reading and writing configuration at runtime
"""
from __future__ import annotations

import json
import logging
import math
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings
from shared.concurrency import worker_concurrency_details

logger = logging.getLogger(__name__)


_APP_DATA_DIR = Path(settings.APP_DATA_DIR)
ENV_FILE = Path(os.getenv("ENV_FILE", str(_APP_DATA_DIR / ".env")))
SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", str(_APP_DATA_DIR / "settings.json")))

_DEFAULT_SIZE_BUTTONS = [4, 5, 8, 9.7, 19.7, 50, 100]
_LEGACY_STOCK_SIZE_BUTTONS = [4, 5, 8, 9.7, 20, 50, 100]

_FOLDER_WATCH_DEFAULTS: dict[str, Any] = {
    'enabled': False,
    'input_folder': '',
    'profile': None,
    'output_mode': 'same_folder',
    'output_folder': '',
    'original_behavior': 'keep',
    'existing_files': 'new_only',
    'recursive': False,
    'stable_seconds': 5,
    'poll_interval_seconds': 5,
    'baseline_ts': 0.0,
}


def _default_codec_visibility() -> dict[str, bool]:
    """Return a fresh visibility map for new or damaged settings files."""
    return {
        'h264_nvenc': True,
        'hevc_nvenc': True,
        'av1_nvenc': True,
        'h264_qsv': True,
        'hevc_qsv': True,
        'av1_qsv': True,
        'h264_vaapi': True,
        'hevc_vaapi': True,
        'av1_vaapi': True,
        'h264_amf': True,
        'hevc_amf': True,
        'av1_amf': True,
        'libx264': True,
        'libx265': True,
        'libsvtav1': True,
        # libaom-av1 is opt-in (slow); SVT-AV1 is the default CPU AV1 path.
        'libaom_av1': False,
    }


def _legacy_stock_profiles() -> list[dict[str, Any]]:
    """Return the exact pre-v140 stock profile list used for safe migration."""
    return [
        {"name": "AV1 9.7MB (NVENC)", "target_mb": 9.7, "video_codec": "av1_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
        {"name": "HEVC 9.7MB (NVENC)", "target_mb": 9.7, "video_codec": "hevc_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
        {"name": "H264 8MB (NVENC)", "target_mb": 8, "video_codec": "h264_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
        {"name": "HEVC 50MB HQ (NVENC)", "target_mb": 50, "video_codec": "hevc_nvenc", "audio_codec": "aac", "preset": "p7", "audio_kbps": 192, "container": "mp4", "tune": "hq"},
        {"name": "H264 25MB Fast (NVENC)", "target_mb": 25, "video_codec": "h264_nvenc", "audio_codec": "aac", "preset": "p3", "audio_kbps": 128, "container": "mp4", "tune": "ll"},
        {"name": "AV1 9.7MB (SVT-AV1, CPU)", "target_mb": 9.7, "video_codec": "libsvtav1", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mkv", "tune": "hq"},
    ]


def _default_preset_profiles() -> list[dict[str, Any]]:
    """Return stock profiles for a new install, including Discord headroom."""
    return [
        {"name": "Discord 19.7 MB", "target_mb": 19.7, "video_codec": "h264_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
        *_legacy_stock_profiles(),
    ]


def _is_untouched_legacy_stock(data: Dict[str, Any]) -> bool:
    """Only migrate settings that still exactly match the old stock set."""
    if data.get('size_buttons') not in (_LEGACY_STOCK_SIZE_BUTTONS, _DEFAULT_SIZE_BUTTONS):
        return False
    if data.get('preset_profiles') != _legacy_stock_profiles():
        return False
    default_name = data.get('default_preset')
    if default_name and default_name not in {p['name'] for p in _legacy_stock_profiles()}:
        return False
    return data.get('default_preset_managed', True) is not False


def _read_settings() -> Dict[str, Any]:
    """Read JSON settings file (persistent across updates when volume-mounted)."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        with SETTINGS_FILE.open('r') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_settings(data: Dict[str, Any]):
    """Write JSON settings file safely."""
    temp_path: Path | None = None
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=SETTINGS_FILE.parent,
            prefix=f'.{SETTINGS_FILE.name}.', suffix='.tmp', delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(data, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, SETTINGS_FILE)
        temp_path = None
    except Exception as e:
        raise RuntimeError(f"Failed to write settings.json: {e}")
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _ensure_defaults() -> Dict[str, Any]:
    """Ensure settings.json exists with sane defaults and return it."""
    data = _read_settings()
    changed = False
    created_profiles = False
    legacy_stock_candidate = (
        data.get('preset_profiles') == _legacy_stock_profiles()
        and data.get('size_buttons', _LEGACY_STOCK_SIZE_BUTTONS) == _LEGACY_STOCK_SIZE_BUTTONS
        and data.get('default_preset_managed', True) is not False
    )
    if 'size_buttons' not in data:
        data['size_buttons'] = list(_DEFAULT_SIZE_BUTTONS)
        changed = True
    if 'preset_profiles' not in data:
        data['preset_profiles'] = _default_preset_profiles()
        created_profiles = True
        changed = True
    # An existing profile list is user-owned. Do not silently reinsert a stock
    # NVENC profile after the user edits or deletes it. The complete stock list
    # is created only when preset_profiles is absent on first initialization.
    if 'default_preset' not in data:
        data['default_preset'] = (
            'Discord 19.7 MB' if created_profiles
            else _pick_initial_default(data.get('preset_profiles', []))
        )
        changed = True
    if 'default_preset_managed' not in data:
        # A legacy settings file may contain an explicit user choice. Only
        # treat known stock defaults as application-managed; never replace a
        # custom preset during an upgrade merely because hardware appeared.
        stock_names = {
            "Discord 19.7 MB",
            "AV1 9.7MB (NVENC)",
            "AV1 9.7MB (SVT-AV1, CPU)",
            "H.264 9.7MB (NVENC)",
            "H.264 9.7MB (CPU)",
        }
        data['default_preset_managed'] = data.get('default_preset') in stock_names
        changed = True

    if legacy_stock_candidate and _is_untouched_legacy_stock(data):
        data['size_buttons'] = list(_DEFAULT_SIZE_BUTTONS)
        data['preset_profiles'] = [
            {"name": "Discord 19.7 MB", "target_mb": 19.7, "video_codec": "h264_nvenc", "audio_codec": "libopus", "preset": "p6", "audio_kbps": 128, "container": "mp4", "tune": "hq"},
            *data['preset_profiles'],
        ]
        data['default_preset'] = 'Discord 19.7 MB'
        data['default_preset_managed'] = True
        changed = True
    visibility = data.get('codec_visibility')
    if not isinstance(visibility, dict):
        # A hand-edited or truncated settings file must not prevent startup.
        # Replace only the damaged section; keep profiles and other settings.
        data['codec_visibility'] = _default_codec_visibility()
        changed = True
    else:
        # Backfill visibility keys for configs that pre-date newer codecs.
        for key, default in _default_codec_visibility().items():
            if key not in visibility:
                visibility[key] = default
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

    Priority: NVENC > QSV > AMF > VAAPI > CPU > first profile.
    """
    codec_priority = ['av1_nvenc', 'hevc_nvenc', 'h264_nvenc',
                       'av1_qsv', 'hevc_qsv', 'h264_qsv',
                       'av1_amf', 'hevc_amf', 'h264_amf',
                       'av1_vaapi', 'hevc_vaapi', 'h264_vaapi',
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
    return secrets.compare_digest(str(password), str(current_pass))


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
        'target_mb': float(p.get('target_mb', 19.7)),
        'video_codec': p.get('video_codec', 'h264_nvenc'),
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
        'target_mb': 19.7,
        'video_codec': 'h264_nvenc',
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
    existing_profile = next(
        (p for p in data.get('preset_profiles', []) if p.get('name') == default_name),
        None,
    )
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
    # This form does not expose the profile frame-rate control. Preserve a cap
    # previously saved through the profile editor instead of silently erasing it.
    if existing_profile and 'max_output_fps' in existing_profile:
        new_values['max_output_fps'] = existing_profile['max_output_fps']
    replaced = False
    for i, p in enumerate(data['preset_profiles']):
        if p.get('name') == default_name:
            data['preset_profiles'][i] = new_values
            replaced = True
            break
    if not replaced:
        data['preset_profiles'].append(new_values)
        data['default_preset'] = default_name
    data['default_preset_managed'] = False
    _write_settings(data)


def get_codec_visibility_settings() -> dict:
    """Get codec visibility from settings.json (persists reliably)."""
    data = _ensure_defaults()
    vis = data.get('codec_visibility', {})
    if not isinstance(vis, dict):
        vis = _default_codec_visibility()

    def as_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return default

    defaults = _default_codec_visibility()
    result = {
        key: as_bool(vis.get(key), default)
        for key, default in defaults.items()
    }
    # Recover safely from a manually corrupted file that disabled every CPU
    # encoder. The write-path already rejects this; the read-path must also
    # keep the UI and backend usable.
    if not any(result[key] for key in ('libx264', 'libx265', 'libsvtav1')):
        result['libx264'] = True
    return result


def update_codec_visibility_settings(settings: dict):
    """Update codec visibility in settings.json."""
    data = _ensure_defaults()
    vis = data.get('codec_visibility', {})
    valid_keys = {
        'h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
        'h264_qsv', 'hevc_qsv', 'av1_qsv',
        'h264_vaapi', 'hevc_vaapi', 'av1_vaapi',
        'h264_amf', 'hevc_amf', 'av1_amf',
        'libx264', 'libx265', 'libsvtav1', 'libaom_av1',
    }
    candidate = {
        key: bool(settings[key]) if key in settings else bool(vis.get(key, True))
        for key in valid_keys
    }
    if not any(candidate[key] for key in ('libx264', 'libx265', 'libsvtav1')):
        raise ValueError('At least one CPU codec must remain enabled')
    for k in valid_keys:
        if k in settings:
            vis[k] = bool(settings[k])
    data['codec_visibility'] = vis
    logger.debug("update_codec_visibility_settings: stored=%s", vis)
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
    if not isinstance(buttons, list) or not buttons or not all(isinstance(x, (int, float)) for x in buttons):
        raise ValueError("buttons must be a non-empty list of numbers")
    if not all(math.isfinite(float(x)) and 0 < float(x) <= 51200 for x in buttons):
        raise ValueError("size buttons must be finite values between 0 and 51200 MB")
    data = _ensure_defaults()
    # dedupe & sort ascending
    cleaned = sorted({round(float(x), 2) for x in buttons})
    data['size_buttons'] = list(cleaned)
    _write_settings(data)


def get_preset_profiles() -> Dict[str, Any]:
    data = _ensure_defaults()
    return { 'profiles': data.get('preset_profiles', []), 'default': data.get('default_preset') }


def get_folder_watch_settings() -> dict[str, Any]:
    """Return the persisted folder-watch configuration without runtime state."""
    data = _ensure_defaults()
    stored = data.get('folder_watch', {})
    if not isinstance(stored, dict):
        stored = {}
    result = dict(_FOLDER_WATCH_DEFAULTS)
    result.update({k: v for k, v in stored.items() if k in result})
    return result


def update_folder_watch_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist folder-watch settings.

    The watcher is intentionally configured, rather than given a public
    arbitrary-path processing endpoint.  Paths are validated only when the
    feature is enabled, so a disabled configuration can be prepared before a
    mounted folder exists.
    """
    data = _ensure_defaults()
    current = get_folder_watch_settings()
    merged = dict(current)
    merged.update({k: values[k] for k in _FOLDER_WATCH_DEFAULTS if k in values})

    merged['enabled'] = bool(merged['enabled'])
    merged['input_folder'] = str(merged.get('input_folder') or '').strip()
    merged['output_folder'] = str(merged.get('output_folder') or '').strip()
    merged['profile'] = str(merged['profile']).strip() if merged.get('profile') else None
    merged['output_mode'] = str(merged.get('output_mode', 'same_folder'))
    merged['original_behavior'] = str(merged.get('original_behavior', 'keep'))
    merged['existing_files'] = str(merged.get('existing_files', 'new_only'))
    merged['recursive'] = bool(merged.get('recursive', False))
    try:
        merged['stable_seconds'] = int(merged.get('stable_seconds', 5))
        merged['poll_interval_seconds'] = int(merged.get('poll_interval_seconds', 5))
    except (TypeError, ValueError) as exc:
        raise ValueError('Folder Watch timing values must be integers') from exc

    if merged['output_mode'] not in {'same_folder', 'specific_folder'}:
        raise ValueError('output_mode must be same_folder or specific_folder')
    if merged['original_behavior'] not in {'keep', 'delete', 'move'}:
        raise ValueError('original_behavior must be keep, delete, or move')
    if merged['existing_files'] not in {'new_only', 'process_existing'}:
        raise ValueError('existing_files must be new_only or process_existing')
    if not 2 <= merged['stable_seconds'] <= 60:
        raise ValueError('stable_seconds must be between 2 and 60')
    if not 2 <= merged['poll_interval_seconds'] <= 300:
        raise ValueError('poll_interval_seconds must be between 2 and 300')

    profile_names = {p.get('name') for p in data.get('preset_profiles', [])}
    if merged['profile'] is not None and merged['profile'] not in profile_names:
        raise ValueError('Folder Watch profile was not found')

    if merged['enabled']:
        input_path = Path(merged['input_folder']).expanduser()
        if not input_path.is_absolute() or not input_path.is_dir() or not os.access(input_path, os.R_OK):
            raise ValueError('Folder Watch input_folder must be an existing readable absolute directory')
        if merged['output_mode'] == 'specific_folder':
            output_path = Path(merged['output_folder']).expanduser()
            if not output_path.is_absolute() or not output_path.is_dir() or not os.access(output_path, os.W_OK):
                raise ValueError('Folder Watch output_folder must be an existing writable absolute directory')

    old_input = str(current.get('input_folder') or '')
    old_enabled = bool(current.get('enabled'))
    if merged['enabled'] and merged['existing_files'] == 'new_only' and (
        not old_enabled or old_input != merged['input_folder']
    ):
        merged['baseline_ts'] = time.time()
    elif not merged['enabled']:
        merged['baseline_ts'] = float(current.get('baseline_ts') or 0.0)
    else:
        merged['baseline_ts'] = float(current.get('baseline_ts') or 0.0)

    data['folder_watch'] = merged
    _write_settings(data)
    return dict(merged)


def get_folder_watch_state() -> dict[str, dict[str, Any]]:
    data = _ensure_defaults()
    state = data.get('folder_watch_state', {})
    return dict(state) if isinstance(state, dict) else {}


def update_folder_watch_state(path: str, record: dict[str, Any]) -> None:
    """Persist a bounded per-file state map used for exactly-once discovery."""
    data = _ensure_defaults()
    state = data.get('folder_watch_state', {})
    if not isinstance(state, dict):
        state = {}
    state[str(path)] = {**record, 'updated_at': time.time()}
    if len(state) > 1000:
        oldest = sorted(state.items(), key=lambda item: float(item[1].get('updated_at', 0)))
        for old_path, _ in oldest[:len(state) - 1000]:
            state.pop(old_path, None)
    data['folder_watch_state'] = state
    _write_settings(data)


def set_default_preset(name: str):
    data = _ensure_defaults()
    names = {p.get('name') for p in data.get('preset_profiles', [])}
    if name not in names:
        raise ValueError("preset not found")
    data['default_preset'] = name
    data['default_preset_managed'] = False
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
    return int(get_worker_concurrency_details()["concurrency"])


def get_worker_concurrency_details() -> dict[str, Any]:
    env_vars = read_env_file()
    configured = os.getenv('WORKER_CONCURRENCY', env_vars.get('WORKER_CONCURRENCY', 'auto'))
    return worker_concurrency_details(configured)


def update_worker_concurrency(concurrency: int | str, mode: str | None = None):
    """Update automatic or explicit worker concurrency in .env."""
    selected = str(concurrency).strip().lower()
    if mode and mode.strip().lower() == 'auto':
        selected = 'auto'
    if selected in {'automatic', 'system', ''}:
        selected = 'auto'
    elif selected != 'auto':
        try:
            value = int(selected)
        except ValueError as exc:
            raise ValueError("concurrency must be 'auto' or an integer from 1 to 20") from exc
        if value < 1:
            raise ValueError("concurrency must be >= 1")
        if value > 20:
            raise ValueError("concurrency should not exceed 20 for stability")
        selected = str(value)
    
    env_vars = read_env_file()
    env_vars['WORKER_CONCURRENCY'] = selected
    write_env_file(env_vars)
    os.environ['WORKER_CONCURRENCY'] = selected
