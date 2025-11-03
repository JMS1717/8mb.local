import json
import math
import os
import shlex
import subprocess
import time
from typing import Dict
from redis import Redis

from .celery_app import celery_app
from .utils import ffprobe_info, calc_bitrates

REDIS = None

def _redis() -> Redis:
    global REDIS
    if REDIS is None:
        REDIS = Redis.from_url(os.getenv("REDIS_URL", "redis://redis-broker:6379/0"), decode_responses=True)
    return REDIS


def _publish(task_id: str, event: Dict):
    event.setdefault("task_id", task_id)
    _redis().publish(f"progress:{task_id}", json.dumps(event))


@celery_app.task(name="app.worker.compress_video", bind=True)
def compress_video(self, job_id: str, input_path: str, output_path: str, target_size_mb: float,
                   video_codec: str, audio_codec: str, audio_bitrate_kbps: int, preset: str):
    # Probe
    info = ffprobe_info(input_path)
    duration = info.get("duration", 0.0)
    total_kbps, video_kbps = calc_bitrates(target_size_mb, duration, audio_bitrate_kbps)

    # Bitrate controls
    maxrate = int(video_kbps * 1.2)
    bufsize = int(video_kbps * 2)

    # Map preset like 'p6' to ffmpeg '-preset p6'
    preset_val = preset.lower()

    # Container/audio compatibility: mp4 doesn't support libopus well, fall back to aac
    chosen_audio_codec = audio_codec
    if output_path.lower().endswith('.mp4') and audio_codec == 'libopus':
        chosen_audio_codec = 'aac'
        _publish(self.request.id, {"type": "log", "message": "mp4 container selected; switching audio codec from libopus to aac"})

    # Audio bitrate string
    a_bitrate_str = f"{int(audio_bitrate_kbps)}k"

    # Construct command
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", input_path,
        "-c:v", video_codec,
        "-b:v", f"{int(video_kbps)}k",
        "-maxrate", f"{maxrate}k",
        "-bufsize", f"{bufsize}k",
        "-preset", preset_val,
        "-tune", "hq",
        "-c:a", chosen_audio_codec,
        "-b:a", a_bitrate_str,
        "-progress", "pipe:2",
        output_path,
    ]

    # Start process
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1)

    last_progress = 0.0
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.strip()
            if not line:
                continue
            # Forward raw log lines for UI when not progress format
            if "=" in line:
                key, _, val = line.partition("=")
                if key == "out_time_ms":
                    try:
                        ms = int(val)
                        if duration > 0:
                            p = min(max(ms / 1000.0 / duration, 0.0), 1.0)
                            if (p - last_progress) >= 0.01 or p >= 0.999:
                                last_progress = p
                                _publish(self.request.id, {"type": "progress", "progress": round(p*100, 2)})
                    except Exception:
                        pass
                elif key in ("bitrate", "total_size", "speed"):
                    _publish(self.request.id, {"type": "log", "message": f"{key}={val}"})
                else:
                    # Progress format has many keys; skip flooding
                    pass
            else:
                _publish(self.request.id, {"type": "log", "message": line})
        proc.wait()
        rc = proc.returncode
        if rc != 0:
            msg = f"ffmpeg failed with code {rc}"
            self.update_state(state="FAILURE", meta={"detail": msg})
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)
    except Exception as e:
        msg = str(e)
        self.update_state(state="FAILURE", meta={"detail": msg})
        _publish(self.request.id, {"type": "error", "message": msg})
        raise

    # Success: compute final stats
    try:
        final_size = os.path.getsize(output_path)
    except Exception:
        final_size = 0
    stats = {
        "input_path": input_path,
        "output_path": output_path,
        "duration_s": duration,
        "target_size_mb": target_size_mb,
        "final_size_mb": round(final_size / (1024*1024), 2) if final_size else 0,
    }
    self.update_state(state="SUCCESS", meta={"output_path": output_path, "progress": 100.0, "detail": "done", **stats})
    _publish(self.request.id, {"type": "done", "stats": stats})
    return stats
