"""Encoder-aware FFmpeg command builder.

Constructs FFmpeg commands with the correct flags for NVENC and CPU encoders.
"""
from __future__ import annotations

import logging
from typing import Optional

from .constants import CPU_PRESET_MAP

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
    """Build the ``-vf`` filter chain for NVENC/CPU encoders."""
    filters: list[str] = []

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
    """Return encoder-specific quality/preset flags."""
    flags: list[str] = []

    if "nvenc" in encoder:
        if preset:
            flags += ["-preset", preset]
        if tune:
            flags += ["-tune", tune]
    else:
        if preset:
            mapped = CPU_PRESET_MAP.get(preset, "medium")
            flags += ["-preset", mapped]

    return flags
