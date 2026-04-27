"""Shared constants for the 8mb.local worker.

All codec name strings, fallback mappings, default values, and Redis channel
patterns live here so that they can be imported consistently from any module
without circular dependencies.

Supported hardware acceleration: NVIDIA NVENC, Intel QSV (via VAAPI), VAAPI.
Systems without hardware acceleration fall back to CPU software encoders.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Encoder name strings
# ---------------------------------------------------------------------------
# NVIDIA NVENC
H264_NVENC = "h264_nvenc"
HEVC_NVENC = "hevc_nvenc"
AV1_NVENC = "av1_nvenc"

# Intel QSV (via VAAPI backend)
H264_QSV = "h264_qsv"
HEVC_QSV = "hevc_qsv"
AV1_QSV = "av1_qsv"

# VAAPI (Intel iGPU, AMD)
H264_VAAPI = "h264_vaapi"
HEVC_VAAPI = "hevc_vaapi"
AV1_VAAPI = "av1_vaapi"

# CPU / software encoders
LIBX264 = "libx264"
LIBX265 = "libx265"
LIBSVTAV1 = "libsvtav1"
LIBAOM_AV1 = "libaom-av1"

# ---------------------------------------------------------------------------
# Encoder priority order per codec family (NVENC → QSV → VAAPI → CPU)
# ---------------------------------------------------------------------------
H264_PRIORITY: list[str] = [H264_NVENC, H264_QSV, H264_VAAPI, LIBX264]
HEVC_PRIORITY: list[str] = [HEVC_NVENC, HEVC_QSV, HEVC_VAAPI, LIBX265]
AV1_PRIORITY: list[str] = [AV1_NVENC, AV1_QSV, AV1_VAAPI, LIBSVTAV1]

CODEC_PRIORITY: dict[str, list[str]] = {
    "h264": H264_PRIORITY,
    "hevc": HEVC_PRIORITY,
    "av1": AV1_PRIORITY,
}

# ---------------------------------------------------------------------------
# CPU fallback mapping: HW encoder → CPU fallback encoder
# ---------------------------------------------------------------------------
CPU_FALLBACK: dict[str, str] = {
    H264_NVENC: LIBX264,
    HEVC_NVENC: LIBX265,
    AV1_NVENC: LIBSVTAV1,
    H264_QSV: LIBX264,
    HEVC_QSV: LIBX265,
    AV1_QSV: LIBSVTAV1,
    H264_VAAPI: LIBX264,
    HEVC_VAAPI: LIBX265,
    AV1_VAAPI: LIBSVTAV1,
}

# All known hardware encoder names (for quick membership checks)
HW_ENCODERS: frozenset[str] = frozenset(CPU_FALLBACK.keys())

# All known CPU encoder names
CPU_ENCODERS: frozenset[str] = frozenset({LIBX264, LIBX265, LIBAOM_AV1, LIBSVTAV1})

# QSV and VAAPI encoder sets for type checking
QSV_ENCODERS: frozenset[str] = frozenset({H264_QSV, HEVC_QSV, AV1_QSV})
VAAPI_ENCODERS: frozenset[str] = frozenset({H264_VAAPI, HEVC_VAAPI, AV1_VAAPI})

# ---------------------------------------------------------------------------
# Preset mapping
# ---------------------------------------------------------------------------
CPU_PRESET_MAP: dict[str, str] = {
    "p1": "ultrafast", "p2": "superfast", "p3": "veryfast",
    "p4": "faster", "p5": "fast", "p6": "medium", "p7": "slow",
    "ultrafast": "ultrafast", "superfast": "superfast",
    "veryfast": "veryfast", "faster": "faster", "fast": "fast",
    "medium": "medium", "slow": "slow", "slower": "slower",
    "veryslow": "veryslow",
}

# QSV preset mapping (p1=veryfast ... p7=veryslow)
QSV_PRESET_MAP: dict[str, str] = {
    "p1": "veryfast", "p2": "faster", "p3": "fast",
    "p4": "medium", "p5": "slow", "p6": "slower", "p7": "veryslow",
    "ultrafast": "veryfast", "superfast": "veryfast", "veryfast": "veryfast",
    "faster": "faster", "fast": "fast", "medium": "medium",
    "slow": "slow", "slower": "slower", "veryslow": "veryslow",
}

# ---------------------------------------------------------------------------
# Audio defaults
# ---------------------------------------------------------------------------
DEFAULT_AUDIO_BITRATE_KBPS: int = 128

# ---------------------------------------------------------------------------
# Retry thresholds
# ---------------------------------------------------------------------------
SIZE_OVERAGE_THRESHOLD_PERCENT: float = 2.0
MAX_RETRY_COUNT: int = 2

# ---------------------------------------------------------------------------
# Redis channel patterns
# ---------------------------------------------------------------------------
PROGRESS_CHANNEL_PREFIX = "progress:"
CANCEL_KEY_PREFIX = "cancel:"
JOB_KEY_PREFIX = "job:"
READY_KEY_PREFIX = "ready:"
ACTIVE_JOBS_SET = "jobs:active"
ENCODER_TEST_KEY_PREFIX = "encoder_test:"
ENCODER_TEST_JSON_PREFIX = "encoder_test_json:"
ENCODER_TEST_DECODE_JSON_PREFIX = "encoder_test_decode_json:"
