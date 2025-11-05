import json
import math
import os
import shlex
import subprocess
import time
import logging
import sys
from pathlib import Path
from typing import Dict
from redis import Redis

from .celery_app import celery_app
from .utils import ffprobe_info, calc_bitrates
from .hw_detect import get_hw_info, map_codec_to_hw
from .startup_tests import run_startup_tests
from threading import Thread

# Configure logging BEFORE any tests run
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True  # Override any existing config
)
logger = logging.getLogger(__name__)

REDIS = None
# Cache encoder test results to avoid slow init tests on every job
ENCODER_TEST_CACHE: Dict[str, bool] = {}

def _start_encoder_tests_async():
    def _run():
        try:
            logger.info("")
            logger.info("*" * 70)
            logger.info("  8MB.LOCAL WORKER INITIALIZATION")
            logger.info("*" * 70)
            logger.info("")
            sys.stdout.flush()
            _hw_info = get_hw_info()
            cache = run_startup_tests(_hw_info)
            ENCODER_TEST_CACHE.update(cache)
            logger.info(f"✓ Encoder cache ready: {len(ENCODER_TEST_CACHE)} encoder(s) validated")
            logger.info(f"✓ Worker initialization complete")
            logger.info("*" * 70)
            logger.info("")
            sys.stdout.flush()
        except Exception as e:
            logger.warning(f"Startup encoder tests failed (non-fatal): {e}")
            sys.stdout.flush()

    # Allow disabling tests entirely via env
    if os.getenv('DISABLE_STARTUP_TESTS', '').lower() in ('1','true','yes'):
        logger.info("Skipping encoder startup tests (DISABLE_STARTUP_TESTS=1)")
        return
    try:
        Thread(target=_run, daemon=True).start()
    except Exception as e:
        logger.warning(f"Failed to start background encoder tests: {e}")


_start_encoder_tests_async()

def _redis() -> Redis:
    global REDIS
    if REDIS is None:
        REDIS = Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    return REDIS


def _publish(task_id: str, event: Dict):
    event.setdefault("task_id", task_id)
    _redis().publish(f"progress:{task_id}", json.dumps(event))


def _is_cancelled(task_id: str) -> bool:
    try:
        val = _redis().get(f"cancel:{task_id}")
        return str(val) == '1'
    except Exception:
        return False


@celery_app.task(name="worker.worker.get_hardware_info")
def get_hardware_info_task():
    """Return hardware acceleration info for the frontend."""
    return get_hw_info()


