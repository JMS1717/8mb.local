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
from .hw_detect import get_hw_info, map_codec_to_hw

REDIS = None

def _redis() -> Redis:
    global REDIS
    if REDIS is None:
        REDIS = Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    return REDIS


def _publish(task_id: str, event: Dict):
    event.setdefault("task_id", task_id)
    _redis().publish(f"progress:{task_id}", json.dumps(event))


@celery_app.task(name="worker.worker.get_hardware_info")
def get_hardware_info_task():
    """Return hardware acceleration info for the frontend."""
    return get_hw_info()


@celery_app.task(name="worker.worker.compress_video", bind=True)
def compress_video(self, job_id: str, input_path: str, output_path: str, target_size_mb: float,
                   video_codec: str, audio_codec: str, audio_bitrate_kbps: int, preset: str, tune: str = "hq",
                   max_width: int = None, max_height: int = None, start_time: str = None, end_time: str = None):
    # Detect hardware acceleration
    hw_info = get_hw_info()
    _publish(self.request.id, {"type": "log", "message": f"Hardware: {hw_info['type'].upper()} acceleration detected"})
    
    # Probe
    info = ffprobe_info(input_path)
    duration = info.get("duration", 0.0)
    total_kbps, video_kbps = calc_bitrates(target_size_mb, duration, audio_bitrate_kbps)

    # Bitrate controls
    maxrate = int(video_kbps * 1.2)
    bufsize = int(video_kbps * 2)

    # Map preset and tune
    preset_val = preset.lower()
    tune_val = (tune or "hq").lower()

    # Container/audio compatibility: mp4 doesn't support libopus well, fall back to aac
    chosen_audio_codec = audio_codec
    if output_path.lower().endswith('.mp4') and audio_codec == 'libopus':
        chosen_audio_codec = 'aac'
        _publish(self.request.id, {"type": "log", "message": "mp4 container selected; switching audio codec from libopus to aac"})

    # Audio bitrate string
    a_bitrate_str = f"{int(audio_bitrate_kbps)}k"

    # Map requested codec to available hardware encoder
    actual_encoder, v_flags = map_codec_to_hw(video_codec, hw_info)
    if actual_encoder != video_codec:
        _publish(self.request.id, {"type": "log", "message": f"Using encoder: {actual_encoder} (requested: {video_codec})"})
    
    # Add preset/tune for compatible encoders
    preset_flags = []
    tune_flags = []
    
    if actual_encoder.endswith("_nvenc"):
        # NVIDIA NVENC
        preset_flags = ["-preset", preset_val]
        tune_flags = ["-tune", tune_val]
    elif actual_encoder.endswith("_qsv"):
        # Intel QSV - map presets
        qsv_preset_map = {"p1": "veryfast", "p2": "faster", "p3": "fast", "p4": "medium", "p5": "slow", "p6": "slower", "p7": "veryslow"}
        preset_flags = ["-preset", qsv_preset_map.get(preset_val, "medium")]
    elif actual_encoder.endswith("_amf"):
        # AMD AMF
        amf_preset_map = {"p1": "speed", "p2": "speed", "p3": "balanced", "p4": "balanced", "p5": "quality", "p6": "quality", "p7": "quality"}
        preset_flags = ["-quality", amf_preset_map.get(preset_val, "balanced")]
    elif actual_encoder.endswith("_vaapi"):
        # VAAPI - limited preset support
        preset_flags = ["-compression_level", "7"]  # 0-7 scale
    elif actual_encoder in ("libx264", "libx265", "libsvtav1"):
        # Software encoders
        cpu_preset_map = {"p1": "ultrafast", "p2": "superfast", "p3": "veryfast", "p4": "faster", "p5": "fast", "p6": "medium", "p7": "slow"}
        preset_flags = ["-preset", cpu_preset_map.get(preset_val, "medium")]
        if actual_encoder == "libx264":
            tune_flags = ["-tune", "film"]  # Better than 'hq' for CPU

    # MP4 web-friendly
    mp4_flags = ["-movflags", "+faststart"] if output_path.lower().endswith(".mp4") else []

    # Build video filter chain
    vf_filters = []
    
    # Resolution scaling
    if max_width or max_height:
        # Build scale expression to maintain aspect ratio
        if max_width and max_height:
            scale_expr = f"'min(iw,{max_width})':'min(ih,{max_height})':force_original_aspect_ratio=decrease"
        elif max_width:
            scale_expr = f"'min(iw,{max_width})':-2"
        else:  # max_height only
            scale_expr = f"-2:'min(ih,{max_height})'"
        vf_filters.append(f"scale={scale_expr}")
        _publish(self.request.id, {"type": "log", "message": f"Resolution: scaling to max {max_width or 'any'}x{max_height or 'any'}"})

    # Build input options for trimming
    input_opts = []
    duration_opts = []
    
    if start_time:
        # -ss before input for fast seeking
        input_opts += ["-ss", str(start_time)]
        _publish(self.request.id, {"type": "log", "message": f"Trimming: start at {start_time}"})
    
    if end_time:
        # Convert end_time to duration if we have start_time
        if start_time:
            # Calculate duration (end - start)
            # Parse times to seconds for calculation
            def parse_time(t):
                if isinstance(t, (int, float)):
                    return float(t)
                if ':' in str(t):
                    parts = str(t).split(':')
                    if len(parts) == 3:  # HH:MM:SS
                        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                    elif len(parts) == 2:  # MM:SS
                        return int(parts[0]) * 60 + float(parts[1])
                return float(t)
            
            try:
                start_sec = parse_time(start_time)
                end_sec = parse_time(end_time)
                duration_sec = end_sec - start_sec
                if duration_sec > 0:
                    duration_opts = ["-t", str(duration_sec)]
                    _publish(self.request.id, {"type": "log", "message": f"Trimming: duration {duration_sec:.2f}s (end at {end_time})"})
            except Exception as e:
                _publish(self.request.id, {"type": "log", "message": f"Warning: Could not parse trim times: {e}"})
        else:
            # No start time, use -to
            duration_opts = ["-to", str(end_time)]
            _publish(self.request.id, {"type": "log", "message": f"Trimming: end at {end_time}"})

    # Construct command
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        *input_opts,  # -ss before input for fast seeking
        "-i", input_path,
        *duration_opts,  # -t or -to for duration/end
        "-c:v", actual_encoder,  # Use detected encoder
        *v_flags,
    ]
    
    # Add video filter if needed
    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]
    
    cmd += [
        "-b:v", f"{int(video_kbps)}k",
        "-maxrate", f"{maxrate}k",
        "-bufsize", f"{bufsize}k",
        *preset_flags,  # Encoder-specific preset
        *tune_flags,    # Encoder-specific tune (if supported)
        "-c:a", chosen_audio_codec,
        "-b:a", a_bitrate_str,
        *mp4_flags,
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
