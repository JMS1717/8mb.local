"""
Settings manager for 8mb.local
Handles reading and writing configuration at runtime
"""
import os
from pathlib import Path
from typing import Optional


ENV_FILE = Path("/app/.env")


def read_env_file() -> dict:
    """Read current .env file and return as dict"""
    if not ENV_FILE.exists():
        return {}
    
    env_vars = {}
    with open(ENV_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip()
    return env_vars


def write_env_file(env_vars: dict):
    """Write env vars to .env file"""
    with open(ENV_FILE, 'w') as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    os.chmod(ENV_FILE, 0o600)


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
    env_vars.setdefault('BACKEND_PORT', '8000')
    
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
            'BACKEND_PORT': '8000'
        }
        write_env_file(default_env)


def get_default_presets() -> dict:
    """Get default preset values"""
    env_vars = read_env_file()
    
    return {
        'target_mb': int(os.getenv('DEFAULT_TARGET_MB', env_vars.get('DEFAULT_TARGET_MB', '25'))),
        'video_codec': os.getenv('DEFAULT_VIDEO_CODEC', env_vars.get('DEFAULT_VIDEO_CODEC', 'av1_nvenc')),
        'audio_codec': os.getenv('DEFAULT_AUDIO_CODEC', env_vars.get('DEFAULT_AUDIO_CODEC', 'libopus')),
        'preset': os.getenv('DEFAULT_PRESET', env_vars.get('DEFAULT_PRESET', 'p6')),
        'audio_kbps': int(os.getenv('DEFAULT_AUDIO_KBPS', env_vars.get('DEFAULT_AUDIO_KBPS', '128'))),
        'container': os.getenv('DEFAULT_CONTAINER', env_vars.get('DEFAULT_CONTAINER', 'mp4')),
        'tune': os.getenv('DEFAULT_TUNE', env_vars.get('DEFAULT_TUNE', 'hq'))
    }


def update_default_presets(
    target_mb: int,
    video_codec: str,
    audio_codec: str,
    preset: str,
    audio_kbps: int,
    container: str,
    tune: str
):
    """Update default preset values in .env file"""
    env_vars = read_env_file()
    
    env_vars['DEFAULT_TARGET_MB'] = str(target_mb)
    env_vars['DEFAULT_VIDEO_CODEC'] = video_codec
    env_vars['DEFAULT_AUDIO_CODEC'] = audio_codec
    env_vars['DEFAULT_PRESET'] = preset
    env_vars['DEFAULT_AUDIO_KBPS'] = str(audio_kbps)
    env_vars['DEFAULT_CONTAINER'] = container
    env_vars['DEFAULT_TUNE'] = tune
    
    write_env_file(env_vars)
    
    # Update environment variables for current process
    os.environ['DEFAULT_TARGET_MB'] = str(target_mb)
    os.environ['DEFAULT_VIDEO_CODEC'] = video_codec
    os.environ['DEFAULT_AUDIO_CODEC'] = audio_codec
    os.environ['DEFAULT_PRESET'] = preset
    os.environ['DEFAULT_AUDIO_KBPS'] = str(audio_kbps)
    os.environ['DEFAULT_CONTAINER'] = container
    os.environ['DEFAULT_TUNE'] = tune


def get_codec_visibility_settings() -> dict:
    """Get codec visibility settings"""
    env_vars = read_env_file()
    
    return {
        'show_nvidia': os.getenv('SHOW_NVIDIA_CODECS', env_vars.get('SHOW_NVIDIA_CODECS', 'true')).lower() == 'true',
        'show_intel': os.getenv('SHOW_INTEL_CODECS', env_vars.get('SHOW_INTEL_CODECS', 'true')).lower() == 'true',
        'show_amd': os.getenv('SHOW_AMD_CODECS', env_vars.get('SHOW_AMD_CODECS', 'true')).lower() == 'true',
        'show_cpu': os.getenv('SHOW_CPU_CODECS', env_vars.get('SHOW_CPU_CODECS', 'true')).lower() == 'true',
    }


def update_codec_visibility_settings(show_nvidia: bool, show_intel: bool, show_amd: bool, show_cpu: bool):
    """Update codec visibility settings in .env file"""
    env_vars = read_env_file()
    
    env_vars['SHOW_NVIDIA_CODECS'] = 'true' if show_nvidia else 'false'
    env_vars['SHOW_INTEL_CODECS'] = 'true' if show_intel else 'false'
    env_vars['SHOW_AMD_CODECS'] = 'true' if show_amd else 'false'
    env_vars['SHOW_CPU_CODECS'] = 'true' if show_cpu else 'false'
    
    write_env_file(env_vars)
    
    # Update environment variables for current process
    os.environ['SHOW_NVIDIA_CODECS'] = 'true' if show_nvidia else 'false'
    os.environ['SHOW_INTEL_CODECS'] = 'true' if show_intel else 'false'
    os.environ['SHOW_AMD_CODECS'] = 'true' if show_amd else 'false'
    os.environ['SHOW_CPU_CODECS'] = 'true' if show_cpu else 'false'
