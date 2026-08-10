#!/usr/bin/env python3
"""Generate a small deterministic media corpus for end-to-end tests.

The corpus is intentionally generated at test time instead of being checked
into git.  That keeps the repository small while giving local, CI, Docker,
and Windows smoke tests identical inputs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def _run(ffmpeg: str, args: list[str]) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(command, check=True)


def _probe(ffprobe: str, path: Path) -> dict:
    command = [
        ffprobe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    metadata = json.loads(result.stdout)
    duration = float((metadata.get("format") or {}).get("duration") or 0)
    streams = metadata.get("streams") or []
    if duration <= 0 or not streams:
        raise RuntimeError(f"Generated media did not probe successfully: {path}")
    return metadata


def _make_video(
    ffmpeg: str,
    output: Path,
    *,
    size: str,
    rate: int,
    duration: float,
    audio: bool,
) -> None:
    args = [
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate={rate}",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000"]
    args += ["-t", str(duration), "-map", "0:v:0"]
    if audio:
        args += ["-map", "1:a:0"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
    ]
    if audio:
        args += ["-c:a", "aac", "-b:a", "96k"]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(output)]
    _run(ffmpeg, args)


def generate(output_dir: Path, ffmpeg: str, ffprobe: str, profile: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    baseline = output_dir / "baseline.mp4"
    _make_video(ffmpeg, baseline, size="320x240", rate=24, duration=2, audio=True)
    generated.append(baseline)

    no_audio = output_dir / "no_audio.mp4"
    _make_video(ffmpeg, no_audio, size="320x240", rate=24, duration=2, audio=False)
    generated.append(no_audio)

    vertical = output_dir / "vertical.mp4"
    _make_video(ffmpeg, vertical, size="240x320", rate=24, duration=2, audio=True)
    generated.append(vertical)

    audio_only = output_dir / "audio_only.m4a"
    _run(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "2",
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(audio_only),
        ],
    )
    generated.append(audio_only)

    # A filename that exercises multipart filename handling and safe output
    # naming without relying on path separators or platform-reserved names.
    tricky_name = output_dir / "unicode name [sample] — 01.mp4"
    shutil.copyfile(baseline, tricky_name)
    generated.append(tricky_name)

    if profile == "extended":
        high_fps = output_dir / "high_fps.mp4"
        _make_video(ffmpeg, high_fps, size="320x240", rate=60, duration=1.5, audio=True)
        generated.append(high_fps)

    for path in generated:
        _probe(ffprobe, path)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--profile", choices=("quick", "extended"), default="extended")
    args = parser.parse_args()

    paths = generate(args.output_dir, args.ffmpeg, args.ffprobe, args.profile)
    print(json.dumps({"output_dir": str(args.output_dir), "files": [str(p) for p in paths]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
