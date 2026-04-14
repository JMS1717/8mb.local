"""Hardware acceleration detection and codec mapping.

Supported hardware: NVIDIA NVENC only. All other GPU vendors fall back to CPU.

Each encoder is tested by actually encoding at least 1 frame (not just checking
``ffmpeg -encoders``). This prevents false positives from encoders that are
merely *listed* by FFmpeg but cannot run on the current GPU.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    AV1_NVENC, CODEC_PRIORITY, CPU_ENCODERS, CPU_FALLBACK, HW_ENCODERS,
    H264_NVENC, HEVC_NVENC,
    LIBAOM_AV1, LIBSVTAV1, LIBX264, LIBX265,
)

logger = logging.getLogger(__name__)

_HW_CACHE: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Encoder initialization tests
# ---------------------------------------------------------------------------

def test_encoder(encoder_name: str) -> bool:
    """Test if an encoder can actually initialize and encode on current hardware."""
    if "nvenc" in encoder_name:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1:r=1",
            "-c:v", encoder_name,
            "-frames:v", "1",
            "-f", "null", "-",
        ]
    else:
        return _encoder_in_list(encoder_name)

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        success = result.returncode == 0
        if success:
            logger.info(f"Encoder {encoder_name} passed initialization test")
        else:
            stderr = result.stderr.decode(errors="replace").strip()
            logger.warning(
                f"Warning: {encoder_name} failed initialization test: {stderr[:200]}"
            )
        return success
    except subprocess.TimeoutExpired:
        logger.warning(f"Encoder {encoder_name} timed out during initialization test")
        return False
    except Exception as e:
        logger.warning(f"Encoder {encoder_name} test error: {e}")
        return False


def _encoder_in_list(encoder_name: str) -> bool:
    """Check if encoder appears in ``ffmpeg -encoders`` output."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, timeout=10,
        )
        return encoder_name in result.stdout.decode(errors="replace")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def detect_hw_accel() -> Dict[str, Any]:
    """Detect available hardware acceleration by testing each encoder.

    Returns a dict with:
    - ``type``: ``"nvidia"`` / ``"cpu"``
    - ``available_encoders``: ``{"h264": "<best_encoder>", ...}``
    - ``tested_encoders``: ``{"h264_nvenc": True/False, ...}``
    """
    result: Dict[str, Any] = {
        "type": "cpu",
        "available_encoders": {},
        "decode_method": None,
        "upload_method": None,
        "tested_encoders": {},
    }

    has_nvidia = _check_nvidia()

    candidates_to_test: List[str] = []
    if has_nvidia:
        candidates_to_test.extend([H264_NVENC, HEVC_NVENC, AV1_NVENC])

    tested: Dict[str, bool] = {}
    for enc in candidates_to_test:
        passed = test_encoder(enc)
        tested[enc] = passed
    result["tested_encoders"] = tested

    best_type = "cpu"
    for family, priority_list in CODEC_PRIORITY.items():
        for enc in priority_list:
            if enc in CPU_ENCODERS:
                result["available_encoders"][family] = enc
                break
            if tested.get(enc):
                result["available_encoders"][family] = enc
                if best_type == "cpu" and "nvenc" in enc:
                    best_type = "nvidia"
                break

    result["type"] = best_type
    if best_type == "nvidia":
        result["decode_method"] = "cuda"

    return result


def _check_nvidia() -> bool:
    """Check if NVIDIA GPU is available via nvidia-smi."""
    try:
        q = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
        if q.returncode == 0:
            return True
        lres = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=2,
        )
        if lres.returncode == 0 and (lres.stdout or "").strip():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        hwa = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True, text=True, timeout=2,
        )
        if "cuda" in hwa.stdout.lower():
            if (
                os.path.exists("/dev/nvidiactl")
                or os.path.exists("/dev/nvidia0")
                or os.path.exists("/dev/dxg")
            ):
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return False


# ---------------------------------------------------------------------------
# Codec → HW encoder mapping
# ---------------------------------------------------------------------------

