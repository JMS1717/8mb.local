from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import time
import logging
import sys
from pathlib import Path
from typing import Dict, Optional
from redis import Redis

from .celery_app import celery_app
from .utils import ffprobe_info, calc_bitrates
from .auto_resolution import choose_auto_resolution
from .hw_detect import get_hw_info, map_codec_to_hw, choose_best_codec
from .startup_tests import run_startup_tests
from .progress import parse_time_string

logger = logging.getLogger(__name__)

REDIS = None
# Cache encoder test results to avoid slow init tests on every job
ENCODER_TEST_CACHE: Dict[str, bool] = {}

# libsvtav1: SVT-AV1 preset helpers
SVTAV1_PARAMS_MAX_LP = ["-svtav1-params", "lp=6"]


def _cpu_fallback_for(encoder: str) -> tuple[str, list[str]]:
    """Return (cpu_encoder, v_flags) for CPU fallback."""
    from .constants import CPU_FALLBACK, LIBSVTAV1, LIBX264, LIBX265
    fb = CPU_FALLBACK.get(encoder)
    if fb is None:
        if "h264" in encoder: fb = LIBX264
        elif "hevc" in encoder or "h265" in encoder: fb = LIBX265
        elif "av1" in encoder: fb = LIBSVTAV1
        else: fb = LIBX264
    flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"] if fb == LIBX264 else ["-pix_fmt", "yuv420p"]
    return fb, flags


def _force_stop_ffmpeg(proc) -> None:
    """Hard-stop ffmpeg (libsvtav1 ignores SIGTERM)."""
    if proc.poll() is not None: return
    try: proc.kill()
    except Exception: pass
    import sys as _sys
    if _sys.platform != "win32" and proc.pid:
        import signal as _sig
        try: os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
        except (ProcessLookupError, OSError): pass




def get_gpu_env():
    """
    Get environment with NVIDIA GPU variables and library paths for subprocess calls.
    Includes LD_LIBRARY_PATH locations needed for CUDA on WSL2 and NVIDIA toolkit.
    """
    env = os.environ.copy()
    # Ensure NVIDIA variables are set for GPU access
    env['NVIDIA_VISIBLE_DEVICES'] = env.get('NVIDIA_VISIBLE_DEVICES', 'all')
    env['NVIDIA_DRIVER_CAPABILITIES'] = env.get('NVIDIA_DRIVER_CAPABILITIES', 'compute,video,utility')
    # Add common library locations (non-destructive append)
    lib_paths = [
        '/usr/local/nvidia/lib64',
        '/usr/local/nvidia/lib',
        '/usr/local/cuda/lib64',
        '/usr/local/cuda/lib',
        '/usr/lib/wsl/lib',  # WSL2 libcuda.so location
        '/usr/lib/x86_64-linux-gnu',
    ]
    existing = env.get('LD_LIBRARY_PATH', '')
    add = ':'.join(p for p in lib_paths if p)
    env['LD_LIBRARY_PATH'] = (existing + (':' if existing and add else '') + add) if (existing or add) else ''
    return env

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
    hw = get_hw_info() or {}
    # Include preferred codec suggestion using startup test cache if available
    try:
        preferred = choose_best_codec(hw, encoder_test_cache=ENCODER_TEST_CACHE)
        hw = dict(hw)  # copy
        hw["preferred"] = preferred
    except Exception:
        # Fall back to raw hw info
        pass
    return hw


