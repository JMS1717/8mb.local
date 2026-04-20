"""Shared constants for the 8mb.local worker.

All codec name strings, fallback mappings, default values, and Redis channel
patterns live here so that they can be imported consistently from any module
without circular dependencies.

Supported hardware acceleration: NVIDIA NVENC only.
Systems without an NVIDIA GPU fall back to CPU software encoders.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Encoder name strings
# ---------------------------------------------------------------------------
# NVIDIA NVENC
H264_NVENC = "h264_nvenc"
HEVC_NVENC = "hevc_nvenc"
AV1_NVENC = "av1_nvenc"

# CPU / software encoders
LIBX264 = "libx264"
LIBX265 = "libx265"
LIBSVTAV1 = "libsvtav1"
LIBAOM_AV1 = "libaom-av1"

# ---------------------------------------------------------------------------
# Encoder priority order per codec family (NVENC → CPU)
# ---------------------------------------------------------------------------
H264_PRIORITY: list[str] = [H264_NVENC, LIBX264]
HEVC_PRIORITY: list[str] = [HEVC_NVENC, LIBX265]
AV1_PRIORITY: list[str] = [AV1_NVENC, LIBSVTAV1, LIBAOM_AV1]

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
}

# All known hardware encoder names (for quick membership checks)
HW_ENCODERS: frozenset[str] = frozenset(CPU_FALLBACK.keys())

# All known CPU encoder names
CPU_ENCODERS: frozenset[str] = frozenset({LIBX264, LIBX265, LIBAOM_AV1, LIBSVTAV1})

# ---------------------------------------------------------------------------
# Preset mapping
# ---------------------------------------------------------------------------
# x264 / x265: NVENC p-level → named CPU preset
CPU_PRESET_MAP: dict[str, str] = {
    "p1": "ultrafast", "p2": "superfast", "p3": "veryfast",
    "p4": "faster", "p5": "fast", "p6": "medium", "p7": "slow",
    "ultrafast": "ultrafast", "superfast": "superfast",
    "veryfast": "veryfast", "faster": "faster", "fast": "fast",
    "medium": "medium", "slow": "slow", "slower": "slower",
    "veryslow": "veryslow",
}

# SVT-AV1: numeric preset 0-13 (lower = slower / better quality)
SVT_PRESET_MAP: dict[str, str] = {
    "p1": "12", "p2": "10", "p3": "8", "p4": "6",
    "p5": "5", "p6": "4", "p7": "2",
    "ultrafast": "12", "superfast": "10", "veryfast": "8",
    "faster": "6", "fast": "5", "medium": "4",
    "slow": "2", "slower": "1", "veryslow": "0",
}

# libaom-av1: -cpu-used 0-8 (lower = slower / better quality, 0 = reference)
LIBAOM_PRESET_MAP: dict[str, str] = {
    "p1": "8", "p2": "6", "p3": "5", "p4": "4",
    "p5": "3", "p6": "2", "p7": "1",
    "ultrafast": "8", "superfast": "6", "veryfast": "5",
    "faster": "4", "fast": "3", "medium": "2",
    "slow": "1", "slower": "0", "veryslow": "0",
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
