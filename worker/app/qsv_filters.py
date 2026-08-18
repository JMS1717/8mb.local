"""Platform-specific QSV decode, VPP, and software-upload helpers."""
from __future__ import annotations

import math
import sys


def source_is_10bit(info: dict | None) -> bool:
    """Return whether the probed source needs a 10-bit hardware surface."""
    if not info:
        return False
    pixel_format = str(info.get("video_pix_fmt") or "").lower()
    if any(token in pixel_format for token in ("10", "12", "14", "p010", "p012", "p016")):
        return True
    try:
        return int(float(info.get("video_bits_per_raw_sample") or 0)) >= 10
    except (TypeError, ValueError):
        return False


def hardware_input_pixel_format(encoder: str, info: dict | None) -> str:
    """Choose the software frame format for the selected hardware encoder.

    Intel H.264 hardware encoders remain 8-bit. HEVC QSV/VAAPI can accept
    P010 surfaces on the tested Linux Intel stack, so preserve 10-bit input
    only for that specific capability-aware combination.
    """
    if source_is_10bit(info) and str(encoder).lower() in {"hevc_qsv", "hevc_vaapi"}:
        return "p010le"
    return "nv12"


def hardware_profile_flags(encoder: str, pixel_format: str) -> list[str]:
    """Return explicit profile flags required for a 10-bit HEVC surface."""
    if pixel_format == "p010le" and str(encoder).lower() in {"hevc_qsv", "hevc_vaapi"}:
        return ["-profile:v", "main10"]
    return []


def source_color_metadata_args(info: dict | None) -> list[str]:
    """Preserve known source color metadata on the encoded video stream."""
    if not info:
        return []
    args: list[str] = []
    for option, key in (
        ("-color_range", "video_color_range"),
        ("-colorspace", "video_color_space"),
        ("-color_primaries", "video_color_primaries"),
        ("-color_trc", "video_color_transfer"),
    ):
        value = str(info.get(key) or "").strip()
        if value and value.lower() not in {"unknown", "n/a", "none"}:
            args.extend([option, value])
    return args


def qsv_input_filter(platform: str | None = None, pixel_format: str = "nv12") -> str:
    """Return the safe software-decode-to-QSV filter for this platform.

    Linux oneVPL requires an explicitly sized hardware frame pool. Native
    Windows QSV accepts NV12 software frames and uploads them internally;
    forcing a D3D11 hwupload can fail on real vertical/rotated AV1 surfaces
    even though a small synthetic initialization probe succeeds.
    """
    current = platform or sys.platform
    if current == "win32":
        return f"format={pixel_format}"
    return f"format={pixel_format},hwupload=extra_hw_frames=64"


def qsv_hardware_decode_supported(
    platform: str | None,
    source_codec: str | None,
    encoder: str,
    rotation_degrees: int | float = 0,
) -> bool:
    """Return whether the optimized Linux QSV decode path is safe to try.

    The tested oneVPL path keeps H.264/HEVC frames on QSV surfaces.  It is
    intentionally limited to Linux Intel QSV jobs and upright sources.  The
    existing software path remains necessary for AV1, rotated phone media,
    Windows native QSV, and any runtime that rejects the probe at execution.
    """
    current = platform or sys.platform
    codec = str(source_codec or "").lower()
    try:
        rotated = int(float(rotation_degrees or 0)) % 360 != 0
    except (TypeError, ValueError):
        rotated = True
    return (
        current != "win32"
        and codec in {"h264", "hevc"}
        and str(encoder).lower() in {"h264_qsv", "hevc_qsv"}
        and not rotated
    )


def qsv_vpp_filter(
    pixel_format: str = "nv12",
    width: int | None = None,
    height: int | None = None,
    frame_rate: float | None = None,
) -> str:
    """Build a QSV-native VPP filter for scaling and/or frame-rate capping."""
    options: list[str] = []
    if width and height:
        options.extend([f"w={int(width)}", f"h={int(height)}"])
    if frame_rate is not None and float(frame_rate) > 0:
        options.append(f"framerate={float(frame_rate):g}")
    options.append(f"format={pixel_format}")
    return "vpp_qsv=" + ":".join(options)


def qsv_scaled_dimensions(
    source_width: int | float | None,
    source_height: int | float | None,
    max_width: int | float | None = None,
    max_height: int | float | None = None,
) -> tuple[int, int] | None:
    """Calculate even dimensions matching the existing aspect-ratio scaling."""
    try:
        width = float(source_width or 0)
        height = float(source_height or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    bounds = [1.0]
    if max_width and float(max_width) > 0:
        bounds.append(float(max_width) / width)
    if max_height and float(max_height) > 0:
        bounds.append(float(max_height) / height)
    scale = min(bounds)
    if scale >= 1.0:
        return None
    scaled_width = max(2, int(math.floor((width * scale) / 2.0) * 2))
    scaled_height = max(2, int(math.floor((height * scale) / 2.0) * 2))
    return scaled_width, scaled_height


def vaapi_input_filter(pixel_format: str = "nv12") -> str:
    """Return the portable software-frame upload filter for VAAPI."""
    return f"format={pixel_format}|vaapi,hwupload"


def qsv_probe_size(platform: str | None = None) -> str:
    """Exercise a realistic vertical surface in native Windows probes."""
    return "1080x1920" if (platform or sys.platform) == "win32" else "256x256"
