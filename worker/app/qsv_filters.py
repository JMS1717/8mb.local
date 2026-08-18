"""Platform-specific QSV software-frame filter selection."""
from __future__ import annotations

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


def vaapi_input_filter(pixel_format: str = "nv12") -> str:
    """Return the portable software-frame upload filter for VAAPI."""
    return f"format={pixel_format}|vaapi,hwupload"


def qsv_probe_size(platform: str | None = None) -> str:
    """Exercise a realistic vertical surface in native Windows probes."""
    return "1080x1920" if (platform or sys.platform) == "win32" else "256x256"
