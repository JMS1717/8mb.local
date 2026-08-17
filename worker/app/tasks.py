from __future__ import annotations

import json
import functools
import math
import os
import queue
import shlex
import signal
import subprocess
import threading
import time
import logging
import sys
import uuid
from pathlib import Path
from typing import Dict, Optional
from redis import Redis

from shared.subprocess_utils import hidden_process_kwargs
from shared.concurrency import AdaptiveConcurrencyGate, configured_worker_concurrency

from .celery_app import celery_app
from .constants import (
    CPU_FALLBACK, CPU_ENCODERS, HW_ENCODERS,
    LIBAOM_AV1, SVT_AV1, LIBX264, LIBX265,
    AMF_ENCODERS, QSV_ENCODERS, VAAPI_ENCODERS,
)
from .utils import ffprobe_info, calc_bitrates
from .auto_resolution import choose_auto_resolution
from .hw_detect import get_hw_info, map_codec_to_hw, choose_best_codec
from .ffmpeg_helpers import cpu_filter_chain, replace_bitrate_args
from .startup_tests import run_startup_tests
from .progress import parse_ffmpeg_out_time, parse_time_string
from .qsv_filters import qsv_input_filter

logger = logging.getLogger(__name__)

REDIS = None
# Cache encoder test results to avoid slow init tests on every job
ENCODER_TEST_CACHE: Dict[str, bool] = {}
_LAST_PUBLISH_WARNING_TS = 0.0
_ENCODE_GATE: AdaptiveConcurrencyGate | None = None


def _encode_gate() -> AdaptiveConcurrencyGate:
    """Return the process/runtime-wide adaptive encode gate."""
    global _ENCODE_GATE
    if _ENCODE_GATE is not None:
        return _ENCODE_GATE
    configured = configured_worker_concurrency()
    local_runtime = os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}
    redis_client = None
    if not local_runtime:
        try:
            redis_client = _redis()
            redis_client.ping()
        except Exception as exc:
            logger.warning("adaptive concurrency: Redis gate unavailable; using process-local gate: %s", exc)
            redis_client = None
    _ENCODE_GATE = AdaptiveConcurrencyGate(configured, redis_client=redis_client)
    return _ENCODE_GATE


def effective_trim_duration(source_duration: float, start_time: str | None, end_time: str | None) -> float:
    """Return the duration used for target-size bitrate math after trimming."""
    source = float(source_duration or 0.0)
    if source <= 0 and not (start_time or end_time):
        return source
    start = parse_time_string(start_time) if start_time else 0.0
    end = parse_time_string(end_time) if end_time else source
    if not math.isfinite(start) or start < 0:
        raise ValueError('start time must be non-negative')
    if not math.isfinite(end) or end <= 0:
        raise ValueError('end time must be positive')
    if source > 0 and start >= source:
        raise ValueError('start time is beyond the input duration')
    if end <= start:
        raise ValueError('end time must be greater than start time')
    actual_end = min(end, source) if source > 0 else end
    return max(actual_end - start, 0.0)


def mp4_video_tag_args(output_path: str, encoder: str) -> list[str]:
    """Use Apple's compatible HEVC sample-entry tag for MP4 outputs.

    ``hev1`` is a valid ISO-BMFF tag, but Apple/iOS playback commonly expects
    ``hvc1`` for HEVC in MP4. This does not change the encoded bitstream; it
    only makes the container metadata advertise the stream in the broadly
    compatible form. Other codecs and non-MP4 containers keep FFmpeg's
    default tag.
    """
    if str(output_path).lower().endswith('.mp4') and (
        encoder == 'libx265' or 'hevc' in str(encoder).lower()
    ):
        return ['-tag:v', 'hvc1']
    return []

# libsvtav1: SVT-AV1's LevelOfParallelism (``lp``) is a library tuning level,
# not a core count. Older releases forced ``lp=6`` for throughput, but that
# can overcommit small VMs and has caused encoder crashes. Let SVT choose a
# safe level by default; operators with a known-good host can opt in with
# ``SVTAV1_LP=0..6``.
def _svtav1_params() -> list[str]:
    value = os.getenv("SVTAV1_LP", "auto").strip().lower()
    if value in {"", "auto", "default"}:
        return []
    try:
        level = max(0, min(6, int(value)))
    except ValueError:
        logger.warning("Invalid SVTAV1_LP=%r; using SVT automatic parallelism", value)
        return []
    return ["-svtav1-params", f"lp={level}"]