@celery_app.task(name="worker.worker.run_hardware_tests")
def run_hardware_tests_task() -> dict:
    """Trigger encoder/decoder startup tests on demand and refresh cache.

    Returns a small summary with the number of cache entries updated.
    """
    try:
        _hw_info = get_hw_info()
        cache = run_startup_tests(_hw_info)
        try:
            ENCODER_TEST_CACHE.update(cache)
        except Exception:
            pass
        return {"status": "ok", "updated": len(cache)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@celery_app.task(name="worker.worker.compress_video", bind=True)
def compress_video(self, job_id: str, input_path: str, output_path: str, target_size_mb: float,
                   video_codec: str, audio_codec: str, audio_bitrate_kbps: int, preset: str, tune: str = "hq",
                   max_width: int = None, max_height: int = None, start_time: str = None, end_time: str = None,
                   force_hw_decode: bool = False, fast_mp4_finalize: bool = False,
                   auto_resolution: bool = False, min_auto_resolution: int = 240,
                   target_resolution: int | None = None, audio_only: bool = False,
                   target_video_bitrate_kbps: float | None = None,
                   max_output_fps: float | None = None):
    # Detect hardware acceleration
    _publish(self.request.id, {"type": "log", "message": "Initializing: detecting hardware…"})
    hw_info = get_hw_info()
    _publish(self.request.id, {"type": "log", "message": f"Hardware: {hw_info['type'].upper()} acceleration detected"})
    
    # Probe
    _publish(self.request.id, {"type": "log", "message": "Initializing: probing input file…"})
    info = ffprobe_info(input_path)
    duration = info.get("duration", 0.0)
    bitrate_mode = target_video_bitrate_kbps is not None and float(target_video_bitrate_kbps) > 0
    if bitrate_mode:
        video_kbps = float(target_video_bitrate_kbps)
        total_kbps = video_kbps + float(audio_bitrate_kbps)
        _publish(self.request.id, {"type": "log", "message": f"Target video bitrate: {int(video_kbps)} kbps (fixed; not derived from file size)"})
    else:
        total_kbps, video_kbps = calc_bitrates(target_size_mb, duration, audio_bitrate_kbps)
    # Estimated size for progress / queue metadata when using fixed bitrate
    progress_target_mb = target_size_mb
    if bitrate_mode and duration > 0:
        progress_target_mb = max(0.01, (total_kbps * duration) / 8192.0)

    rot_deg = int(info.get("rotation_degrees") or 0)
    disp_w = info.get("display_width") or info.get("width")
    disp_h = info.get("display_height") or info.get("height")
    if rot_deg % 360 != 0:
        dar_note = ""
        if info.get("display_aspect_ratio"):
            dar_note = f" DAR {info.get('display_aspect_ratio')}."
        _publish(self.request.id, {"type": "log", "message": (
            f"Display rotation {rot_deg}° (coded {info.get('width')}×{info.get('height')}).{dar_note} "
            "Using software decode so FFmpeg can apply display orientation (GPU decode ignores this metadata)."
        )})

    # Bitrate controls
    maxrate = int(video_kbps * 1.2)
    bufsize = int(video_kbps * 2)

    # Map requested codec to actual encoder and flags
    actual_encoder, v_flags, init_hw_flags = map_codec_to_hw(video_codec, hw_info)
    
    # Fallback to CPU only if startup tests explicitly marked encoder as unavailable.
    # If cache is empty (tests still running in background), attempt hardware and rely on runtime fallback below.
    original_encoder = actual_encoder
    if actual_encoder not in ("libx264", "libx265", "libaom-av1"):
        global ENCODER_TEST_CACHE
        cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
        if cache_key in ENCODER_TEST_CACHE and not ENCODER_TEST_CACHE[cache_key]:
            _publish(self.request.id, {"type": "log", "message": f"⚠️ {actual_encoder} marked unavailable by startup tests, falling back to CPU"})
            _publish(self.request.id, {"type": "log", "message": (
                "Note: The selected hardware encoder failed initialization during startup tests. "
                "This means hardware acceleration for this codec is unavailable on this system; "
                "the job will use a CPU encoder instead which is typically much slower and increases CPU usage. "
                "To enable hardware encoding, ensure drivers/libraries are installed and run 'System → Run encoder tests' in the UI to refresh results."
            )})
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
            # Update hardware info display to show CPU fallback
            _publish(self.request.id, {"type": "log", "message": f"Encoder: CPU ({actual_encoder})"})
    
    _publish(self.request.id, {"type": "log", "message": f"Using encoder: {actual_encoder} (requested: {video_codec})"})
    _publish(self.request.id, {"type": "log", "message": "Starting compression…"})
    # Mark task as started so queue shows running immediately
    try:
        self.update_state(state="STARTED", meta={"progress": 0.0, "phase": "encoding"})
    except Exception:
        pass
    
    # Start timing from here (actual encoding, not initialization)
    start_ts = time.time()
    # Dynamic progress model parameters
    # Reserve more time for finalization when not using fragmented MP4
    is_mp4 = str(output_path).lower().endswith('.mp4')
    if is_mp4 and fast_mp4_finalize:
        encoding_portion = 0.985  # almost all progress goes to encoding
    elif is_mp4 and not fast_mp4_finalize:
        encoding_portion = 0.90   # leave more for moov/faststart move
    else:
        encoding_portion = 0.96   # mkv and others
    finalize_portion = max(0.0, 1.0 - encoding_portion)
    # Track measured speed from ffmpeg (EWMA of "speed=..x")
    speed_ewma: Optional[float] = None
    ewma_alpha = 0.3
    
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
    if bitrate_mode and preset_val == "extraquality":
        _publish(self.request.id, {"type": "log", "message": "Extra Quality uses constant-quality mode, not fixed bitrate — using P6 for this encode."})
        preset_val = "p6"

    # Audio-only path: ignore video entirely and produce .m4a (aac) or .opus per requested audio codec
    if audio_only:
        _publish(self.request.id, {"type": "log", "message": "Audio-only mode enabled — extracting audio"})
        # Validate presence of an audio stream before invoking ffmpeg
        if not info.get("has_audio"):
            msg = "Input file contains no audio stream; cannot perform audio-only extraction"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)
        # Decide audio codec/container by output extension; prefer AAC in .m4a for broad compatibility
        a_codec = 'aac' if output_path.lower().endswith('.m4a') else (audio_codec if audio_codec != 'none' else 'aac')
        a_bitrate_str = f"{int(max(64, audio_bitrate_kbps))}k"
        # Build simple ffmpeg command to extract/transcode audio
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", input_path,
            "-vn",
            "-c:a", a_codec, "-b:a", a_bitrate_str,
            "-movflags", "+faststart" if output_path.lower().endswith('.m4a') else "",
            output_path,
        ]
        # Remove empty flags
        cmd = [c for c in cmd if c != ""]
        _publish(self.request.id, {"type": "log", "message": f"FFmpeg (audio-only): {' '.join(cmd)}"})
        rc, was_cancelled = (subprocess.run(cmd, text=True).returncode, False)
        if rc != 0:
            msg = f"Audio extraction failed with code {rc}"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)
        # Publish completion
        final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        stats = {
            "input_path": input_path,
            "output_path": output_path,
            "duration_s": duration,
            "target_size_mb": target_size_mb,
            "final_size_mb": round(final_size / (1024*1024), 2),
        }
        _publish(self.request.id, {"type": "progress", "progress": 100.0, "phase": "done"})
        try:
            self.update_state(state="SUCCESS", meta={"output_path": output_path, "progress": 100.0, "detail": "done", **stats})
        except Exception:
            pass
        _publish(self.request.id, {"type": "done", "stats": stats})
        return stats

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
    
    # Handle "extraquality" preset (slowest, best quality) — not compatible with fixed target bitrate
    if preset_val == "extraquality" and not bitrate_mode:
        _publish(self.request.id, {"type": "log", "message": "Extra Quality mode enabled (slowest encoding, best quality)"})
        if actual_encoder.endswith("_nvenc"):
            preset_flags = ["-preset", "p7"]
            tune_flags = ["-tune", "hq"]
            preset_flags += ["-rc:v", "vbr", "-cq:v", "19", "-b:v", "0"]
        elif actual_encoder in ("libx264", "libx265"):
            preset_flags = ["-preset", "veryslow"]
            if actual_encoder == "libx264":
                tune_flags = ["-tune", "film"]
                preset_flags += ["-crf", "18"]
            else:
                preset_flags += ["-crf", "20"]
        elif actual_encoder == "libaom-av1":
            preset_flags = ["-cpu-used", "0"]
            preset_flags += ["-crf", "20"]
    elif actual_encoder.endswith("_nvenc"):
        preset_flags = ["-preset", preset_val]
        tune_flags = ["-tune", tune_val]
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
    
    # Resolution scaling (explicit or auto) — use display dimensions when rotation metadata swaps W/H
    if auto_resolution:
        aw, ah = choose_auto_resolution(
            disp_w, disp_h, info.get("video_bitrate_kbps"),
            video_kbps, min_auto_resolution, target_resolution
        )
        if ah:
            max_height = ah
            _publish(self.request.id, {"type": "log", "message": f"Auto-resolution: targeting ≤{max_height}p based on bitrate budget"})
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
                    # Use trimmed duration for accurate progress scaling
                    try:
                        duration = float(duration_sec)
                    except Exception:
                        pass
            except Exception as e:
                _publish(self.request.id, {"type": "log", "message": f"Warning: Could not parse trim times: {e}"})
        else:
            # No start time, use -to
            duration_opts = ["-to", str(end_time)]
            _publish(self.request.id, {"type": "log", "message": f"Trimming: end at {end_time}"})
            # If only end_time provided, set duration to end timestamp if parsable
            try:
                et = str(end_time)
                if ':' in et:
                    parts = et.split(':')
                    if len(parts) == 3:
                        duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                    elif len(parts) == 2:
                        duration = int(parts[0]) * 60 + float(parts[1])
                else:
                    duration = float(et)
            except Exception:
                pass

    # Decide decoder strategy based on input codec and runtime capability
    in_codec = info.get("video_codec")

    def has_decoder(dec_name: str) -> bool:
        try:
            r = subprocess.run([
                "ffmpeg", "-hide_banner", "-decoders"
            ], capture_output=True, text=True, timeout=5, env=get_gpu_env())
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
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env())
            stderr = (r.stderr or "").lower()
            fail_patterns = [
                "doesn't support hardware accelerated", "failed setup for format cuda",
                "not supported", "invalid argument", "error while opening decoder",
                "no decoder surface",
            ]
            if any(s in stderr for s in fail_patterns):
                return False
            return r.returncode == 0 and "error" not in stderr
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
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env())
            stderr = (r.stderr or "").lower()
            fail_patterns = [
                "not found", "unknown decoder", "cannot load", "init failed",
                "device not present", "not supported", "invalid argument",
                "error while opening decoder", "no decoder surface",
            ]
            if any(s in stderr for s in fail_patterns):
                return False
            return r.returncode == 0 and "error" not in stderr
        except Exception:
            return False

    # Log force decode preference once
    if force_hw_decode:
        _publish(self.request.id, {"type": "log", "message": "Force hardware decode: enabled"})

    # AV1 decode strategy (skip GPU decode when display rotation metadata is present — it is not applied like software)
    if in_codec == "av1":
        if actual_encoder.endswith("_nvenc"):
            # Never use av1_cuvid from "decoder exists in ffmpeg" alone: force_hw_decode / preferHwDecode
            # must not bypass a runtime probe — many builds list av1_cuvid while the GPU/driver cannot decode AV1.
            if has_decoder("av1_cuvid") and can_av1_cuvid_decode(input_path) and rot_deg % 360 == 0:
                init_hw_flags = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] + init_hw_flags
                input_opts += ["-c:v", "av1_cuvid"]
                v_flags = [f for i, f in enumerate(v_flags) if not (f == "-pix_fmt" or (i > 0 and v_flags[i-1] == "-pix_fmt"))]
                if vf_filters:
                    vf_filters = [f.replace("scale=", "scale_npp=") for f in vf_filters]
                _publish(self.request.id, {"type": "log", "message": "Decoder: av1_cuvid (CUDA) probe passed; GPU decode + NVENC encode"})
            else:
                input_opts += ["-c:v", "libdav1d"]
                msg = "Decoder: libdav1d (software AV1 decode) — av1_cuvid missing or probe failed"
                if force_hw_decode:
                    msg += " (hardware decode was preferred but is not usable for this file/GPU)"
                _publish(self.request.id, {"type": "log", "message": msg})
        else:
            input_opts += ["-c:v", "libdav1d"]
            _publish(self.request.id, {"type": "log", "message": "Decoder: using libdav1d (CPU encoder path)"})
    elif in_codec in ("h264", "hevc") and actual_encoder.endswith("_nvenc") and rot_deg % 360 == 0:
        # H.264/HEVC: NVDEC widely supported; prefer CUDA when using NVENC (software decode if rotation metadata must be honored)
        init_hw_flags = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] + init_hw_flags
        # Remove -pix_fmt if present (GPU surfaces)
        v_flags = [f for i, f in enumerate(v_flags) if not (f == "-pix_fmt" or (i > 0 and v_flags[i-1] == "-pix_fmt"))]
        # Switch scale filter to GPU variant if scaling is requested
        if vf_filters:
            vf_filters = [f.replace("scale=", "scale_npp=") for f in vf_filters]
        _publish(self.request.id, {"type": "log", "message": f"Decoder: using cuda ({in_codec})"})

    # Optional output frame-rate cap: only when we know source fps is above the cap (same as input otherwise).
    input_fps = info.get("video_fps")
    if max_output_fps is not None and float(max_output_fps) > 0:
        cap = float(max_output_fps)
        if input_fps is None:
            _publish(self.request.id, {"type": "log", "message": (
                f"Frame rate: {cap:g} fps cap not applied — could not read source frame rate; "
                "keeping native timing (same as input)."
            )})
        elif float(input_fps) <= cap + 0.01:
            pass  # already at or below cap — preserve input fps
        else:
            fps_filter = f"fps=fps={cap}"
            has_npp = any("scale_npp=" in f for f in vf_filters)
            cuda_hw_frames = (
                actual_encoder.endswith("_nvenc")
                and "-hwaccel_output_format" in init_hw_flags
                and "cuda" in init_hw_flags
            )
            if has_npp or cuda_hw_frames:
                if vf_filters:
                    vf_filters = [",".join(vf_filters) + ",hwdownload,format=yuv420p," + fps_filter]
                else:
                    vf_filters = [f"hwdownload,format=yuv420p,{fps_filter}"]
                if actual_encoder.endswith("_nvenc") and not any(
                    v_flags[i] == "-pix_fmt" for i in range(len(v_flags))
                ):
                    nvenc_pix = ["-pix_fmt", "yuv420p"]
                    if "h264" in actual_encoder:
                        nvenc_pix += ["-profile:v", "high"]
                    elif "hevc" in actual_encoder:
                        nvenc_pix += ["-profile:v", "main"]
                    v_flags = nvenc_pix + v_flags
            else:
                if vf_filters:
                    vf_filters = vf_filters + [fps_filter]
                else:
                    vf_filters = [fps_filter]
            ip = f"{float(input_fps):.3g}"
            _publish(self.request.id, {"type": "log", "message": f"Frame rate: capping at {cap:g} fps (source ~{ip} fps)"})

    # Construct command (decoder autorotate is left ON for SW decode — manual transpose was flipping some phone clips)
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        *init_hw_flags,  # Hardware initialization (CUDA device setup)
        *input_opts,  # -ss before input for fast seeking
        "-i", input_path,
        *duration_opts,  # -t or -to for duration/end
        "-c:v", actual_encoder,  # Use detected encoder
        *v_flags,
    ]
    
    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]
    
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
        proc_i = subprocess.Popen(command, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1, env=get_gpu_env())
        local_stderr = []
        nonlocal last_progress
        nonlocal speed_ewma
        emitted_initial_progress = False
        cancelled = False
        last_update_time = time.time()
        
        # Track multiple progress signals from ffmpeg
        current_time_s = 0.0  # out_time_ms converted to seconds
        current_size_bytes = 0  # total_size in bytes
        current_bitrate_kbps = 0.0  # bitrate in kbps
        last_time_s = 0.0  # Track last time value to detect restarts
        
        # Dynamic progress emit threshold
        min_step = 0.0005  # 0.05%
        if duration and duration < 120:
            min_step = 0.00025  # 0.025% for very short content
        max_update_interval = 2.0  # Force update every 2 seconds
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
                    if last_progress < 0.001:
                        last_progress = 0.001
                        _publish(self.request.id, {"type": "progress", "progress": 0.1, "phase": "encoding"})
                        try:
                            self.update_state(state="PROGRESS", meta={"progress": 0.1, "phase": "encoding"})
                        except Exception:
                            pass
                if "=" in line:
                    key, _, val = line.partition("=")
                    
                    # Collect all progress metrics from ffmpeg
                    if key == "out_time_ms":
                        try:
                            new_time_s = int(val) / 1000.0
                            
                            # Detect FFmpeg restart (time goes backwards significantly)
                            if last_time_s > 0 and new_time_s < (last_time_s * 0.5):
                                # FFmpeg restarted (retry or new pass) - reset tracking
                                current_size_bytes = 0
                                current_bitrate_kbps = 0.0
                                last_progress = 0.0
                                time_start = time.time()  # Reset start time for wallclock
                                speed_ewma = None  # Reset speed EWMA
                                _publish(self.request.id, {"type": "log", "message": "⚠️ Encoding restarted, resetting progress..."})
                            
                            current_time_s = new_time_s
                            last_time_s = new_time_s
                        except Exception:
                            pass
                    elif key == "total_size":
                        try:
                            current_size_bytes = int(val)
                        except Exception:
                            pass
                    elif key == "bitrate":
                        try:
                            # bitrate comes as "1234.5kbits/s" - extract number
                            br_str = val.strip().replace("kbits/s", "").replace("kbit/s", "")
                            current_bitrate_kbps = float(br_str)
                        except Exception:
                            pass
                    elif key == "speed":
                        try:
                            sval = (val or "").strip()
                            if sval.endswith("x"):
                                sval = sval[:-1]
                            sp = float(sval)
                            if math.isfinite(sp) and sp > 0:
                                speed_ewma = sp if (speed_ewma is None) else (ewma_alpha*sp + (1.0-ewma_alpha)*speed_ewma)
                        except Exception:
                            pass
                    
                    # Calculate progress using multiple signals
                    if key == "out_time_ms" and duration > 0:
                        try:
                            # Primary: Time-based progress (most stable and predictable)
                            time_progress = min(max(current_time_s / duration, 0.0), 1.0)
                            
                            # Secondary: Wall-clock estimate using measured speed
                            elapsed = max(time.time() - start_ts, 0.0)
                            wallclock_progress = 0.0
                            if speed_ewma and speed_ewma > 0.01 and duration > 0 and elapsed > 2.0:
                                try:
                                    est_total_time = duration / speed_ewma
                                    if est_total_time > 0:
                                        wallclock_progress = min(max(elapsed / est_total_time, 0.0), 1.0)
                                except Exception:
                                    pass
                            
                            # Tertiary: Size-based sanity check (detect if way off)
                            target_bytes = progress_target_mb * 1024 * 1024
                            size_progress = 0.0
                            if current_size_bytes > 0 and target_bytes > 0:
                                # Only use size if it's reasonable (within 2x of time progress)
                                raw_size_progress = current_size_bytes / target_bytes
                                if raw_size_progress < (time_progress * 2.0):
                                    size_progress = raw_size_progress
                            
                            # Simple weighted blend favoring time stability
                            if wallclock_progress > 0.01 and elapsed > 3.0:
                                # Blend time (70%) and wallclock (30%) after speed stabilizes
                                scaled_progress = (0.7 * time_progress + 0.3 * wallclock_progress) * encoding_portion
                            else:
                                # Pure time-based (most stable)
                                scaled_progress = time_progress * encoding_portion
                            
                            # Allow backwards progress (user OK with this)
                            # Just clamp to valid range
                            scaled_progress = min(max(scaled_progress, 0.0), encoding_portion)
                            
                            # Skip confused analysis phase more aggressively
                            # FFmpeg analysis can report high progress (80-98%) very quickly
                            # Only report when we have actual encoding happening (significant output size)
                            should_report = (
                                scaled_progress >= 0.03 and  # Skip first 3%
                                speed_ewma is not None and   # Have speed data
                                speed_ewma > 0.1 and         # Speed is meaningful (not just analysis)
                                elapsed > 2.0 and            # At least 2 seconds elapsed
                                current_size_bytes > 100000  # At least 100KB output (real encoding started)
                            )
                            
                            if should_report:
                                last_progress = scaled_progress

                            # Compute ETA
                            eta_seconds = None
                            if speed_ewma and speed_ewma > 0.01 and duration > 0:
                                try:
                                    est_total = (duration / speed_ewma)
                                    fin_factor = 1.0
                                    if is_mp4 and not fast_mp4_finalize:
                                        fin_factor = 1.15
                                    total_with_final = est_total * (encoding_portion + fin_factor*finalize_portion)
                                    eta_seconds = max(total_with_final - elapsed, 0.0)
                                except Exception:
                                    eta_seconds = None

                            # Update if progress changed OR time elapsed (only if should_report)
                            if should_report:
                                time_since_update = time.time() - last_update_time
                                progress_delta = abs(scaled_progress - last_progress)
                                should_update = (
                                    progress_delta >= min_step or 
                                    scaled_progress >= (encoding_portion - 0.001) or
                                    time_since_update >= max_update_interval
                                )
                                
                                if should_update:
                                    last_update_time = time.time()
                                    prog = round(scaled_progress*100, 2)
                                    evt = {"type": "progress", "progress": prog, "phase": "encoding"}
                                    if eta_seconds is not None and math.isfinite(eta_seconds):
                                        evt["eta_seconds"] = round(float(eta_seconds), 1)
                                    if speed_ewma is not None and math.isfinite(speed_ewma):
                                        evt["speed_x"] = round(float(speed_ewma), 2)
                                    _publish(self.request.id, evt)
                                    try:
                                        meta = {"progress": prog, "phase": "encoding"}
                                        if "eta_seconds" in evt:
                                            meta["eta_seconds"] = evt["eta_seconds"]
                                        self.update_state(state="PROGRESS", meta=meta)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                    
                    # Log non-progress keys for debugging
                    if key not in ("out_time_ms", "total_size", "bitrate", "speed"):
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

    # Decode-error retry: if the failure looks like a hardware decoder issue,
    # retry the same encoder with software decode before falling back to CPU.
    decode_fail_hints = ["cuvid", "error while opening decoder", "hwaccel", "not supported"]
    if rc != 0 and not was_cancelled and any(h in '\n'.join(stderr_lines).lower() for h in decode_fail_hints):
        _publish(self.request.id, {"type": "log", "message": "⚠️ Hardware decode failed. Retrying with software decoder..."})
        sw_input_opts = [o for i, o in enumerate(input_opts)
                         if not (o in ("-c:v",) or
                                 (i > 0 and input_opts[i-1] == "-c:v"))]
        if in_codec == "av1":
            sw_input_opts += ["-c:v", "libdav1d"]
        sw_vf = [f.replace("scale_npp=", "scale=") for f in vf_filters] if vf_filters else []
        sw_v_flags = v_flags
        if "-pix_fmt" not in v_flags and actual_encoder.endswith("_nvenc"):
            sw_v_flags = ["-pix_fmt", "yuv420p"] + sw_v_flags
        retry_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            *sw_input_opts,
            "-i", input_path,
            *duration_opts,
            "-c:v", actual_encoder,
            *sw_v_flags,
        ]
        if sw_vf:
            retry_cmd += ["-vf", ",".join(sw_vf)]
        retry_cmd += [
            "-b:v", f"{int(video_kbps)}k",
            "-maxrate", f"{maxrate}k",
            "-bufsize", f"{bufsize}k",
            *preset_flags, *tune_flags,
        ]
        if chosen_audio_codec is None:
            retry_cmd += ["-an"]
        else:
            retry_cmd += ["-c:a", chosen_audio_codec, "-b:a", a_bitrate_str]
        retry_cmd += [*mp4_flags, "-progress", "pipe:2", output_path]
        _publish(self.request.id, {"type": "log", "message": f"FFmpeg retry: {' '.join(retry_cmd)}"})
        stderr_lines = []
        last_progress = 0.0
        rc, was_cancelled = run_ffmpeg_and_stream(retry_cmd)
        if was_cancelled:
            _publish(self.request.id, {"type": "canceled"})
            msg = "Job canceled by user"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)

    if rc != 0 and actual_encoder.endswith("_nvenc"):
        _publish(self.request.id, {"type": "log", "message": f"⚠️ Hardware encode failed (rc={rc}). Retrying on CPU..."})
        _publish(self.request.id, {"type": "log", "message": (
            "Explanation: The hardware encoder failed at runtime. The worker will retry using a CPU encoder which is slower. "
            "This can happen if drivers, device nodes, or libraries are missing or if the encoder is unsupported by the current ffmpeg build. "
            "Run the encoder diagnostic tests from the UI or check logs to investigate."
        )})
        if "h264" in actual_encoder:
            fb_encoder = "libx264"; fb_flags = ["-pix_fmt","yuv420p","-profile:v","high"]
        elif "hevc" in actual_encoder or "h265" in actual_encoder:
            fb_encoder = "libx265"; fb_flags = ["-pix_fmt","yuv420p"]
        else:
            fb_encoder = "libaom-av1"; fb_flags = ["-pix_fmt","yuv420p"]

        _publish(self.request.id, {"type": "log", "message": f"Encoder: CPU ({fb_encoder})"})
        actual_encoder = fb_encoder

        # Strip any hardware decode options; use software decode for CPU fallback
        cpu_input_opts = [o for i, o in enumerate(input_opts)
                          if not (o in ("-c:v",) or
                                  (i > 0 and input_opts[i-1] == "-c:v"))]
        # Use libdav1d for AV1 input, otherwise let FFmpeg auto-select
        if in_codec == "av1":
            cpu_input_opts += ["-c:v", "libdav1d"]
        # Revert scale_npp back to scale for CPU path
        cpu_vf = [f.replace("scale_npp=", "scale=") for f in vf_filters] if vf_filters else []

        cmd2 = [
            "ffmpeg", "-hide_banner", "-y",
            *cpu_input_opts,
            "-i", input_path,
            *duration_opts,
            "-c:v", fb_encoder,
            *fb_flags,
        ]
        if cpu_vf:
            cmd2 += ["-vf", ",".join(cpu_vf)]
        cmd2 += [
            "-b:v", f"{int(video_kbps)}k",
            "-maxrate", f"{maxrate}k",
            "-bufsize", f"{bufsize}k",
        ]
        if fb_encoder == "libx264":
            cmd2 += ["-preset","medium","-tune","film"]
        elif fb_encoder == "libx265":
            cmd2 += ["-preset","medium"]
        elif fb_encoder == "libaom-av1":
            cmd2 += ["-cpu-used","4"]
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

    # Encoding complete - move to end of encoding portion and start finalization steps
    enc_done_pct = round(encoding_portion*100, 2)
    _publish(self.request.id, {"type": "progress", "progress": enc_done_pct, "phase": "finalizing"})
    try:
        self.update_state(state="PROGRESS", meta={"progress": enc_done_pct, "phase": "finalizing"})
    except Exception:
        pass
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
    # Bump progress as we complete verification - halfway through finalization
    verify_pct = round((encoding_portion + finalize_portion*0.5)*100, 2)
    _publish(self.request.id, {"type": "progress", "progress": verify_pct, "phase": "finalizing"})
    try:
        self.update_state(state="PROGRESS", meta={"progress": verify_pct, "phase": "finalizing"})
    except Exception:
        pass

    # Checking file size and preparing for possible retry
    final_size_mb = round(final_size / (1024*1024), 2) if final_size else 0
    
    # Check if file is too large (>2% over target) and retry with lower bitrate (size-target mode only)
    size_overage_percent = ((final_size_mb - progress_target_mb) / progress_target_mb) * 100 if progress_target_mb > 0 else 0
    
    # Track retry attempt (stored in task metadata)
    retry_attempt = self.request.retries or 0
    max_retries = 2  # Maximum 2 retry attempts
    
    if (not bitrate_mode) and size_overage_percent > 2.0 and final_size_mb > progress_target_mb and retry_attempt < max_retries:
        # Calculate if retry is feasible
        # If we need to reduce bitrate below 50%, it's probably impossible
        reduction_factor = max(0.5, 1.0 - (size_overage_percent / 100.0) - 0.05)
        
        if reduction_factor < 0.5:
            _publish(self.request.id, {"type": "log", "message": f"⚠️ File is {size_overage_percent:.1f}% over target, but further reduction would compromise quality too much."})
            _publish(self.request.id, {"type": "log", "message": f"📊 Final size: {final_size_mb:.2f} MB (target was {progress_target_mb:.2f} MB). Consider adjusting target size or resolution."})
        else:
            # File is too large! Notify user and retry
            _publish(self.request.id, {"type": "log", "message": f"⚠️ File is {size_overage_percent:.1f}% over target ({final_size_mb:.2f} MB vs {progress_target_mb:.2f} MB)"})
            _publish(self.request.id, {"type": "log", "message": f"🔄 Retry attempt {retry_attempt + 1}/{max_retries} with reduced bitrate..."})
            _publish(self.request.id, {"type": "retry", "message": f"File too large ({final_size_mb:.2f} MB), retrying to fit {progress_target_mb:.2f} MB target (attempt {retry_attempt + 1}/{max_retries})", "overage_percent": round(size_overage_percent, 1)})
            
            # Calculate adjusted bitrate
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
            try:
                self.update_state(state="PROGRESS", meta={"progress": 1.0, "phase": "encoding"})
            except Exception:
                pass
            
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
                _publish(self.request.id, {"type": "error", "message": f"Retry encode failed with return code {rc}. Using best result."})
                # Don't fail completely, just note the retry failed
            else:
                # Update final size after successful retry
                try:
                    final_size = os.path.getsize(output_path)
                    final_size_mb = round(final_size / (1024*1024), 2)
                    new_overage = ((final_size_mb - progress_target_mb) / progress_target_mb) * 100 if progress_target_mb > 0 else 0
                    if new_overage <= 0:
                        _publish(self.request.id, {"type": "log", "message": f"✅ Retry successful! Final size: {final_size_mb:.2f} MB (under target)"})
                    else:
                        _publish(self.request.id, {"type": "log", "message": f"✅ Retry complete! Final size: {final_size_mb:.2f} MB ({new_overage:+.1f}% vs target)"})
                except Exception:
                    final_size = 0
    elif size_overage_percent > 2.0 and retry_attempt >= max_retries:
        _publish(self.request.id, {"type": "log", "message": f"⚠️ File is {size_overage_percent:.1f}% over target after {max_retries} retries. Keeping best result."})
        _publish(self.request.id, {"type": "log", "message": f"📊 Final size: {final_size_mb:.2f} MB (target was {progress_target_mb:.2f} MB)"})
    
    stats = {
        "input_path": input_path,
        "output_path": output_path,
        "duration_s": duration,
        "target_size_mb": target_size_mb,
        "final_size_mb": final_size_mb,
        "target_video_bitrate_kbps": int(video_kbps) if bitrate_mode else None,
    }
    
    # Advance progress before final save - 3/4 through finalization
    presave_pct = round((encoding_portion + finalize_portion*0.75)*100, 2)
    _publish(self.request.id, {"type": "progress", "progress": presave_pct, "phase": "finalizing"})
    try:
        self.update_state(state="PROGRESS", meta={"progress": presave_pct, "phase": "finalizing"})
    except Exception:
        pass

    # Add to history if enabled
    try:
        # Default ON if variable not set
        history_enabled = os.getenv('HISTORY_ENABLED', 'true').lower() in ('true', '1', 'yes')
        if history_enabled:
            # Import here to avoid circular dependency
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
    try:
        self.update_state(state="SUCCESS", meta={"output_path": output_path, "progress": 100.0, "detail": "done", **stats})
    except Exception:
        pass
    _publish(self.request.id, {"type": "done", "stats": stats})
    return stats