def map_codec_to_hw(
    requested_codec: str,
    hw_info: Dict[str, Any],
) -> Tuple[str, list[str], list[str]]:
    """Map user-requested codec to appropriate hardware encoder.

    Returns ``(encoder_name, extra_flags, init_hw_flags)``.
    """
    # CPU encoders -- honor directly
    if requested_codec in (LIBX264, LIBX265, LIBSVTAV1, LIBAOM_AV1):
        encoder = requested_codec if requested_codec != LIBSVTAV1 else LIBAOM_AV1
        flags: list[str] = []
        if encoder == LIBX264:
            flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
        elif encoder == LIBX265:
            flags = ["-pix_fmt", "yuv420p"]
        return encoder, flags, []

    # Explicit NVENC selection
    if requested_codec in HW_ENCODERS:
        encoder = requested_codec
        flags = ["-pix_fmt", "yuv420p"]
        if "h264" in encoder:
            flags += ["-profile:v", "high"]
        elif "hevc" in encoder:
            flags += ["-profile:v", "main"]
        return encoder, flags, []

    # Legacy / bare codec name fallback
    if "h264" in requested_codec:
        base = "h264"
    elif "hevc" in requested_codec or "h265" in requested_codec:
        base = "hevc"
    elif "av1" in requested_codec:
        base = "av1"
    else:
        base = "h264"

    encoder = hw_info.get("available_encoders", {}).get(base, LIBX264)
    flags = []

    if encoder.endswith("_nvenc"):
        flags = ["-pix_fmt", "yuv420p"]
        if base == "h264":
            flags += ["-profile:v", "high"]
        elif base == "hevc":
            flags += ["-profile:v", "main"]
    elif encoder == LIBX264:
        flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
    elif encoder == LIBX265:
        flags = ["-pix_fmt", "yuv420p"]

    return encoder, flags, []


# ---------------------------------------------------------------------------
# Cached accessor
# ---------------------------------------------------------------------------

def get_hw_info() -> Dict[str, Any]:
    """Get cached hardware info (computed once per process)."""
    global _HW_CACHE
    if _HW_CACHE is None:
        _HW_CACHE = detect_hw_accel()
    return _HW_CACHE


# ---------------------------------------------------------------------------
# Best-codec chooser
# ---------------------------------------------------------------------------

def choose_best_codec(
    hw_info: Dict[str, Any],
    encoder_test_cache: Optional[Dict[str, bool]] = None,
    redis_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Choose the preferred codec/encoder using priority AV1 > HEVC > H264."""
    hw_priority = ["av1", "hevc", "h264"]

    def _encoder_passed(
        base_codec: str, encoder_name: str, init_flags: list[str],
    ) -> Optional[bool]:
        if encoder_test_cache is not None:
            cache_key = f"{encoder_name}:{':'.join(init_flags)}"
            if cache_key in encoder_test_cache:
                return bool(encoder_test_cache[cache_key])
            for k, v in encoder_test_cache.items():
                if k.startswith(f"{encoder_name}:") or k == encoder_name:
                    return bool(v)

        try:
            from redis import Redis as SyncRedis
            redis_client = SyncRedis.from_url(
                redis_url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                decode_responses=True,
            )
            for cand in (encoder_name, base_codec):
                try:
                    flag = redis_client.get(f"encoder_test:{cand}")
                    if flag is not None:
                        return str(flag) == "1"
                except Exception:
                    continue
        except Exception:
            return None
        return None

    candidates: list[tuple[str, str, list[str], list[str], bool]] = []

    for base, enc in (hw_info.get("available_encoders", {}) or {}).items():
        try:
            encoder_name, flags, init_flags = map_codec_to_hw(base, hw_info)
        except Exception:
            encoder_name, flags, init_flags = enc, [], []
        is_hw = not encoder_name.startswith("lib")
        candidates.append((base, encoder_name, flags, init_flags, is_hw))

    if encoder_test_cache is not None:
        for cache_key in encoder_test_cache.keys():
            try:
                enc_name = cache_key.split(":", 1)[0]
                if "av1" in enc_name:
                    base = "av1"
                elif "hevc" in enc_name or "h265" in enc_name:
                    base = "hevc"
                elif "h264" in enc_name:
                    base = "h264"
                else:
                    base = enc_name
                if not any(c[1] == enc_name for c in candidates):
                    candidates.append(
                        (base, enc_name, [], [], not enc_name.startswith("lib"))
                    )
            except Exception:
                continue

    for base in hw_priority:
        for c_base, c_enc, c_flags, c_init, c_is_hw in candidates:
            if c_base != base:
                continue
            passed = _encoder_passed(c_base, c_enc, c_init)
            if passed is True:
                return {
                    "base": c_base, "encoder": c_enc, "hardware": c_is_hw,
                    "flags": c_flags, "init_flags": c_init,
                }

        for c_base, c_enc, c_flags, c_init, c_is_hw in candidates:
            if c_base != base or not c_is_hw:
                continue
            passed = _encoder_passed(c_base, c_enc, c_init)
            if passed is None:
                return {
                    "base": c_base, "encoder": c_enc, "hardware": True,
                    "flags": c_flags, "init_flags": c_init,
                }

        for c_base, c_enc, c_flags, c_init, c_is_hw in candidates:
            if c_base != base or c_is_hw:
                continue
            return {
                "base": c_base, "encoder": c_enc, "hardware": False,
                "flags": c_flags, "init_flags": c_init,
            }

    try:
        encoder_name, flags, init_flags = map_codec_to_hw("h264", hw_info)
    except Exception:
        encoder_name = LIBX264
        flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
        init_flags = []
    return {
        "base": "h264", "encoder": encoder_name, "hardware": False,
        "flags": flags, "init_flags": init_flags,
    }
