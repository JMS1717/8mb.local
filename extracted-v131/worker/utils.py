import json
import os
import subprocess
from typing import Optional


def get_gpu_env():
    """
    Get environment with NVIDIA GPU variables and library paths for subprocess calls.
    """
    env = os.environ.copy()
    env['NVIDIA_VISIBLE_DEVICES'] = env.get('NVIDIA_VISIBLE_DEVICES', 'all')
    env['NVIDIA_DRIVER_CAPABILITIES'] = env.get('NVIDIA_DRIVER_CAPABILITIES', 'compute,video,utility')
    lib_paths = [
        '/usr/local/nvidia/lib64',
        '/usr/local/nvidia/lib',
        '/usr/local/cuda/lib64',
        '/usr/local/cuda/lib',
        '/usr/lib/wsl/lib',
        '/usr/lib/x86_64-linux-gnu',
    ]
    existing = env.get('LD_LIBRARY_PATH', '')
    add = ':'.join(p for p in lib_paths if p)
    env['LD_LIBRARY_PATH'] = (existing + (':' if existing and add else '') + add) if (existing or add) else ''
    return env


def ffprobe_info(input_path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=index,codec_type,codec_name,bit_rate",
        "-of", "json",
        input_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=get_gpu_env())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    data = json.loads(proc.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    v_bitrate = None
    a_bitrate = None
    v_codec = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and s.get("bit_rate"):
            v_bitrate = float(s["bit_rate"]) / 1000.0
            v_codec = s.get("codec_name")
        if s.get("codec_type") == "audio" and s.get("bit_rate"):
            a_bitrate = float(s["bit_rate"]) / 1000.0
    return {
        "duration": duration,
        "video_bitrate_kbps": v_bitrate,
        "audio_bitrate_kbps": a_bitrate,
        "video_codec": v_codec,
    }


def calc_bitrates(target_mb: float, duration_s: float, audio_kbps: int):
    if duration_s <= 0:
        return 0.0, 0.0
    total_kbps = (target_mb * 8192.0) / duration_s
    video_kbps = max(total_kbps - float(audio_kbps), 0.0)
    return total_kbps, video_kbps
