from __future__ import annotations

import json
import math
import os
import subprocess
from typing import Any, Optional

from shared.subprocess_utils import hidden_process_kwargs


def get_gpu_env() -> dict[str, str]:
    """
    Get environment with NVIDIA GPU variables and library paths for subprocess calls.
    """
    env = os.environ.copy()
    env['NVIDIA_VISIBLE_DEVICES'] = env.get('NVIDIA_VISIBLE_DEVICES', 'all')
    env['NVIDIA_DRIVER_CAPABILITIES'] = env.get('NVIDIA_DRIVER_CAPABILITIES', 'compute,video,utility')
    lib_paths = [
        '/usr/local/nvidia/lib64',
        '/usr/local/nvidia/lib',
        '/usr/local/cuda/lib64',
        '/usr/local/cuda/lib',
        '/usr/lib/wsl/lib',
        '/usr/lib/x86_64-linux-gnu',
    ]
    existing = env.get('LD_LIBRARY_PATH', '')
    add = ':'.join(p for p in lib_paths if p)
    env['LD_LIBRARY_PATH'] = (existing + (':' if existing and add else '') + add) if (existing or add) else ''
    return env


def parse_fps_fraction(val: Any) -> Optional[float]:
    """Parse ffprobe avg_frame_rate / r_frame_rate (e.g. '60/1', '30000/1001') to float fps."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ("0/0", "N/A"):
        return None
    if "/" in s:
        parts = s.split("/", 1)
        try:
            num, den = float(parts[0]), float(parts[1])
            if den == 0:
                return None
            return num / den
        except (TypeError, ValueError):
            return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_finite_float(value: Any) -> Optional[float]:
    """Parse ffprobe numeric fields while tolerating ``N/A`` and infinities."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _normalize_rotation_degrees(val: Any) -> Optional[int]:
    """Normalize a tag/matrix value to 0/90/180/270 (clockwise display rotation vs coded frame)."""
    if val is None:
        return None
    try:
        x = float(str(val).strip())
    except (TypeError, ValueError):
        return None
    r = int(round(x)) % 360
    if r < 0:
        r += 360
    # Snap near-multiples of 90 (some matrices use float-ish values)
    if r % 90 != 0:
        r = (int(round(r / 90.0)) * 90) % 360
    return r


def parse_stream_rotation_degrees(stream: dict, format_tags: Optional[dict] = None) -> int:
    """
    Return clockwise rotation in degrees (0, 90, 180, 270) indicated by metadata so that
    display orientation matches how players show the file (rotate tag / Display Matrix).
    """
    tags = stream.get("tags") or {}
    for key in ("rotate", "rotation"):
        if key in tags:
            r = _normalize_rotation_degrees(tags[key])
            if r is not None:
                return r
    # QuickTime / phone tags (e.g. com.apple.rotation, Android metadata)
    for key, val in tags.items():
        lk = str(key).lower()
        if lk in ("rotate", "rotation"):
            r = _normalize_rotation_degrees(val)
            if r is not None:
                return r
        if "rotate" in lk and val is not None:
            r = _normalize_rotation_degrees(val)
            if r is not None and r % 360 != 0:
                return r
    ft = format_tags or {}
    for key in ("rotate", "rotation"):
        if key in ft:
            r = _normalize_rotation_degrees(ft[key])
            if r is not None:
                return r
    # Display matrix rotation (ffprobe JSON); do not require side_data_type to contain "display matrix"
    # — selective -show_entries has omitted rotation for some phone HEVC MP4s while ffmpeg decodes it.
    for sd in stream.get("side_data_list") or []:
        if not isinstance(sd, dict) or "rotation" not in sd:
            continue
        r = _normalize_rotation_degrees(sd.get("rotation"))
        if r is not None and r % 360 != 0:
            return r
    return 0


def coded_to_display_dimensions(
    coded_w: Optional[int], coded_h: Optional[int], rotation_deg: int,
) -> tuple[Optional[int], Optional[int]]:
    """Swap width/height when a 90°/270° display rotation is indicated."""
    if not coded_w or not coded_h or coded_w <= 0 or coded_h <= 0:
        return coded_w, coded_h
    if rotation_deg % 180 == 90:
        return coded_h, coded_w
    return coded_w, coded_h