@celery_app.task(name="worker.worker.compress_video", bind=True)
def compress_video(self, job_id: str, input_path: str, output_path: str, target_size_mb: float,
                   video_codec: str, audio_codec: str, audio_bitrate_kbps: int, preset: str, tune: str = "hq",
                   max_width: int = None, max_height: int = None, start_time: str = None, end_time: str = None,
                   force_hw_decode: bool = False, fast_mp4_finalize: bool = False):
    # Detect hardware acceleration
    _publish(self.request.id, {"type": "log", "message": "Initializing: detecting hardware…"})
    hw_info = get_hw_info()
    _publish(self.request.id, {"type": "log", "message": f"Hardware: {hw_info['type'].upper()} acceleration detected"})
    
    # Probe
    _publish(self.request.id, {"type": "log", "message": "Initializing: probing input file…"})
    info = ffprobe_info(input_path)
    duration = info.get("duration", 0.0)
    total_kbps, video_kbps = calc_bitrates(target_size_mb, duration, audio_bitrate_kbps)

    # Bitrate controls
    maxrate = int(video_kbps * 1.2)
    bufsize = int(video_kbps * 2)

    # Map requested codec to actual encoder and flags
    actual_encoder, v_flags, init_hw_flags = map_codec_to_hw(video_codec, hw_info)
    
    # Fallback to CPU only if startup tests explicitly marked encoder as unavailable.
    # If cache is empty (tests still running in background), attempt hardware and rely on runtime fallback below.
    if actual_encoder not in ("libx264", "libx265", "libaom-av1"):
        global ENCODER_TEST_CACHE
        cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
        if cache_key in ENCODER_TEST_CACHE and not ENCODER_TEST_CACHE[cache_key]:
            _publish(self.request.id, {"type": "log", "message": f"Warning: {actual_encoder} marked unavailable by startup tests, falling back to CPU"})
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
    
    _publish(self.request.id, {"type": "log", "message": f"Using encoder: {actual_encoder} (requested: {video_codec})"})
    _publish(self.request.id, {"type": "log", "message": "Starting compression…"})
    
    # Start timing from here (actual encoding, not initialization)
    start_ts = time.time()
    
    # Log decode path info
    try:
        if any(x == "-hwaccel" for x in init_hw_flags):
            idx = init_hw_flags.index("-hwaccel")
            dec = init_hw_flags[idx+1] if idx+1 < len(init_hw_flags) else "unknown"
            _publish(self.request.id, {"type": "log", "message": f"Decoder: using {dec}"})
    except Exception:
        pass

    # Map preset and tune
    preset_val = preset.lower()
    tune_val = (tune or "hq").lower()

    # Container/audio compatibility: mp4 doesn't support libopus well, fall back to aac
    # Handle mute option
    chosen_audio_codec = audio_codec
    if audio_codec == 'none':
        chosen_audio_codec = None
        _publish(self.request.id, {"type": "log", "message": "Audio removed (mute option enabled)"})
    elif output_path.lower().endswith('.mp4') and audio_codec == 'libopus':
        chosen_audio_codec = 'aac'
        _publish(self.request.id, {"type": "log", "message": "mp4 container selected; switching audio codec from libopus to aac"})

    # Audio bitrate string
    a_bitrate_str = f"{int(audio_bitrate_kbps)}k"

    # Add preset/tune for compatible encoders
    preset_flags = []
    tune_flags = []
    
    # Handle "extraquality" preset (slowest, best quality)
    if preset_val == "extraquality":
        _publish(self.request.id, {"type": "log", "message": "Extra Quality mode enabled (slowest encoding, best quality)"})
        if actual_encoder.endswith("_nvenc"):
            preset_flags = ["-preset", "p7"]
            tune_flags = ["-tune", "hq"]
            # Add extra quality flags for NVENC
            preset_flags += ["-rc:v", "vbr", "-cq:v", "19", "-b:v", "0"]  # Variable bitrate with quality target
        elif actual_encoder.endswith("_qsv"):
            preset_flags = ["-preset", "veryslow"]
        elif actual_encoder.endswith("_vaapi"):
            preset_flags = ["-compression_level", "7", "-quality", "1"]
        elif actual_encoder in ("libx264", "libx265"):
            preset_flags = ["-preset", "veryslow"]
            if actual_encoder == "libx264":
                tune_flags = ["-tune", "film"]
                preset_flags += ["-crf", "18"]  # Very high quality
            else:  # libx265
                preset_flags += ["-crf", "20"]  # Very high quality for HEVC
        elif actual_encoder == "libaom-av1":
            preset_flags = ["-cpu-used", "0"]  # Slowest, best quality
            preset_flags += ["-crf", "20"]
    elif actual_encoder.endswith("_nvenc"):
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

    # MP4 finalize behavior
    if output_path.lower().endswith(".mp4"):
        if fast_mp4_finalize:
            # Fragmented MP4 avoids long finalization step
            mp4_flags = ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
            _publish(self.request.id, {"type": "log", "message": "MP4: using fragmented MP4 (fast finalize)"})
        else:
            mp4_flags = ["-movflags", "+faststart"]
    else:
        mp4_flags = []

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

    # Decide decoder strategy based on input codec and runtime capability
    in_codec = info.get("video_codec")

    def has_decoder(dec_name: str) -> bool:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-decoders"], capture_output=True, text=True, timeout=5)
            return (r.returncode == 0) and (dec_name in (r.stdout or ""))
        except Exception:
            return False

    def can_cuda_decode(path: str) -> bool:
        try:
            test_cmd = [
                "ffmpeg", "-hide_banner", "-v", "error",
                "-hwaccel", "cuda",
                "-ss", "0",
                "-t", "0.1",
                "-i", path,
                "-f", "null", "-"
            ]
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)
            stderr = (r.stderr or "").lower()
            if "doesn't support hardware accelerated" in stderr or "failed setup for format cuda" in stderr:
                return False
            # Return code isn't always indicative; absence of the above errors is a good proxy
            return r.returncode == 0 or "error" not in stderr
        except Exception:
            return False

    def can_av1_cuvid_decode(path: str) -> bool:
        if not has_decoder("av1_cuvid"):
            return False
        try:
            test_cmd = [
                "ffmpeg", "-hide_banner", "-v", "error",
                "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
                "-c:v", "av1_cuvid",
                "-ss", "0",
                "-t", "0.1",
                "-i", path,
                "-f", "null", "-"
            ]
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)
            stderr = (r.stderr or "").lower()
            if any(s in stderr for s in ["not found", "unknown decoder", "cannot load", "init failed", "device not present"]):
                return False
            return r.returncode == 0 or "error" not in stderr
        except Exception:
            return False

    # AV1: Prefer HW decode only if actually supported; otherwise libdav1d
    if in_codec == "av1":
        if actual_encoder.endswith("_nvenc"):
            used_cuda = False
            # Be more aggressive when force_hw_decode is enabled
            if force_hw_decode and can_av1_cuvid_decode(input_path):
                init_hw_flags = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] + init_hw_flags
                input_opts += ["-c:v", "av1_cuvid"]
                _publish(self.request.id, {"type": "log", "message": "Decoder: using av1_cuvid (CUDA)"})
                used_cuda = True
            elif can_cuda_decode(input_path):
                init_hw_flags = ["-hwaccel", "cuda"] + init_hw_flags
                _publish(self.request.id, {"type": "log", "message": "Decoder: using cuda (AV1)"})
                used_cuda = True

            if not used_cuda:
                input_opts += ["-c:v", "libdav1d"]
                _publish(self.request.id, {"type": "log", "message": "Decoder: using libdav1d for AV1 input"})
        else:
            # Non-NVIDIA encoders: keep software AV1 decode for now
            input_opts += ["-c:v", "libdav1d"]
            _publish(self.request.id, {"type": "log", "message": "Decoder: using libdav1d for AV1 input"})
    elif in_codec in ("h264", "hevc") and actual_encoder.endswith("_nvenc"):
        # H.264/HEVC: NVDEC widely supported
        init_hw_flags = ["-hwaccel", "cuda"] + init_hw_flags
        _publish(self.request.id, {"type": "log", "message": f"Decoder: using cuda ({in_codec})"})

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
    # Note: v_flags were already added earlier; avoid duplicating them for non-VAAPI paths
    
    cmd += [
        "-b:v", f"{int(video_kbps)}k",
        "-maxrate", f"{maxrate}k",
        "-bufsize", f"{bufsize}k",
        *preset_flags,  # Encoder-specific preset
        *tune_flags,    # Encoder-specific tune (if supported)
    ]
    
    # Add audio encoding or disable audio if muted
    if chosen_audio_codec is None:
        cmd += ["-an"]  # No audio
    else:
        cmd += ["-c:a", chosen_audio_codec, "-b:a", a_bitrate_str]
    
    cmd += [
        *mp4_flags,
        "-progress", "pipe:2",
        output_path,
    ]

    # Log the full ffmpeg command for debugging
    cmd_str = ' '.join(cmd)
    _publish(self.request.id, {"type": "log", "message": f"FFmpeg command: {cmd_str}"})

    def run_ffmpeg_and_stream(command: list) -> tuple[int, bool]:
        proc_i = subprocess.Popen(command, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1)
        local_stderr = []
        nonlocal last_progress
        emitted_initial_progress = False
        cancelled = False
        try:
            assert proc_i.stderr is not None
            for line in proc_i.stderr:
                # Check for cancellation between lines
                if _is_cancelled(self.request.id):
                    cancelled = True
                    _publish(self.request.id, {"type": "log", "message": "Cancel received, stopping encoder..."})
                    try:
                        proc_i.terminate()
                    except Exception:
                        pass
                    try:
                        proc_i.wait(timeout=3)
                    except Exception:
                        try:
                            proc_i.kill()
                        except Exception:
                            pass
                    break
                line = line.strip()
                if not line:
                    continue
                local_stderr.append(line)
                # Emit a small initial progress bump on first stderr line to avoid long "Starting…"
                if not emitted_initial_progress and duration > 0:
                    emitted_initial_progress = True
                    if last_progress < 0.01:
                        last_progress = 0.01
                        _publish(self.request.id, {"type": "progress", "progress": 1.0})
                if "=" in line:
                    key, _, val = line.partition("=")
                    if key == "out_time_ms":
                        try:
                            ms = int(val)
                            if duration > 0:
                                # Use 0-99% for encoding (the actual work), reserve only 99-100% for finalization
                                p = min(max(ms / 1000.0 / duration, 0.0), 1.0)
                                scaled_progress = p * 0.99  # 0.0 to 0.99 (99%)
                                # Update every 0.5% or when reaching 99%
                                if (scaled_progress - last_progress) >= 0.005 or scaled_progress >= 0.989:
                                    last_progress = scaled_progress
                                    _publish(self.request.id, {"type": "progress", "progress": round(scaled_progress*100, 2), "phase": "encoding"})
                        except Exception:
                            pass
                    elif key in ("bitrate", "total_size", "speed"):
                        _publish(self.request.id, {"type": "log", "message": f"{key}={val}"})
                else:
                    _publish(self.request.id, {"type": "log", "message": line})
            if not cancelled:
                proc_i.wait()
            return (proc_i.returncode or 0, cancelled)
        finally:
            stderr_lines.extend(local_stderr)

    # Start process and optionally fall back to CPU on failure
    last_progress = 0.0
    stderr_lines: list[str] = []
    rc, was_cancelled = run_ffmpeg_and_stream(cmd)

    if was_cancelled:
        _publish(self.request.id, {"type": "canceled"})
        msg = "Job canceled by user"
        _publish(self.request.id, {"type": "error", "message": msg})
        raise RuntimeError(msg)

    if rc != 0 and (actual_encoder.endswith("_nvenc") or actual_encoder.endswith("_qsv") or actual_encoder.endswith("_vaapi") or actual_encoder.endswith("_amf")):
        _publish(self.request.id, {"type": "log", "message": f"Hardware encode failed (rc={rc}). Retrying on CPU..."})
        # Determine CPU fallback
        if "h264" in actual_encoder:
            fb_encoder = "libx264"; fb_flags = ["-pix_fmt","yuv420p","-profile:v","high"]
        elif "hevc" in actual_encoder or "h265" in actual_encoder:
            fb_encoder = "libx265"; fb_flags = ["-pix_fmt","yuv420p"]
        else:
            fb_encoder = "libaom-av1"; fb_flags = ["-pix_fmt","yuv420p"]

        # Rebuild command for CPU
        cmd2 = [
            "ffmpeg", "-hide_banner", "-y",
            *input_opts,
            "-i", input_path,
            *duration_opts,
            "-c:v", fb_encoder,
            *fb_flags,
        ]
        # Add video filters if any
        if vf_filters:
            cmd2 += ["-vf", ",".join(vf_filters)]
        cmd2 += [
            "-b:v", f"{int(video_kbps)}k",
            "-maxrate", f"{maxrate}k",
            "-bufsize", f"{bufsize}k",
        ]
        # Reasonable CPU presets
        if fb_encoder == "libx264":
            cmd2 += ["-preset","medium","-tune","film"]
        elif fb_encoder == "libx265":
            cmd2 += ["-preset","medium"]
        elif fb_encoder == "libaom-av1":
            cmd2 += ["-cpu-used","4"]
        # Audio
        if chosen_audio_codec is None:
            cmd2 += ["-an"]
        else:
            cmd2 += ["-c:a", chosen_audio_codec, "-b:a", a_bitrate_str]
        cmd2 += [*mp4_flags, "-progress", "pipe:2", output_path]

        rc, was_cancelled = run_ffmpeg_and_stream(cmd2)

    if was_cancelled:
        _publish(self.request.id, {"type": "canceled"})
        msg = "Job canceled by user"
        _publish(self.request.id, {"type": "error", "message": msg})
        raise RuntimeError(msg)

    if rc != 0:
        recent_stderr = '\n'.join(stderr_lines[-20:]) if stderr_lines else 'No stderr output'
        msg = f"ffmpeg failed with code {rc}\nLast stderr output:\n{recent_stderr}"
        _publish(self.request.id, {"type": "error", "message": msg})
        raise RuntimeError(msg)

    # Encoding complete - move to 99% and start finalization
    _publish(self.request.id, {"type": "progress", "progress": 99.0, "phase": "finalizing"})
    _publish(self.request.id, {"type": "log", "message": "Encoding complete. Finalizing output..."})

    # CRITICAL: Wait for file to be fully written and readable (especially on networked/slow filesystems)
    max_wait = 10  # seconds
    file_ready = False
    for attempt in range(max_wait * 5):  # Check every 200ms
        try:
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                # Try to open the file to ensure it's not locked
                with open(output_path, 'rb') as f:
                    f.read(1)
                file_ready = True
                break
        except (FileNotFoundError, IOError, OSError):
            pass
        time.sleep(0.2)
    
    if not file_ready:
        msg = f"Output file not accessible after encode completion: {output_path}"
        _publish(self.request.id, {"type": "error", "message": msg})
        raise RuntimeError(msg)

    # Success: compute final stats
    try:
        final_size = os.path.getsize(output_path)
    except Exception:
        final_size = 0
    
    _publish(self.request.id, {"type": "log", "message": f"Output verified: {final_size / (1024*1024):.2f} MB"})

    # Still 99% - checking file size and preparing for possible retry
    final_size_mb = round(final_size / (1024*1024), 2) if final_size else 0

    # Make the file downloadable immediately after encode finishes
    # Expose output_path early via task meta and send a 'ready' event for the UI
    try:
        # Persist readiness in both Celery meta and Redis for robust download lookup
        self.update_state(state="PROGRESS", meta={"output_path": output_path, "progress": 100.0, "detail": "ready"})
    except Exception:
        pass
    try:
        # Store a short-lived ready pointer for the API to fetch if Celery meta lags
        _redis().setex(f"ready:{self.request.id}", 24*3600, str(output_path))
    except Exception:
        pass
    try:
        from pathlib import Path as _Path
        _publish(self.request.id, {"type": "ready", "output_filename": _Path(output_path).name})
    except Exception:
        pass
    
    final_size_mb = round(final_size / (1024*1024), 2) if final_size else 0
    
    # Check if file is too large (>2% over target) and retry with lower bitrate
    size_overage_percent = ((final_size_mb - target_size_mb) / target_size_mb) * 100 if target_size_mb > 0 else 0
    
    if size_overage_percent > 2.0 and final_size_mb > target_size_mb:
        # File is too large! Notify user and retry
        _publish(self.request.id, {"type": "log", "message": f"⚠️ File is {size_overage_percent:.1f}% over target ({final_size_mb:.2f} MB vs {target_size_mb:.2f} MB)"})
        _publish(self.request.id, {"type": "log", "message": "🔄 Retrying with reduced bitrate to meet size constraint..."})
        _publish(self.request.id, {"type": "retry", "message": f"File too large ({final_size_mb:.2f} MB), retrying to fit {target_size_mb:.2f} MB target", "overage_percent": round(size_overage_percent, 1)})
        
        # Calculate adjusted bitrate (reduce by overage + 5% safety margin)
        reduction_factor = max(0.5, 1.0 - (size_overage_percent / 100.0) - 0.05)
        adjusted_video_kbps = int(video_kbps * reduction_factor)
        
        _publish(self.request.id, {"type": "log", "message": f"Adjusted video bitrate: {video_kbps} → {adjusted_video_kbps} kbps (reduction: {(1-reduction_factor)*100:.1f}%)"})
        
        # Delete the oversized file
        try:
            os.remove(output_path)
            _publish(self.request.id, {"type": "log", "message": "Removed oversized file"})
        except Exception as e:
            _publish(self.request.id, {"type": "log", "message": f"Warning: Could not remove oversized file: {e}"})
        
        # Reset progress for retry
        _publish(self.request.id, {"type": "progress", "progress": 1.0, "phase": "encoding"})
        
        # Re-run the encoding with adjusted bitrate by modifying cmd
        # Find and replace the bitrate values in the original command
        retry_cmd = []
        i = 0
        while i < len(cmd):
            if cmd[i] == "-b:v":
                retry_cmd.append(cmd[i])
                retry_cmd.append(f"{adjusted_video_kbps}k")
                i += 2
            elif cmd[i] == "-maxrate":
                retry_cmd.append(cmd[i])
                retry_cmd.append(f"{int(adjusted_video_kbps * 1.2)}k")
                i += 2
            elif cmd[i] == "-bufsize":
                retry_cmd.append(cmd[i])
                retry_cmd.append(f"{int(adjusted_video_kbps * 2)}k")
                i += 2
            else:
                retry_cmd.append(cmd[i])
                i += 1
        
        _publish(self.request.id, {"type": "log", "message": f"Retry FFmpeg command: {' '.join(retry_cmd[:10])}..."})
        
        # Run the retry encode
        last_progress = 0.0
        stderr_lines = []
        rc, was_cancelled = run_ffmpeg_and_stream(retry_cmd)
        
        if was_cancelled:
            _publish(self.request.id, {"type": "canceled"})
            msg = "Job canceled during retry"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)
        
        if rc != 0:
            _publish(self.request.id, {"type": "error", "message": f"Retry encode failed with return code {rc}. Keeping original result."})
            # Don't fail completely, just note the retry failed
        else:
            # Update final size after successful retry
            try:
                final_size = os.path.getsize(output_path)
                final_size_mb = round(final_size / (1024*1024), 2)
                new_overage = ((final_size_mb - target_size_mb) / target_size_mb) * 100 if target_size_mb > 0 else 0
                if new_overage <= 0:
                    _publish(self.request.id, {"type": "log", "message": f"✅ Retry successful! Final size: {final_size_mb:.2f} MB (under target)"})
                else:
                    _publish(self.request.id, {"type": "log", "message": f"✅ Retry complete! Final size: {final_size_mb:.2f} MB ({new_overage:+.1f}% vs target)"})
            except Exception:
                final_size = 0
                final_size_mb = 0
    
    stats = {
        "input_path": input_path,
        "output_path": output_path,
        "duration_s": duration,
        "target_size_mb": target_size_mb,
        "final_size_mb": final_size_mb,
    }
    
    # Add to history if enabled
    try:
        # Default ON if variable not set
        history_enabled = os.getenv('HISTORY_ENABLED', 'true').lower() in ('true', '1', 'yes')
        if history_enabled:
            # Import here to avoid circular dependency
            import sys
            sys.path.insert(0, '/app')
            import importlib
            hm = importlib.import_module('backend.history_manager')
            
            # Get original file size
            original_size = os.path.getsize(input_path)
            original_size_mb = original_size / (1024*1024)
            
            # Extract filename from path
            filename = Path(input_path).name
            
            # Get compression duration (time taken)
            compression_duration = max(time.time() - start_ts, 0)
            
            # Derive container from output path
            container = 'mp4' if str(output_path).lower().endswith('.mp4') else 'mkv'
            
            hm.add_history_entry(
                filename=filename,
                original_size_mb=original_size_mb,
                compressed_size_mb=final_size_mb,
                video_codec=actual_encoder,
                audio_codec=chosen_audio_codec or 'none',
                target_mb=target_size_mb,
                preset=preset_val,
                duration=compression_duration,
                task_id=self.request.id,
                container=container,
                tune=tune_val,
                audio_bitrate_kbps=int(audio_bitrate_kbps),
                max_width=max_width,
                max_height=max_height,
                start_time=start_time,
                end_time=end_time,
                encoder=actual_encoder,
            )
    except Exception as e:
        # Don't fail the job if history fails
        _publish(self.request.id, {"type": "log", "message": f"Failed to save history: {str(e)}"})
    
    # 100% - Complete!
    _publish(self.request.id, {"type": "progress", "progress": 100.0, "phase": "done"})
    
    self.update_state(state="SUCCESS", meta={"output_path": output_path, "progress": 100.0, "detail": "done", **stats})
    _publish(self.request.id, {"type": "done", "stats": stats})
    return stats
