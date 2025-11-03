from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Dict, Tuple

from .schemas import JobEstimate, JobRequest, ProbeResponse


async def run_ffprobe(path: Path) -> ProbeResponse:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,bit_rate,size",
        "-show_streams",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()}".strip())
    payload = json.loads(stdout.decode())
    format_info = payload.get("format", {})
    streams = payload.get("streams", [])

    duration = float(format_info.get("duration", 0.0))
    bitrate = float(format_info.get("bit_rate", 0.0)) / 1000.0
    size = float(format_info.get("size", 0.0)) / (1024 * 1024)

    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    audio_bitrate = 0.0
    if audio_streams:
        audio_bitrate = float(audio_streams[0].get("bit_rate", 0.0)) / 1000.0

    return ProbeResponse(
        duration=duration,
        bitrate_kbps=bitrate,
        audio_bitrate_kbps=audio_bitrate,
        size_mb=size,
    )


def estimate_bitrates(probe: ProbeResponse, request: JobRequest) -> Tuple[JobEstimate, Dict[str, float]]:
    if probe.duration <= 0:
        raise ValueError("Video duration must be greater than zero")

    target_total_bitrate = (request.target_size_mb * 8192) / probe.duration
    video_bitrate = max(target_total_bitrate - request.audio_bitrate_kbps, 0)

    warning = None
    if video_bitrate < 100:
        warning = "Target video bitrate is below 100 kbps. Output quality will be extremely low."

    return (
        JobEstimate(
            target_total_bitrate_kbps=target_total_bitrate,
            target_video_bitrate_kbps=video_bitrate,
            warning=warning,
        ),
        {
            "target_total_bitrate_kbps": target_total_bitrate,
            "target_video_bitrate_kbps": video_bitrate,
            "maxrate_kbps": video_bitrate * 1.2,
            "bufsize_kbps": video_bitrate * 2,
        },
    )


def human_readable_size(num_bytes: float) -> str:
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    power = min(int(math.log(num_bytes, 1024)), len(units) - 1)
    value = num_bytes / (1024 ** power)
    return f"{value:.1f} {units[power]}"