def infer_rotation_quicktime_landscape_storage(
    format_tags: Optional[dict],
    coded_w: Optional[int],
    coded_h: Optional[int],
) -> int:
    """
    Last-resort heuristic: iPhone/MOV often stores portrait as 1920×1080 with a QuickTime brand
    but rotation metadata is missing from a short ffprobe. If container looks QuickTime-ish and
    dimensions match common phone landscape-raster portrait storage, assume 90° display rotation.
    """
    if not format_tags or not coded_w or not coded_h:
        return 0
    if coded_w <= coded_h:
        return 0
    if (coded_w, coded_h) not in ((1920, 1080), (1280, 720)):
        return 0
    mb = str(format_tags.get("major_brand", "") or "").lower()
    cb = str(format_tags.get("compatible_brands", "") or "").lower()
    # qt / isom variants common for phone camera; avoid bare isom-only (too generic)
    if "qt" in mb or "qt" in cb or "apple" in mb or "apple" in cb:
        return 90
    return 0


def infer_rotation_from_display_aspect_ratio(
    coded_w: Optional[int],
    coded_h: Optional[int],
    dar_str: Optional[str],
) -> int:
    """
    When rotate metadata is missing, infer 90° if coded storage is landscape but DAR is portrait
    (or vice versa). Common for phone MP4/MOV where only the container DAR reflects intended display.
    """
    if not coded_w or not coded_h or not dar_str or ":" not in dar_str:
        return 0
    s = dar_str.strip().replace(" ", "")
    parts = s.split(":")
    if len(parts) != 2:
        return 0
    try:
        da, db = float(parts[0]), float(parts[1])
    except ValueError:
        return 0
    if da <= 0 or db <= 0:
        return 0
    coded_landscape = coded_w >= coded_h
    coded_portrait = coded_h > coded_w
    dar_portrait = db > da
    dar_landscape = da > db
    if coded_landscape and dar_portrait:
        return 90
    if coded_portrait and dar_landscape:
        return 90
    return 0


def transpose_filters_for_rotation_degrees(rotation_deg: int) -> list[str]:
    """
    FFmpeg transpose filters to apply raw coded frames so pixels match upright display.
    rotation_deg: clockwise display rotation indicated by metadata/heuristic (0, 90, 180, 270).
    Uses -autorotate 0 on input + this chain to avoid double-rotation from the decoder.
    """
    d = int(rotation_deg) % 360
    if d == 0:
        return []
    # transpose=1: 90° clockwise; transpose=2: 90° counter-clockwise (270° CW)
    if d == 90:
        return ["transpose=1"]
    if d == 270:
        return ["transpose=2"]
    if d == 180:
        return ["transpose=1", "transpose=1"]
    return []