def _cpu_fallback_for(
    encoder: str,
    available_cpu_encoders: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Return (cpu_encoder, v_flags) to use when a hardware encoder fails.

    Uses CPU_FALLBACK from constants so AV1 falls back to SVT-AV1 (FFmpeg's
    ``libsvtav1`` token).  libaom-av1 is intentionally not an automatic
    fallback: if SVT-AV1 is absent, failing clearly is safer than silently
    turning a fast hardware request into a very slow encode.
    """
    fb = CPU_FALLBACK.get(encoder)
    if fb is None:
        # Bare codec name fallbacks
        if "h264" in encoder:
            fb = LIBX264
        elif "hevc" in encoder or "h265" in encoder:
            fb = LIBX265
        elif "av1" in encoder:
            fb = SVT_AV1
        else:
            fb = LIBX264
    if available_cpu_encoders is not None and fb not in available_cpu_encoders:
        if fb == SVT_AV1:
            raise RuntimeError(
                "SVT-AV1 is required for the CPU AV1 fallback, but this FFmpeg build "
                "does not expose libsvtav1"
            )
        logger.warning(
            "Configured CPU fallback %s for %s is not in the FFmpeg inventory",
            fb,
            encoder,
        )
    if fb == LIBX264:
        flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
    else:
        flags = ["-pix_fmt", "yuv420p"]
    logger.debug("_cpu_fallback_for(%s) -> (%s, %s)", encoder, fb, flags)
    return fb, flags


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
        '/usr/lib/x86_64-linux-gnu/dri',
        '/usr/lib/dri',
    ]
    existing = env.get('LD_LIBRARY_PATH', '')
    add = ':'.join(p for p in lib_paths if p)
    env['LD_LIBRARY_PATH'] = (existing + (':' if existing and add else '') + add) if (existing or add) else ''
    return env


def _is_hardware_encoder(encoder: str) -> bool:
    return encoder in HW_ENCODERS



def _redis() -> Redis:
    global REDIS
    if REDIS is None:
        if os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}:
            from shared.local_runtime import get_sync_redis

            REDIS = get_sync_redis()
        else:
            REDIS = Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    return REDIS


def _publish(task_id: str, event: Dict) -> bool:
    global _LAST_PUBLISH_WARNING_TS
    event.setdefault("task_id", task_id)
    try:
        _redis().publish(f"progress:{task_id}", json.dumps(event))
        if os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}:
            from shared.local_runtime import record_worker_event

            record_worker_event(task_id, event)
        return True
    except Exception as exc:
        # Progress is best-effort. Redis can briefly restart or lose a pub/sub
        # connection; that must not turn a successful FFmpeg encode into a
        # failed job. The queue/status metadata remains the source of truth.
        now = time.monotonic()
        if now - _LAST_PUBLISH_WARNING_TS >= 10.0:
            _LAST_PUBLISH_WARNING_TS = now
            logger.warning("progress publish failed for %s: %s", task_id[:8], exc)
        return False


def _is_cancelled(task_id: str) -> bool:
    try:
        val = _redis().get(f"cancel:{task_id}")
        return str(val) == '1'
    except Exception:
        return False


def _history_filename(job_id: str, input_path: str) -> str:
    """Recover the user-facing name from the UUID-prefixed staging path."""
    name = Path(input_path).name
    prefix = f"{job_id}_"
    if name.startswith(prefix) and len(name) > len(prefix):
        return name[len(prefix):]
    return name


def _force_stop_ffmpeg(proc: subprocess.Popen) -> None:
    """Hard-stop ffmpeg. libsvtav1 can keep CPU busy until SIGKILL if we only SIGTERM
    from a loop that was blocked on stderr; also kill the process group on POSIX when
    start_new_session was used."""
    if proc.poll() is not None:
        return
    try:
        proc.kill()
    except Exception:
        pass
    if sys.platform != "win32" and proc.pid:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def cleanup_transient_input(input_path: str | Path | None, transient_input: bool = False) -> None:
    """Remove an API-staged upload after its complete task lifecycle.

    Folder Watch and other path-based callers do not set ``transient_input``
    and therefore retain ownership of their source files. API uploads are
    temporary on every platform and are removed after success, retry/fallback,
    cancellation, or failure; the retention scheduler remains a crash-recovery
    backstop.
    """
    if not transient_input or input_path is None:
        return
    try:
        Path(input_path).unlink(missing_ok=True)
        logger.info("windows-temp: removed transient input %s", input_path)
    except OSError as exc:
        logger.warning("windows-temp: could not remove transient input %s: %s", input_path, exc)


def _cleanup_transient_input_after_task(func):
    """Guarantee source cleanup after success, retry exhaustion, or failure."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        input_path = kwargs.get("input_path")
        if input_path is None and len(args) >= 3:
            input_path = args[2]  # bound self, job_id, input_path
        transient_input = bool(kwargs.get("transient_input", False))
        gate = _encode_gate()
        lease = gate.acquire()
        logger.info(
            "adaptive concurrency: acquired encode slot task_id=%s limit=%s",
            getattr(getattr(args[0], "request", None), "id", "?"),
            gate.current_limit(),
        )
        try:
            return func(*args, **kwargs)
        finally:
            gate.release(lease)
            cleanup_transient_input(input_path, transient_input)
    return wrapper


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
@_cleanup_transient_input_after_task
def compress_video(self, job_id: str, input_path: str, output_path: str, target_size_mb: float,
                   video_codec: str, audio_codec: str, audio_bitrate_kbps: int, preset: str, tune: str = "hq",
                   max_width: int = None, max_height: int = None, start_time: str = None, end_time: str = None,
                   force_hw_decode: bool = False, fast_mp4_finalize: bool = False,
                   auto_resolution: bool = False, min_auto_resolution: int = 240,
                   target_resolution: int | None = None, audio_only: bool = False,
                   target_video_bitrate_kbps: float | None = None,
                   max_output_fps: float | None = None,
                   transient_input: bool = False):
    logger.info(
        "compress_video START task_id=%s job_id=%s codec=%s target_mb=%s preset=%s tune=%s "
        "audio=%s@%skbps container=%s audio_only=%s auto_res=%s max_wh=%s/%s "
        "target_res=%s fps_cap=%s force_hw_decode=%s fast_finalize=%s input=%s",
        self.request.id, job_id, video_codec, target_size_mb, preset, tune,
        audio_codec, audio_bitrate_kbps,
        Path(output_path).suffix.lstrip("."), audio_only, auto_resolution,
        max_width, max_height, target_resolution, max_output_fps,
        force_hw_decode, fast_mp4_finalize, input_path,
    )

    def remove_cancelled_output() -> None:
        """Remove any partial first-pass output after a cancellation."""
        try:
            Path(output_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove canceled partial output: %s", output_path)

    # Detect hardware acceleration
    _publish(self.request.id, {"type": "log", "message": "Initializing: detecting hardware…"})
    hw_info = get_hw_info()
    available_cpu_encoders = set(hw_info.get("available_cpu_encoders") or [])
    logger.debug(
        "compress_video hw_info: type=%s device=%s encoders=%s",
        hw_info.get("type"), hw_info.get("device"),
        list((hw_info.get("available_encoders") or {}).keys()),
    )
    _publish(self.request.id, {"type": "log", "message": f"Hardware: {hw_info['type'].upper()} acceleration detected"})
    
    # Probe
    _publish(self.request.id, {"type": "log", "message": "Initializing: probing input file…"})
    info = ffprobe_info(input_path, allow_audio_only=bool(audio_only))
    duration = info.get("duration", 0.0)
    if start_time or end_time:
        try:
            duration = effective_trim_duration(duration, start_time, end_time)
        except Exception as exc:
            raise RuntimeError(f"Invalid trim range: {exc}") from exc
        _publish(self.request.id, {"type": "log", "message": f"Target-size bitrate duration: {duration:.2f}s after trim"})
    logger.debug(
        "compress_video ffprobe: duration=%.2fs %sx%s v_kbps=%s a_kbps=%s fps=%s rotation=%s",
        duration, info.get("width"), info.get("height"),
        info.get("video_bitrate_kbps"), info.get("audio_bitrate_kbps"),
        info.get("video_fps"), info.get("rotation_degrees"),
    )
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

    # Bitrate controls (VBV peak / buffer — x264-style multipliers)
    maxrate = int(video_kbps * 1.2)
    bufsize = int(video_kbps * 2)

    def abr_rate_control_args(enc: str) -> list[str]:
        """Build rate-control + VBV args for target-bitrate encodes.

        **libsvtav1:** SVT caps need CRF for maxrate; use ``-b:v`` only.

        **NVENC / x264 / x265:** classic VBV-style **1.2×** peak and **2×** buffer vs average video kbps.
        """
        vb = int(video_kbps)
        bvk = f"{vb}k"
        if enc == SVT_AV1:
            return ["-b:v", bvk]
        return ["-b:v", bvk, "-maxrate", f"{maxrate}k", "-bufsize", f"{bufsize}k"]

    # Map requested codec to actual encoder and flags
    actual_encoder, v_flags, init_hw_flags = map_codec_to_hw(video_codec, hw_info)
    
    # Fallback to CPU only if startup tests explicitly marked encoder as unavailable.
    # If cache is empty (tests still running in background), attempt hardware and rely on runtime fallback below.
    original_encoder = actual_encoder
    if actual_encoder not in CPU_ENCODERS:
        global ENCODER_TEST_CACHE
        cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
        logger.debug(
            "startup-test cache lookup: key=%s present=%s value=%s",
            cache_key, cache_key in ENCODER_TEST_CACHE,
            ENCODER_TEST_CACHE.get(cache_key),
        )
        if cache_key in ENCODER_TEST_CACHE and not ENCODER_TEST_CACHE[cache_key]:
            _publish(self.request.id, {"type": "log", "message": f"⚠️ {actual_encoder} marked unavailable by startup tests, falling back to CPU"})
            _publish(self.request.id, {"type": "log", "message": (
                "Note: The selected hardware encoder failed initialization during startup tests. "
                "This means hardware acceleration for this codec is unavailable on this system; "
                "the job will use a CPU encoder instead which is typically much slower and increases CPU usage. "
                "To enable hardware encoding, ensure drivers/libraries are installed and run 'System → Run encoder tests' in the UI to refresh results."
            )})
            actual_encoder, v_flags = _cpu_fallback_for(actual_encoder, available_cpu_encoders)
            init_hw_flags = []
            logger.info(
                "CPU fallback selected (startup-test cache): %s -> %s",
                original_encoder, actual_encoder,
            )
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
        audio_temp_path = f"{output_path}.audio.{uuid.uuid4().hex}{Path(output_path).suffix}"
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", input_path,
            "-vn",
            "-c:a", a_codec, "-b:a", a_bitrate_str,
            "-movflags", "+faststart" if output_path.lower().endswith('.m4a') else "",
            audio_temp_path,
        ]
        # Remove empty flags
        cmd = [c for c in cmd if c != ""]
        _publish(self.request.id, {"type": "log", "message": f"FFmpeg (audio-only): {' '.join(cmd)}"})
        audio_proc = None
        was_cancelled = False
        rc = -1
        try:
            popen_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "env": get_gpu_env(),
            }
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True
            popen_kwargs.update(hidden_process_kwargs())
            audio_proc = subprocess.Popen(cmd, **popen_kwargs)
            while audio_proc.poll() is None:
                if _is_cancelled(self.request.id):
                    was_cancelled = True
                    _publish(self.request.id, {"type": "log", "message": "Cancel received, stopping audio extraction..."})
                    _force_stop_ffmpeg(audio_proc)
                    break
                time.sleep(0.25)
            try:
                audio_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                _force_stop_ffmpeg(audio_proc)
                audio_proc.wait(timeout=5)
            rc = audio_proc.returncode if audio_proc.returncode is not None else -1
        except Exception as exc:
            _publish(self.request.id, {"type": "log", "message": f"Audio extraction process failed: {exc}"})
            raise
        finally:
            if audio_proc is not None and audio_proc.poll() is None:
                _force_stop_ffmpeg(audio_proc)
            if was_cancelled or rc != 0:
                try:
                    if os.path.exists(audio_temp_path):
                        os.remove(audio_temp_path)
                except OSError:
                    pass

        if was_cancelled:
            remove_cancelled_output()
            _publish(self.request.id, {"type": "canceled"})
            msg = "Job canceled by user"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)
        if rc != 0:
            msg = f"Audio extraction failed with code {rc}"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)
        try:
            if not os.path.exists(audio_temp_path) or os.path.getsize(audio_temp_path) <= 0:
                raise RuntimeError("Audio extraction produced no output")
            os.replace(audio_temp_path, output_path)
        except Exception as exc:
            try:
                if os.path.exists(audio_temp_path):
                    os.remove(audio_temp_path)
            except OSError:
                pass
            msg = f"Audio extraction output could not be finalized: {exc}"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg) from exc
        # Publish completion
        final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        stats = {
            "input_path": input_path,
            "output_path": output_path,
            "duration_s": duration,
            "target_size_mb": target_size_mb,
            "final_size_mb": round(final_size / (1024*1024), 2),
        }
        try:
            if os.getenv("HISTORY_ENABLED", "true").lower() in ("true", "1", "yes"):
                import importlib

                app_root = os.getenv("BACKEND_APP_ROOT", "/app")
                sys.path.insert(0, app_root)
                try:
                    history = importlib.import_module("backend.history_manager")
                except ModuleNotFoundError:
                    history = importlib.import_module("app.history_manager")
                history.add_history_entry(
                    filename=_history_filename(job_id, input_path),
                    original_size_mb=os.path.getsize(input_path) / (1024 * 1024),
                    compressed_size_mb=stats["final_size_mb"],
                    video_codec="audio-only",
                    audio_codec=a_codec,
                    target_mb=target_size_mb,
                    preset=preset_val,
                    duration=max(time.time() - start_ts, 0),
                    task_id=self.request.id,
                    container="m4a",
                    tune=tune_val,
                    audio_bitrate_kbps=int(audio_bitrate_kbps),
                    encoder=a_codec,
                    output_filename=Path(output_path).name,
                )
        except Exception as exc:
            _publish(self.request.id, {"type": "log", "message": f"Failed to save audio history: {exc}"})
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
    
    # SVT-AV1 uses numeric presets 0 (slowest/best) .. 13 (fastest). p-scale mapping:
    # p1 fastest -> 12, p7 slowest -> 4, extraquality -> 2.
    svt_preset_map = {
        "p1": "12", "p2": "10", "p3": "9", "p4": "8",
        "p5": "7", "p6": "6", "p7": "4",
    }
    # libaom-av1 uses -cpu-used 0 (slowest) .. 8 (fastest)
    aom_cpu_used_map = {
        "p1": "8", "p2": "7", "p3": "6", "p4": "5",
        "p5": "4", "p6": "4", "p7": "2",
    }

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
        elif actual_encoder == SVT_AV1:
            preset_flags = ["-preset", "2", "-crf", "22", *_svtav1_params()]
        elif actual_encoder == "libaom-av1":
            preset_flags = ["-cpu-used", "0", "-crf", "20"]
        elif actual_encoder in QSV_ENCODERS:
            # QSV has a preset but no NVENC-style tune. Keep this conservative
            # because VAAPI encoders do not accept the same options.
            preset_flags = ["-preset", "slow"]
        elif actual_encoder in VAAPI_ENCODERS:
            preset_flags = []
    elif actual_encoder.endswith("_nvenc"):
        # Honor UI preset/tune only — do not switch AV1 (or other NVENC) to faster presets
        # based on target bitrate; low-bitrate jobs still use the user's quality choice.
        preset_flags = ["-preset", preset_val]
        tune_flags = ["-tune", tune_val]
    elif actual_encoder in QSV_ENCODERS:
        qsv_preset_map = {
            "p1": "veryfast", "p2": "faster", "p3": "fast",
            "p4": "medium", "p5": "slow", "p6": "slower", "p7": "veryslow",
        }
        preset_flags = ["-preset", qsv_preset_map.get(preset_val, "medium")]
    elif actual_encoder in VAAPI_ENCODERS or actual_encoder in AMF_ENCODERS:
        # VAAPI encoders use driver-specific quality/rate controls; FFmpeg's
        # generic -preset/-tune flags are not portable here. AMF similarly
        # varies by FFmpeg/driver version, so rate control stays conservative.
        preset_flags = []
    elif actual_encoder in ("libx264", "libx265"):
        cpu_preset_map = {"p1": "ultrafast", "p2": "superfast", "p3": "veryfast", "p4": "faster", "p5": "fast", "p6": "medium", "p7": "slow"}
        preset_flags = ["-preset", cpu_preset_map.get(preset_val, "medium")]
        if actual_encoder == "libx264":
            tune_flags = ["-tune", "film"]  # Better than 'hq' for CPU
    elif actual_encoder == SVT_AV1:
        preset_flags = ["-preset", svt_preset_map.get(preset_val, "8"), *_svtav1_params()]
    elif actual_encoder == "libaom-av1":
        preset_flags = ["-cpu-used", aom_cpu_used_map.get(preset_val, "4"), "-row-mt", "1"]

    logger.debug(
        "preset/tune selection: encoder=%s preset_val=%s tune_val=%s bitrate_mode=%s "
        "-> preset_flags=%s tune_flags=%s",
        actual_encoder, preset_val, tune_val, bitrate_mode, preset_flags, tune_flags,
    )

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
    
    trim_start_seconds = parse_time_string(start_time) if start_time else None
    trim_end_seconds = parse_time_string(end_time) if end_time else None
    if start_time:
        try:
            start_sec = trim_start_seconds
            if not math.isfinite(start_sec) or start_sec < 0:
                raise ValueError("start time must be non-negative")
        except Exception as e:
            raise RuntimeError(f"Invalid start time: {e}") from e
        # -ss before input for fast seeking
        input_opts += ["-ss", str(start_time)]
        _publish(self.request.id, {"type": "log", "message": f"Trimming: start at {start_time}"})
    
    if end_time:
        if start_time:
            try:
                start_sec = trim_start_seconds
                end_sec = trim_end_seconds
                if not math.isfinite(start_sec) or not math.isfinite(end_sec) or start_sec < 0 or end_sec <= start_sec:
                    raise ValueError("end time must be greater than start time, and both must be non-negative")
                duration_sec = end_sec - start_sec
                duration_opts = ["-t", str(duration_sec)]
                _publish(self.request.id, {"type": "log", "message": f"Trimming: duration {duration_sec:.2f}s (end at {end_time})"})
            except Exception as e:
                raise RuntimeError(f"Invalid trim range: {e}") from e
        else:
            duration_opts = ["-to", str(end_time)]
            _publish(self.request.id, {"type": "log", "message": f"Trimming: end at {end_time}"})

    # Decide decoder strategy based on input codec and runtime capability
    in_codec = info.get("video_codec")

    def has_decoder(dec_name: str) -> bool:
        try:
            r = subprocess.run([
                "ffmpeg", "-hide_banner", "-decoders"
            ], capture_output=True, text=True, timeout=5, env=get_gpu_env(), **hidden_process_kwargs())
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
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env(), **hidden_process_kwargs())
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
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env(), **hidden_process_kwargs())
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

    # QSV and VAAPI encode hardware frames, but software decode and software
    # scaling remain the most reliable path across Intel/AMD driver versions.
    # Upload only after all software filters (scale/fps) have been applied.
    if actual_encoder in QSV_ENCODERS:
        # Linux oneVPL requires a fixed pool; native Windows QSV performs its
        # own upload because explicit D3D11 hwupload rejects some real-world
        # rotated/vertical AV1 surfaces with E_INVALIDARG (0x80070057).
        qsv_filter = qsv_input_filter(sys.platform)
        vf_filters.append(qsv_filter)
        _publish(self.request.id, {
            "type": "log",
            "message": (
                f"Encoder: {actual_encoder} with software decode and "
                + ("QSV internal upload" if sys.platform == "win32" else "QSV hardware upload")
            ),
        })
    elif actual_encoder in VAAPI_ENCODERS:
        # Allow an already-uploaded VAAPI frame as well as software nv12.
        # This is the portable form used by FFmpeg's VAAPI filter graph.
        vf_filters.append("format=nv12|vaapi,hwupload")
        _publish(self.request.id, {
            "type": "log",
            "message": f"Encoder: {actual_encoder} with software decode and VAAPI hardware upload",
        })

    # Note: We do not inject -extra_hw_frames here. Large values (e.g. 16) plus the
    # default H.264 decoder thread count can exceed NVDEC's ~32 decode-surface budget and
    # make cuvidCreateDecoder fail. Capping -threads to compensate then slowed decodes
    # vs FFmpeg defaults — felt "way slower" than builds without those flags.

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
        *abr_rate_control_args(actual_encoder),
        *preset_flags,  # Encoder-specific preset
        *tune_flags,    # Encoder-specific tune (if supported)
    ]
    
    # Add audio encoding or disable audio if muted
    if chosen_audio_codec is None:
        cmd += ["-an"]  # No audio
    else:
        cmd += ["-c:a", chosen_audio_codec, "-b:a", a_bitrate_str]
    
    cmd += [
        *mp4_video_tag_args(output_path, actual_encoder),
        *mp4_flags,
        "-progress", "pipe:2",
        output_path,
    ]

    # Log the full ffmpeg command for debugging
    cmd_str = ' '.join(cmd)
    logger.info("ffmpeg exec task_id=%s cmd=%s", self.request.id, cmd_str)
    _publish(self.request.id, {"type": "log", "message": f"FFmpeg command: {cmd_str}"})

    def run_ffmpeg_and_stream(command: list) -> tuple[int, bool]:
        logger.debug("ffmpeg Popen pid=launching args[0..5]=%s", command[:6])
        _popen_kw: dict = {
            "stderr": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "text": True,
            "bufsize": 1,
            "env": get_gpu_env(),
        }
        if sys.platform != "win32":
            _popen_kw["start_new_session"] = True
        _popen_kw.update(hidden_process_kwargs())
        proc_i = subprocess.Popen(command, **_popen_kw)
        logger.debug("ffmpeg Popen pid=%s", proc_i.pid)
        local_stderr = []
        nonlocal last_progress
        nonlocal speed_ewma
        emitted_initial_progress = False
        cancelled = False
        last_update_time = time.time()
        
        # Track multiple progress signals from ffmpeg
        current_time_s = 0.0  # FFmpeg out_time_ms/out_time_us converted from microseconds
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
            # Read stderr on a thread so the main loop can poll cancel every ~250ms. A plain
            # ``for line in proc_i.stderr`` blocks until a full line arrives — libsvtav1 can go
            # long stretches without flushing stderr while pegging CPU, so Stop appeared broken.
            _line_q: queue.Queue[Optional[str]] = queue.Queue()

            def _stderr_reader() -> None:
                try:
                    for _ln in proc_i.stderr:
                        _line_q.put(_ln)
                except Exception:
                    pass
                finally:
                    _line_q.put(None)

            threading.Thread(target=_stderr_reader, daemon=True).start()

            while True:
                if _is_cancelled(self.request.id):
                    cancelled = True
                    _publish(self.request.id, {"type": "log", "message": "Cancel received, stopping encoder..."})
                    _force_stop_ffmpeg(proc_i)
                    break
                try:
                    line = _line_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if line is None:
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
                            new_time_s = parse_ffmpeg_out_time(val)
                            if new_time_s is None:
                                continue
                            
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
            else:
                try:
                    proc_i.wait(timeout=20)
                except Exception:
                    pass
            return (proc_i.returncode or 0, cancelled)
        finally:
            stderr_lines.extend(local_stderr)

    # Start process and optionally fall back to CPU on failure
    last_progress = 0.0
    stderr_lines: list[str] = []
    rc, was_cancelled = run_ffmpeg_and_stream(cmd)
    last_successful_cmd: list[str] | None = cmd.copy() if rc == 0 and not was_cancelled else None

    if was_cancelled:
        remove_cancelled_output()
        _publish(self.request.id, {"type": "canceled"})
        msg = "Job canceled by user"
        _publish(self.request.id, {"type": "error", "message": msg})
        raise RuntimeError(msg)

    # Decode-error retry: if the failure looks like a hardware decoder issue,
    # retry the same encoder with software decode before falling back to CPU.
    decode_fail_hints = ["cuvid", "error while opening decoder", "hwaccel", "not supported"]
    if (
        rc != 0
        and not was_cancelled
        and actual_encoder.endswith("_nvenc")
        and any(h in '\n'.join(stderr_lines).lower() for h in decode_fail_hints)
    ):
        _publish(self.request.id, {"type": "log", "message": "⚠️ Hardware decode failed. Retrying with software decoder..."})
        sw_input_opts = [o for i, o in enumerate(input_opts)
                         if not (o in ("-c:v",) or
                                 (i > 0 and input_opts[i-1] == "-c:v"))]
        if in_codec == "av1":
            sw_input_opts += ["-c:v", "libdav1d"]
        sw_vf = cpu_filter_chain(vf_filters)
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
            *abr_rate_control_args(actual_encoder),
            *preset_flags, *tune_flags,
        ]
        if chosen_audio_codec is None:
            retry_cmd += ["-an"]
        else:
            retry_cmd += ["-c:a", chosen_audio_codec, "-b:a", a_bitrate_str]
        retry_cmd += [*mp4_video_tag_args(output_path, actual_encoder), *mp4_flags, "-progress", "pipe:2", output_path]
        _publish(self.request.id, {"type": "log", "message": f"FFmpeg retry: {' '.join(retry_cmd)}"})
        stderr_lines = []
        last_progress = 0.0
        rc, was_cancelled = run_ffmpeg_and_stream(retry_cmd)
        if rc == 0 and not was_cancelled:
            last_successful_cmd = retry_cmd.copy()
        if was_cancelled:
            remove_cancelled_output()
            _publish(self.request.id, {"type": "canceled"})
            msg = "Job canceled by user"
            _publish(self.request.id, {"type": "error", "message": msg})
            raise RuntimeError(msg)

    if rc != 0 and _is_hardware_encoder(actual_encoder):
        _publish(self.request.id, {"type": "log", "message": f"⚠️ Hardware encode ({original_encoder}) failed (rc={rc}). Retrying on CPU..."})
        _publish(self.request.id, {"type": "log", "message": (
            "Explanation: The hardware encoder failed at runtime. The worker will retry using a CPU encoder which is slower. "
            "This can happen if drivers, device nodes, or libraries are missing or if the encoder is unsupported by the current ffmpeg build. "
            "Run the encoder diagnostic tests from the UI or check logs to investigate."
        )})
        try:
            fb_encoder, fb_flags = _cpu_fallback_for(actual_encoder, available_cpu_encoders)
        except RuntimeError as exc:
            logger.error("No approved CPU fallback for %s: %s", actual_encoder, exc)
            _publish(self.request.id, {"type": "error", "message": str(exc)})
            raise
        logger.info(
            "Runtime CPU fallback after hardware failure: %s -> %s (rc=%s)",
            actual_encoder, fb_encoder, rc,
        )
        _publish(self.request.id, {"type": "log", "message": f"Encoder: CPU ({fb_encoder})"})
        actual_encoder = fb_encoder

        # Strip any hardware decode options; use software decode for CPU fallback
        cpu_input_opts = [o for i, o in enumerate(input_opts)
                          if not (o in ("-c:v",) or
                                  (i > 0 and input_opts[i-1] == "-c:v"))]
        # Use libdav1d for AV1 input, otherwise let FFmpeg auto-select
        if in_codec == "av1":
            cpu_input_opts += ["-c:v", "libdav1d"]
        # Strip all hardware-frame filters for the CPU path. This also handles
        # a joined chain such as ``scale=...,hwdownload,format=yuv420p``.
        cpu_vf = cpu_filter_chain(vf_filters)

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
        cmd2 += abr_rate_control_args(fb_encoder)
        if fb_encoder == "libx264":
            cmd2 += ["-preset","medium","-tune","film"]
        elif fb_encoder == "libx265":
            cmd2 += ["-preset","medium"]
        elif fb_encoder == SVT_AV1:
            cmd2 += [
                "-preset", svt_preset_map.get(preset_val, "8"),
                *_svtav1_params(),
            ]
        elif fb_encoder == "libaom-av1":
            cmd2 += ["-cpu-used","4"]
        if chosen_audio_codec is None:
            cmd2 += ["-an"]
        else:
            cmd2 += ["-c:a", chosen_audio_codec, "-b:a", a_bitrate_str]
        cmd2 += [*mp4_video_tag_args(output_path, fb_encoder), *mp4_flags, "-progress", "pipe:2", output_path]

        rc, was_cancelled = run_ffmpeg_and_stream(cmd2)
        if rc == 0 and not was_cancelled:
            last_successful_cmd = cmd2.copy()

    if was_cancelled:
        remove_cancelled_output()
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
        # Re-scale video bitrate from measured output size vs target (works for
        # huge NVENC overshoots). Old logic used max(0.5, 1 - overage/100 - 0.05)
        # so e.g. +144% overage only halved bitrate — often still over target,
        # forcing another full encode (2× wall time) and frustrating UX.
        size_ratio = progress_target_mb / max(final_size_mb, 1e-6)
        adjusted_video_kbps = int(float(video_kbps) * size_ratio * 0.94)
        adjusted_video_kbps = max(48, adjusted_video_kbps)

        if adjusted_video_kbps >= int(video_kbps * 0.985):
            _publish(self.request.id, {"type": "log", "message": (
                f"⚠️ File is {size_overage_percent:.1f}% over target — margin too small for a reliable re-encode; "
                f"keeping {final_size_mb:.2f} MB output."
            )})
        else:
            # File is too large — retry once or twice with a proportional cut
            _publish(self.request.id, {"type": "log", "message": f"⚠️ File is {size_overage_percent:.1f}% over target ({final_size_mb:.2f} MB vs {progress_target_mb:.2f} MB)"})
            _publish(self.request.id, {"type": "log", "message": f"🔄 Retry attempt {retry_attempt + 1}/{max_retries} with optimized bitrate..."})
            _publish(self.request.id, {"type": "retry", "message": f"File too large ({final_size_mb:.2f} MB), re-encoding with optimized bitrate (attempt {retry_attempt + 1}/{max_retries})", "overage_percent": round(size_overage_percent, 1)})

            _publish(self.request.id, {"type": "log", "message": (
                f"Adjusted video bitrate: {int(video_kbps)} → {adjusted_video_kbps} kbps "
                f"(size ratio {size_ratio:.3f}×, −{100 * (1 - adjusted_video_kbps / max(video_kbps, 1e-9)):.1f}%)"
            )})
            
            # Keep a reversible backup while the retry runs.  If FFmpeg or a
            # driver fails during the second encode, returning success with a
            # truncated/missing output is worse than keeping the verified
            # oversized file the first pass produced.
            retry_backup_path: str | None = None
            retry_staging_path: str | None = None
            try:
                retry_backup_path = f"{output_path}.oversized.{uuid.uuid4().hex}.bak"
                os.replace(output_path, retry_backup_path)
                _publish(self.request.id, {"type": "log", "message": "Temporarily moved oversized output while retrying"})
            except Exception as e:
                retry_backup_path = None
                # Keep the verified first-pass output in place when it cannot be
                # moved.  Encode the optional retry to a separate path so a
                # failed retry cannot replace or truncate the good output.
                retry_staging_path = f"{output_path}.retry.{uuid.uuid4().hex}{Path(output_path).suffix}"
                _publish(self.request.id, {"type": "log", "message": f"Could not move oversized output; retrying to a staging file: {e}"})
            
            # Reset progress for retry
            _publish(self.request.id, {"type": "progress", "progress": 1.0, "phase": "encoding"})
            try:
                self.update_state(state="PROGRESS", meta={"progress": 1.0, "phase": "encoding"})
            except Exception:
                pass
            
            # Re-run the last command that actually produced the verified file.
            # A decode retry or CPU fallback may have replaced the original
            # command; rebuilding from the stale initial command silently
            # reintroduced the failed hardware path on oversized outputs.
            retry_base = last_successful_cmd or cmd
            retry_cmd = replace_bitrate_args(retry_base, adjusted_video_kbps)
            if retry_staging_path:
                retry_cmd[-1] = retry_staging_path
            
            _publish(self.request.id, {"type": "log", "message": f"Retry FFmpeg command: {' '.join(retry_cmd[:10])}..."})
            
            # Run the retry encode
            last_progress = 0.0
            stderr_lines = []
            rc, was_cancelled = run_ffmpeg_and_stream(retry_cmd)
            
            if was_cancelled:
                if retry_staging_path and os.path.exists(retry_staging_path):
                    try:
                        os.remove(retry_staging_path)
                    except OSError:
                        logger.warning("Could not remove canceled retry staging file: %s", retry_staging_path)
                if retry_backup_path and os.path.exists(retry_backup_path):
                    try:
                        if os.path.exists(output_path):
                            os.remove(output_path)
                        os.replace(retry_backup_path, output_path)
                    except Exception:
                        logger.warning("Could not restore oversized output after canceled retry: %s", output_path)
                _publish(self.request.id, {"type": "canceled"})
                msg = "Job canceled during retry"
                _publish(self.request.id, {"type": "error", "message": msg})
                raise RuntimeError(msg)

            retry_output_path = retry_staging_path or output_path
            retry_output_valid = False
            try:
                retry_output_valid = os.path.exists(retry_output_path) and os.path.getsize(retry_output_path) > 0
            except OSError:
                retry_output_valid = False
            if rc != 0 or not retry_output_valid:
                if retry_staging_path and os.path.exists(retry_staging_path):
                    try:
                        os.remove(retry_staging_path)
                    except OSError:
                        logger.warning("Could not remove failed retry staging file: %s", retry_staging_path)
                if retry_backup_path and os.path.exists(retry_backup_path):
                    try:
                        if os.path.exists(output_path):
                            os.remove(output_path)
                        os.replace(retry_backup_path, output_path)
                        final_size = os.path.getsize(output_path)
                        final_size_mb = round(final_size / (1024 * 1024), 2)
                    except Exception:
                        logger.warning("Could not restore oversized output after failed retry: %s", output_path)
                _publish(self.request.id, {"type": "error", "message": f"Retry encode failed with return code {rc}. Keeping the verified first-pass output."})
                # Do not fail the job solely because the optional size retry failed.
            else:
                if retry_staging_path:
                    try:
                        os.replace(retry_staging_path, output_path)
                    except Exception:
                        logger.exception("Could not promote successful retry staging file: %s", retry_staging_path)
                        _publish(self.request.id, {"type": "error", "message": "Retry output could not be promoted; keeping the verified first-pass output."})
                        try:
                            if os.path.exists(retry_staging_path):
                                os.remove(retry_staging_path)
                        except OSError:
                            pass
                        retry_output_valid = False
                if not retry_output_valid:
                    # The original output remains valid when staging promotion
                    # fails; leave it in place and skip the optional retry result.
                    pass
                else:
                    last_successful_cmd = retry_cmd.copy()
                    if retry_backup_path:
                        try:
                            os.remove(retry_backup_path)
                        except OSError:
                            logger.warning("Could not remove retry backup: %s", retry_backup_path)
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
        # This is the encoder that produced the final output, which may be a
        # CPU encoder after a controlled hardware fallback.
        "encoder": actual_encoder,
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
            # Import here to avoid circular dependency (``sys`` is module-global; do not re-import
            # inside ``compress_video`` or nested functions break: NameError on ``sys.platform``).
            import importlib
            app_root = os.getenv("BACKEND_APP_ROOT", "/app")
            sys.path.insert(0, app_root)
            try:
                hm = importlib.import_module('backend.history_manager')
            except ModuleNotFoundError:
                # The source tree calls the package ``app`` while Docker
                # copies it to ``backend``.  The local desktop build keeps
                # the source package name, so use it as a safe fallback.
                hm = importlib.import_module('app.history_manager')
            
            # Get original file size
            original_size = os.path.getsize(input_path)
            original_size_mb = original_size / (1024*1024)
            
            filename = _history_filename(job_id, input_path)
            
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
                output_filename=Path(output_path).name,
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
    logger.info(
        "compress_video DONE task_id=%s encoder=%s final=%sMB target=%sMB elapsed=%.1fs",
        self.request.id, actual_encoder, final_size_mb, target_size_mb,
        max(time.time() - start_ts, 0),
    )
    _publish(self.request.id, {"type": "done", "stats": stats})
    return stats
