"""FFmpeg progress parsing utilities.

Provides helpers for calculating encoding progress from ffmpeg stderr
``-progress pipe:2`` output and estimating time remaining.
"""
from __future__ import annotations

import math
from typing import Optional


def parse_time_string(t: str | int | float) -> float:
    """Parse an ffmpeg-style time string (HH:MM:SS or seconds) to float seconds."""
    if isinstance(t, (int, float)):
        return float(t)
    s = str(t)
    if ':' in s:
        parts = s.split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    return float(s)


def parse_ffmpeg_bitrate(val: str) -> float:
    """Extract numeric kbps from an ffmpeg bitrate string like ``'1234.5kbits/s'``."""
    br_str = val.strip().replace("kbits/s", "").replace("kbit/s", "")
    return float(br_str)


def parse_ffmpeg_speed(val: str) -> Optional[float]:
    """Extract numeric speed multiplier from ``'2.34x'``."""
    sval = (val or "").strip()
    if sval.endswith("x"):
        sval = sval[:-1]
    sp = float(sval)
    if math.isfinite(sp) and sp > 0:
        return sp
    return None


def compute_progress(
    current_time_s: float,
    duration: float,
    encoding_portion: float,
    speed_ewma: Optional[float],
    elapsed_s: float,
    current_size_bytes: int,
    target_size_mb: float,
) -> float:
    """Compute scaled encoding progress (0.0 .. encoding_portion) from multiple signals.

    Uses a weighted blend of time-based and wallclock-based estimates,
    favouring time stability.
    """
    if duration <= 0:
        return 0.0

    time_progress = min(max(current_time_s / duration, 0.0), 1.0)

    wallclock_progress = 0.0
    if speed_ewma and speed_ewma > 0.01 and elapsed_s > 2.0:
        est_total_time = duration / speed_ewma
        if est_total_time > 0:
            wallclock_progress = min(max(elapsed_s / est_total_time, 0.0), 1.0)

    if wallclock_progress > 0.01 and elapsed_s > 3.0:
        scaled = (0.7 * time_progress + 0.3 * wallclock_progress) * encoding_portion
    else:
        scaled = time_progress * encoding_portion

    return min(max(scaled, 0.0), encoding_portion)


def compute_eta(
    speed_ewma: Optional[float],
    duration: float,
    elapsed_s: float,
    encoding_portion: float,
    finalize_portion: float,
    is_mp4: bool,
    fast_mp4_finalize: bool,
) -> Optional[float]:
    """Estimate seconds remaining based on measured encoding speed."""
    if not speed_ewma or speed_ewma <= 0.01 or duration <= 0:
        return None
    try:
        est_total = duration / speed_ewma
        fin_factor = 1.15 if (is_mp4 and not fast_mp4_finalize) else 1.0
        total_with_final = est_total * (encoding_portion + fin_factor * finalize_portion)
        return max(total_with_final - elapsed_s, 0.0)
    except Exception:
        return None


def update_speed_ewma(
    current: Optional[float],
    new_speed: float,
    alpha: float = 0.3,
) -> float:
    """Update an exponentially-weighted moving average of encoding speed."""
    if current is None:
        return new_speed
    return alpha * new_speed + (1.0 - alpha) * current
