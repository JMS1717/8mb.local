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

    # Map requested codec to actual encoder and flags
    actual_encoder, v_flags, init_hw_flags = map_codec_to_hw(video_codec, hw_info)
    
    # Validate encoder is available and can be initialized
    def is_encoder_available(encoder_name: str) -> bool:
        """Check if encoder is available in ffmpeg."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=2
            )
            return encoder_name in result.stdout
        except Exception:
            return False
    
    def test_encoder_init(encoder_name: str, hw_flags: list) -> bool:
        """Test if encoder can actually be initialized (not just listed)."""
        try:
            # Try to initialize encoder with a minimal command
            # This will fail fast if the hardware isn't accessible
            cmd = ["ffmpeg", "-hide_banner"]
            cmd.extend(hw_flags)  # Hardware init flags
            cmd.extend([
                "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
                "-c:v", encoder_name,
                "-f", "null", "-"
            ])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10  # Increased timeout for slower systems
            )
            # Check for specific errors that indicate driver/library issues
            stderr_lower = result.stderr.lower()
            
            # Log the test result for debugging
            if result.returncode != 0:
                _publish(self.request.id, {"type": "log", "message": f"Encoder test for {encoder_name}: exit code {result.returncode}"})
            
            # These are definite failures - hardware not accessible
            if "operation not permitted" in stderr_lower:
                _publish(self.request.id, {"type": "log", "message": f"Encoder test failed: Operation not permitted"})
                return False
            if "could not open encoder" in stderr_lower:
                _publish(self.request.id, {"type": "log", "message": f"Encoder test failed: Could not open encoder"})
                return False
            if "no device found" in stderr_lower:
                _publish(self.request.id, {"type": "log", "message": f"Encoder test failed: No device found"})
                return False
            if "failed to set value" in stderr_lower and "init_hw_device" in stderr_lower:
                _publish(self.request.id, {"type": "log", "message": f"Encoder test failed: Hardware device initialization failed"})
                return False
            if "cannot load" in stderr_lower and ".so" in stderr_lower:
                _publish(self.request.id, {"type": "log", "message": f"Encoder test failed: Missing library ({result.stderr.split('Cannot load')[1].split()[0] if 'Cannot load' in result.stderr else 'unknown'})"})
                return False
            
            # Success: either return code 0, or non-zero but without hardware access errors
            # (some formats might cause non-zero but encoder itself initialized fine)
            return True
        except subprocess.TimeoutExpired:
            _publish(self.request.id, {"type": "log", "message": f"Encoder test timeout (> 10s) - assuming encoder works"})
            return True  # Timeout suggests it's working but slow, let it try
        except Exception as e:
            _publish(self.request.id, {"type": "log", "message": f"Encoder test exception: {str(e)}"})
            return False
    
    # Fallback to CPU if hardware encoder not available or can't initialize
    # Skip initialization test for NVENC if NVIDIA toolkit is installed (check for nvidia-smi)
    skip_test = False
    if actual_encoder.endswith("_nvenc"):
        try:
            nvidia_check = subprocess.run(["nvidia-smi"], capture_output=True, timeout=2)
            if nvidia_check.returncode == 0:
                skip_test = True
                _publish(self.request.id, {"type": "log", "message": "NVIDIA GPU detected via nvidia-smi, skipping encoder test"})
        except Exception:
            pass
    
    if actual_encoder not in ("libx264", "libx265", "libaom-av1"):
        if not is_encoder_available(actual_encoder):
            _publish(self.request.id, {"type": "log", "message": f"Warning: {actual_encoder} not available, falling back to CPU"})
            
            # Determine CPU fallback based on codec type
            if "h264" in actual_encoder:
                actual_encoder = "libx264"
                v_flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
            elif "hevc" in actual_encoder or "h265" in actual_encoder:
                actual_encoder = "libx265"
                v_flags = ["-pix_fmt", "yuv420p"]
            else:  # AV1
                actual_encoder = "libaom-av1"
                v_flags = ["-pix_fmt", "yuv420p"]
            init_hw_flags = []
        elif not skip_test and not test_encoder_init(actual_encoder, init_hw_flags):
            _publish(self.request.id, {"type": "log", "message": f"Warning: {actual_encoder} failed initialization test (driver/library issue), falling back to CPU"})
            
            # Determine CPU fallback based on codec type
            if "h264" in actual_encoder:
                actual_encoder = "libx264"
                v_flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
            elif "hevc" in actual_encoder or "h265" in actual_encoder:
                actual_encoder = "libx265"
                v_flags = ["-pix_fmt", "yuv420p"]
            else:  # AV1
                actual_encoder = "libaom-av1"
                v_flags = []
            
            init_hw_flags = []  # Clear hardware init flags
    
    _publish(self.request.id, {"type": "log", "message": f"Using encoder: {actual_encoder} (requested: {video_codec})"})

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

    # Build input options for trimming and decoder preferences
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

    # Prefer robust AV1 decoder when encoding on CPU and input is AV1
    if info.get("video_codec") == "av1" and actual_encoder in ("libx264", "libx265", "libaom-av1"):
        input_opts += ["-c:v", "libdav1d"]
        _publish(self.request.id, {"type": "log", "message": "Decoder: using libdav1d for AV1 input"})

    # Construct command
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        *init_hw_flags,  # Hardware initialization (QSV/VAAPI device setup)
        *input_opts,  # -ss before input for fast seeking
        "-i", input_path,
        *duration_opts,  # -t or -to for duration/end
        "-c:v", actual_encoder,  # Use detected encoder
        *v_flags,
    ]
    
    # Add video filter if needed
    # Special handling for VAAPI: filter already in v_flags
    if vf_filters and not actual_encoder.endswith("_vaapi"):
        cmd += ["-vf", ",".join(vf_filters)]
    elif vf_filters and actual_encoder.endswith("_vaapi"):
        # For VAAPI, we need to inject scale before format=nv12|vaapi,hwupload
        # Parse existing -vf from v_flags
        scale_filter = ",".join(vf_filters)
        # Replace the -vf in v_flags if present
        for i, flag in enumerate(v_flags):
            if flag == "-vf":
                v_flags[i+1] = f"{scale_filter},{v_flags[i+1]}"
                break
        cmd += v_flags[:]
        v_flags = []  # Already added
    
    if v_flags:  # Add remaining v_flags if not already added
        cmd += v_flags
    
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

    # Log the full ffmpeg command for debugging
    cmd_str = ' '.join(cmd)
    _publish(self.request.id, {"type": "log", "message": f"FFmpeg command: {cmd_str}"})

    # Start process
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1)

    last_progress = 0.0
    stderr_lines = []  # Capture all stderr for error reporting
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.strip()
            if not line:
                continue
            stderr_lines.append(line)  # Store for error diagnostics
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
            # Include last 20 lines of stderr in error message for diagnostics
            recent_stderr = '\n'.join(stderr_lines[-20:]) if stderr_lines else 'No stderr output'
            msg = f"ffmpeg failed with code {rc}\nLast stderr output:\n{recent_stderr}"
            # Publish error for UI and raise; let Celery handle failure state and exception payload formatting
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)
    except Exception as e:
        msg = str(e)
        # Do not manually set FAILURE; raising propagates proper exception metadata to Celery backend
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