def ffprobe_info(input_path: str, allow_audio_only: bool = False) -> dict:
    """Return media metadata, optionally accepting audio-only inputs.

    Normal video jobs still reject audio-only media. The explicit flag keeps
    that validation while allowing the audio extraction path to reuse the
    same probe and upload contract.
    """
    # Large probesize helps read moov/trak metadata for rotation on phone MP4/MOV (default probe can miss tags).
    # Full -show_streams (not selective -show_entries) so side_data_list includes Display Matrix rotation
    # for HEVC/Android MP4; selective entries have been observed to omit rotation while ffmpeg -i shows it.
    cmd = [
        "ffprobe", "-v", "error",
        "-probesize", "100M",
        "-analyzeduration", "20M",
        "-show_format",
        "-show_streams",
        "-of", "json",
        input_path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=get_gpu_env(), timeout=45,
            **hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffprobe timed out while analyzing the input") from exc
    if proc.returncode != 0:
        # Fallback for older ffprobe without side_data_list
        cmd_fb = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=index,codec_type,codec_name,bit_rate,width,height",
            "-of", "json",
            input_path,
        ]
        try:
            proc = subprocess.run(
                cmd_fb, capture_output=True, text=True, env=get_gpu_env(), timeout=30,
                **hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffprobe fallback timed out while analyzing the input") from exc
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ffprobe returned invalid JSON") from exc
        format_tags = ((data.get("format") or {}).get("tags") or {}) if isinstance(data, dict) else {}
    else:
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ffprobe returned invalid JSON") from exc
        format_tags = ((data.get("format") or {}).get("tags") or {}) if isinstance(data, dict) else {}
    if not isinstance(data, dict):
        raise RuntimeError("ffprobe returned an unexpected JSON shape")
    duration = _parse_finite_float((data.get("format") or {}).get("duration"))
    if duration is None or duration <= 0:
        raise RuntimeError("Input has no usable media duration")
    v_bitrate = None
    a_bitrate = None
    v_codec = None
    v_width = None
    v_height = None
    v_fps: Optional[float] = None
    v_pix_fmt: Optional[str] = None
    v_profile: Optional[str] = None
    v_color_space: Optional[str] = None
    v_color_transfer: Optional[str] = None
    v_color_primaries: Optional[str] = None
    v_color_range: Optional[str] = None
    v_bits_per_raw_sample: Optional[int] = None
    display_aspect_ratio: Optional[str] = None
    has_audio = False
    has_video = False
    rotation_degrees = 0
    video_seen = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            bitrate = _parse_finite_float(s.get("bit_rate"))
            if bitrate is not None and bitrate >= 0:
                v_bitrate = bitrate / 1000.0
            v_codec = s.get("codec_name")
            if not v_pix_fmt:
                v_pix_fmt = s.get("pix_fmt")
            if not v_profile:
                v_profile = s.get("profile")
            if not v_color_space:
                v_color_space = s.get("color_space")
            if not v_color_transfer:
                v_color_transfer = s.get("color_transfer")
            if not v_color_primaries:
                v_color_primaries = s.get("color_primaries")
            if not v_color_range:
                v_color_range = s.get("color_range")
            if v_bits_per_raw_sample is None:
                try:
                    parsed_bits = int(float(s.get("bits_per_raw_sample") or 0))
                    if parsed_bits > 0:
                        v_bits_per_raw_sample = parsed_bits
                except (TypeError, ValueError):
                    pass
            width = _parse_finite_float(s.get("width"))
            height = _parse_finite_float(s.get("height"))
            if width is not None and width > 0:
                v_width = int(width)
            if height is not None and height > 0:
                v_height = int(height)
        if s.get("codec_type") == "video":
            has_video = True
            # For some inputs, bit_rate may be missing on the stream; keep codec/size discovery regardless
            if s.get("codec_name") and not v_codec:
                v_codec = s.get("codec_name")
            width = _parse_finite_float(s.get("width"))
            height = _parse_finite_float(s.get("height"))
            if width is not None and width > 0 and not v_width:
                v_width = int(width)
            if height is not None and height > 0 and not v_height:
                v_height = int(height)
            if not video_seen:
                rotation_degrees = parse_stream_rotation_degrees(s, format_tags)
                dar = s.get("display_aspect_ratio")
                if isinstance(dar, str) and dar.strip():
                    display_aspect_ratio = dar.strip()
                v_fps = parse_fps_fraction(s.get("avg_frame_rate"))
                if v_fps is None or v_fps <= 0:
                    v_fps = parse_fps_fraction(s.get("r_frame_rate"))
                video_seen = True
        if s.get("codec_type") == "audio":
            has_audio = True
            # Bitrate on audio stream can be missing (VBR); only set when present
            bitrate = _parse_finite_float(s.get("bit_rate"))
            if bitrate is not None and bitrate >= 0:
                a_bitrate = bitrate / 1000.0
    if rotation_degrees == 0:
        inferred = infer_rotation_from_display_aspect_ratio(v_width, v_height, display_aspect_ratio)
        if inferred:
            rotation_degrees = inferred
    if rotation_degrees == 0:
        inferred_qt = infer_rotation_quicktime_landscape_storage(format_tags, v_width, v_height)
        if inferred_qt:
            rotation_degrees = inferred_qt
    if not has_video and not (allow_audio_only and has_audio):
        raise RuntimeError("Input has no usable video stream")
    disp_w, disp_h = coded_to_display_dimensions(v_width, v_height, rotation_degrees)
    return {
        "duration": duration,
        "video_bitrate_kbps": v_bitrate,
        "audio_bitrate_kbps": a_bitrate,
        "video_codec": v_codec,
        "video_pix_fmt": v_pix_fmt,
        "video_profile": v_profile,
        "video_color_space": v_color_space,
        "video_color_transfer": v_color_transfer,
        "video_color_primaries": v_color_primaries,
        "video_color_range": v_color_range,
        "video_bits_per_raw_sample": v_bits_per_raw_sample,
        "width": v_width,
        "height": v_height,
        "display_width": disp_w,
        "display_height": disp_h,
        "display_aspect_ratio": display_aspect_ratio,
        "rotation_degrees": rotation_degrees,
        "video_fps": v_fps,
        "has_audio": has_audio,
        "has_video": has_video,
    }


def calc_bitrates(target_mb: float, duration_s: float, audio_kbps: int) -> tuple[float, float]:
    """Compute total and video bitrates (kbps) to fit target size given duration and fixed audio bitrate."""
    if duration_s <= 0:
        return 0.0, 0.0
    total_kbps = (target_mb * 8192.0) / duration_s
    video_kbps = max(total_kbps - float(audio_kbps), 0.0)
    return total_kbps, video_kbps
