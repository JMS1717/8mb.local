"""Encoder-aware FFmpeg command builder.

Constructs FFmpeg commands with the correct flags for NVENC, QSV, VAAPI, and CPU encoders.
"""
from __future__ import annotations

import logging
from typing import Optional

from .constants import CPU_PRESET_MAP, QSV_PRESET_MAP, QSV_ENCODERS, VAAPI_ENCODERS

logger = logging.getLogger(__name__)


def build_video_encode_command(
    encoder: str,
    input_path: str,
    output_path: str,
    video_bitrate_kbps: int,
    audio_codec: Optional[str],
    audio_bitrate_kbps: int,
    preset: Optional[str] = None,
    tune: Optional[str] = None,
    scale_width: Optional[int] = None,
    scale_height: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    duration: Optional[str] = None,
    extra_flags: Optional[list[str]] = None,
    init_hw_flags: Optional[list[str]] = None,
    v_flags: Optional[list[str]] = None,
    fast_mp4_finalize: bool = False,
    force_hw_decode: bool = False,
    input_codec: Optional[str] = None,
) -> list[str]:
    """Build a complete FFmpeg command with encoder-specific flags."""
    cmd: list[str] = ["ffmpeg", "-hide_banner", "-y"]

    if start_time is not None:
        cmd += ["-ss", str(start_time)]

    if init_hw_flags:
        cmd += init_hw_flags

    cmd += ["-i", input_path]

    if duration is not None:
        cmd += ["-t", str(duration)]
    elif end_time is not None and start_time is None:
        cmd += ["-to", str(end_time)]

    vf_filters = _build_video_filters(encoder, scale_width, scale_height)

    caller_vf = None
    remaining_v_flags: list[str] = []
    if v_flags:
        i = 0
        while i < len(v_flags):
            if v_flags[i] == "-vf" and i + 1 < len(v_flags):
                caller_vf = v_flags[i + 1]
                i += 2
            else:
                remaining_v_flags.append(v_flags[i])
                i += 1

    if caller_vf and vf_filters:
        combined = f"{caller_vf},{','.join(vf_filters)}"
        cmd += ["-vf", combined]
    elif caller_vf:
        cmd += ["-vf", caller_vf]
    elif vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]

    if remaining_v_flags:
        cmd += remaining_v_flags

    cmd += ["-c:v", encoder]

    maxrate = int(video_bitrate_kbps * 1.2)
    bufsize = video_bitrate_kbps * 2
    cmd += [
        "-b:v", f"{video_bitrate_kbps}k",
        "-maxrate", f"{maxrate}k",
        "-bufsize", f"{bufsize}k",
    ]

    cmd += _build_encoder_quality_flags(encoder, preset, tune)

    if audio_codec is None:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", audio_codec, "-b:a", f"{audio_bitrate_kbps}k"]

    if output_path.lower().endswith(".mp4"):
        if fast_mp4_finalize:
            cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
        else:
            cmd += ["-movflags", "+faststart"]

    if extra_flags:
        cmd += extra_flags

    cmd += ["-progress", "pipe:2", output_path]

    return cmd


def _build_video_filters(
    encoder: str,
    scale_width: Optional[int],
    scale_height: Optional[int],
) -> list[str]:
    """Build the ``-vf`` filter chain for encoder-specific requirements.

    QSV:   scale_qsv (HW scaling) + hwmap=derive_device=qsv,format=qsv
    VAAPI: scale (CPU) + format=nv12|vaapi + hwupload
    NVENC/CPU: standard scale filter
    """
    filters: list[str] = []

    if encoder in QSV_ENCODERS:
        # QSV: Use hardware scaling
        if scale_width and scale_height:
            filters.append(f"scale_qsv={scale_width}:{scale_height}")
        elif scale_width:
            filters.append(f"scale_qsv={scale_width}:-1")
        elif scale_height:
            filters.append(f"scale_qsv=-1:{scale_height}")
        # Final: Map to QSV format (MUST be last filter)
        filters.append("hwmap=derive_device=qsv,format=qsv")
    elif encoder in VAAPI_ENCODERS:
        # VAAPI: CPU scaling + VAAPI upload
        if scale_width and scale_height:
            filters.append(f"scale={scale_width}:{scale_height}")
        elif scale_width:
            filters.append(f"scale={scale_width}:-2")
        elif scale_height:
            filters.append(f"scale=-2:{scale_height}")
        # Format for VAAPI hardware upload
        filters.append("format=nv12,hwupload")
    else:
        # NVENC / CPU: standard software scaling
        if scale_width and scale_height:
            filters.append(f"scale={scale_width}:{scale_height}")
        elif scale_width:
            filters.append(f"scale={scale_width}:-2")
        elif scale_height:
            filters.append(f"scale=-2:{scale_height}")

    return filters


def _build_encoder_quality_flags(
    encoder: str,
    preset: Optional[str],
    tune: Optional[str],
) -> list[str]:
    """Return encoder-specific quality/preset flags.

    QSV:   -global_quality (ICQ mode, best quality) + -look_ahead 1
    VAAPI: -rc_mode VBR or CQP + -qp (if quality specified)
    NVENC: -preset + -tune
    CPU:   -preset
    """
    flags: list[str] = []

    if encoder in QSV_ENCODERS:
        # QSV: Always use lookahead for better quality
        flags += ["-look_ahead", "1"]
        # Note: If quality/crf is needed, use -global_quality in higher-level code
        if preset:
            mapped = QSV_PRESET_MAP.get(preset, "medium")
            flags += ["-preset", mapped]
    elif encoder in VAAPI_ENCODERS:
        # VAAPI: Use VBR by default (bitrate-based)
        flags += ["-rc_mode", "VBR"]
        # Note: For CQP mode, add -rc_mode CQP + -qp in higher-level code
        if preset:
            # VAAPI doesn't have standard presets, use compression_level
            compression_level = _vaapi_compression_level(preset)
            flags += ["-compression_level", str(compression_level)]
    elif "nvenc" in encoder:
        # NVENC: preset + tune
        if preset:
            flags += ["-preset", preset]
        if tune:
            flags += ["-tune", tune]
    else:
        # CPU encoders: preset mapping
        if preset:
            mapped = CPU_PRESET_MAP.get(preset, "medium")
            flags += ["-preset", mapped]

    return flags


def _vaapi_compression_level(preset: str) -> int:
    """Map preset string to VAAPI compression_level (0=fastest, 7=best quality)."""
    mapping = {
        "p1": 0, "p2": 1, "p3": 1, "p4": 2, "p5": 3, "p6": 4, "p7": 5,
        "ultrafast": 0, "superfast": 1, "veryfast": 1,
        "faster": 2, "fast": 3, "medium": 4,
        "slow": 5, "slower": 6, "veryslow": 7,
    }
    return mapping.get(preset, 4)
