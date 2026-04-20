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
# SVT-AV1 is preferred on CPU because it is 10×–50× faster than libaom-av1 at
# comparable quality and has better rate-control for strict target-size / ABR
# encoding (which is the core product use case).  libaom-av1 is kept as a
# final fallback for builds that lack libsvtav1.
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
CPU_PRESET_MAP: dict[str, str] = {
    "p1": "ultrafast", "p2": "superfast", "p3": "veryfast",
    "p4": "faster", "p5": "fast", "p6": "medium", "p7": "slow",
    "ultrafast": "ultrafast", "superfast": "superfast",
    "veryfast": "veryfast", "faster": "faster", "fast": "fast",
    "medium": "medium", "slow": "slow", "slower": "slower",
    "veryslow": "veryslow",
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
