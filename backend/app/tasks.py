from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

import redis

from .celery_app import celery
from .config import get_settings
from .jobs import JOB_KEY_PREFIX, channel_name

settings = get_settings()


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _publish(job_id: str, message: Dict) -> None:
    client = _redis_client()
    client.publish(channel_name(job_id), json.dumps(message))
    key = f"{JOB_KEY_PREFIX}:{job_id}:log"
    if message.get("type") == "log":
        client.rpush(key, message["payload"].get("line"))
        client.ltrim(key, -500, -1)


def _update_state(job_id: str, **changes) -> None:
    client = _redis_client()
    key = f"{JOB_KEY_PREFIX}:{job_id}:state"
    state = client.hgetall(key)
    state.update(changes)
    state.setdefault("job_id", job_id)
    state.setdefault("status", "queued")
    state.setdefault("progress", 0.0)
    state.setdefault("created_at", datetime.utcnow().isoformat())
    state["updated_at"] = datetime.utcnow().isoformat()
    client.hset(key, mapping=state)


def _read_progress(stream: Iterable[str], job_id: str) -> None:
    for raw_line in stream:
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        _publish(job_id, {"type": "log", "payload": {"line": line}})
        if "frame=" in line or "time=" in line:
            continue


def _read_metrics(stream: Iterable[bytes], job_id: str, duration: Optional[float]) -> None:
    for raw_line in stream:
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        if "out_time_ms" in line:
            try:
                _, value = line.split("=")
                progress_ms = float(value)
                progress = 0.0
                if duration and duration > 0:
                    progress = min(progress_ms / (duration * 1000_000), 1.0)
                _update_state(job_id, progress=progress)
                _publish(job_id, {"type": "progress", "payload": {"out_time_ms": progress_ms, "ratio": progress}})
            except ValueError:
                continue


def build_ffmpeg_command(
    input_file: Path,
    output_file: Path,
    job_payload: Dict[str, any],
) -> Iterable[str]:
    target_video_bitrate = job_payload["target_video_bitrate_kbps"]
    maxrate = job_payload["maxrate_kbps"]
    bufsize = job_payload["bufsize_kbps"]
    audio_bitrate = job_payload["audio_bitrate_kbps"]
    codec = job_payload["video_codec"]
    audio_codec = job_payload["audio_codec"]
    preset = job_payload["preset"]

    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(input_file),
        "-c:v",
        codec,
        "-b:v",
        f"{target_video_bitrate:.0f}k",
        "-maxrate",
        f"{maxrate:.0f}k",
        "-bufsize",
        f"{bufsize:.0f}k",
        "-preset",
        preset,
        "-tune",
        "hq",
        "-c:a",
        audio_codec,
        "-b:a",
        f"{audio_bitrate}k",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_file),
    ]


@celery.task(name="8mblocal.compress_video")
def compress_video(job_id: str, payload: Dict[str, any]) -> Dict[str, any]:
    input_file = Path(payload["input_path"])
    output_file = Path(payload["output_path"])
    output_file.parent.mkdir(parents=True, exist_ok=True)

    _update_state(job_id, status="running")
    _publish(job_id, {"type": "status", "payload": {"status": "running"}})

    command = build_ffmpeg_command(input_file, output_file, payload)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )

    stdout_thread = threading.Thread(
        target=_read_metrics,
        args=(iter(process.stdout.readline, b""), job_id, payload.get("duration")),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_progress, args=(iter(process.stderr.readline, b""), job_id), daemon=True
    )

    stdout_thread.start()
    stderr_thread.start()

    return_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()

    if return_code != 0:
        _update_state(job_id, status="failed", progress=0.0)
        _publish(
            job_id,
            {
                "type": "status",
                "payload": {"status": "failed", "return_code": return_code},
            },
        )
        raise RuntimeError(f"FFmpeg exited with status {return_code}")

    final_size = output_file.stat().st_size if output_file.exists() else 0

    summary = {
        "status": "completed",
        "output_path": str(output_file),
        "output_size": final_size,
        "output_basename": output_file.name,
    }

    _update_state(job_id, status="completed", progress=1.0, output_path=str(output_file))
    _publish(job_id, {"type": "status", "payload": summary})
    _publish(job_id, {"type": "result", "payload": summary})

    return summary
