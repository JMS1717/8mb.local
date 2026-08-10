"""Small native Windows command-line build of 8mb.local.

This executable intentionally has no Docker, Redis, Celery, or Python runtime
dependency at use time. It uses the bundled FFmpeg binaries and probes native
Windows encoders before each codec family is used:

    8mblocal.exe input.mp4 --target-mb 9.7 --video-codec auto

``auto`` prefers NVENC, Quick Sync, and AMF before software encoding. Linux
VAAPI is not selected on Windows; AMD uses AMF there when the FFmpeg build and
driver expose it. The web application remains the full-featured deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable


VERSION = "native-1"
CPU_ENCODERS = {
    "h264": "libx264",
    "hevc": "libx265",
    "av1": "libsvtav1",
}
ENCODER_CANDIDATES = {
    "h264": ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"],
    "hevc": ["hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265"],
    "av1": ["av1_nvenc", "av1_qsv", "av1_amf", "libsvtav1"],
}
VIDEO_CODEC_ALIASES = {
    "h264": "h264",
    "avc": "h264",
    "hevc": "hevc",
    "h265": "hevc",
    "av1": "av1",
}


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _binary(name: str) -> str:
    """Find a bundled binary first, then an operator-provided PATH binary."""
    filename = f"{name}.exe" if os.name == "nt" else name
    candidates = []
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "bin" / filename)
    candidates.extend((
        _base_dir() / "bin" / filename,
        _base_dir() / filename,
    ))
    configured = os.environ.get(name.upper() + "_PATH", "").strip()
    if configured:
        candidates.insert(0, Path(configured))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(filename) or shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{filename} was not found. Put FFmpeg binaries in a bin folder next to the executable "
        f"or add them to PATH."
    )


def _run(command: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _ffprobe(input_path: Path) -> dict:
    result = _run([
        _binary("ffprobe"), "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(input_path),
    ], timeout=30)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffprobe failed").strip()[-1200:])
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ffprobe returned an invalid duration") from exc
    if not duration > 0:
        raise RuntimeError("Input has no usable duration")
    has_video = any(s.get("codec_type") == "video" for s in data.get("streams", []))
    if not has_video:
        raise RuntimeError("Input has no video stream")
    return {"duration": duration, "has_audio": any(
        s.get("codec_type") == "audio" for s in data.get("streams", [])
    )}


def _encoder_init_flags(encoder: str) -> list[str]:
    # Windows QSV commonly works with an explicit device context, while
    # builds using D3D11 may reject it. _probe_encoder tries both forms.
    return ["-init_hw_device", "qsv=hw"] if encoder.endswith("_qsv") else []


def _probe_encoder(encoder: str) -> bool:
    attempts = [_encoder_init_flags(encoder)]
    if attempts[0]:
        attempts.append([])
    for init_flags in attempts:
        try:
            result = _run([
                _binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                *init_flags, "-f", "lavfi", "-i", "color=c=black:s=256x256:r=1",
                "-frames:v", "1", "-pix_fmt", "yuv420p", "-c:v", encoder,
                "-f", "null", "-",
            ], timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        if result.returncode == 0:
            return True
    return False


def _normalize_codec(value: str) -> str:
    key = value.strip().lower()
    if key in VIDEO_CODEC_ALIASES:
        return VIDEO_CODEC_ALIASES[key]
    for family, candidates in ENCODER_CANDIDATES.items():
        if key in candidates:
            return family
    raise ValueError("video codec must be auto, h264, hevc, av1, or a native FFmpeg encoder")


def _select_encoder(requested: str, probe_cache: dict[str, bool]) -> tuple[str, list[str]]:
    requested_key = requested.strip().lower()
    if requested_key == "auto":
        family = "auto"
        # Match the web app's quality-first default, then fall back to more
        # broadly supported families.
        candidates = [
            encoder
            for family in ("av1", "hevc", "h264")
            for encoder in ENCODER_CANDIDATES[family]
        ]
    else:
        family = _normalize_codec(requested)
        candidates = ENCODER_CANDIDATES[family]
    if requested_key in candidates:
        candidates = [requested_key, *[x for x in candidates if x != requested_key]]
    for encoder in candidates:
        if encoder not in probe_cache:
            probe_cache[encoder] = _probe_encoder(encoder)
        if probe_cache[encoder]:
            return encoder, _encoder_init_flags(encoder)
    scope = "any native" if family == "auto" else family
    raise RuntimeError(f"No working {scope} encoder was found")


def _safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", path.stem).strip(" .") or "output"
    return stem[:120]


def _encode_once(
    input_path: Path,
    output_path: Path,
    encoder: str,
    init_flags: list[str],
    video_kbps: int,
    audio_kbps: int,
    has_audio: bool,
) -> None:
    command = [
        _binary("ffmpeg"), "-hide_banner", "-y", *init_flags, "-i", str(input_path),
        "-c:v", encoder, "-b:v", f"{video_kbps}k",
        "-maxrate", f"{max(video_kbps, 1) * 12 // 10}k",
        "-bufsize", f"{max(video_kbps, 1) * 2}k",
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        command += ["-c:a", "aac", "-b:a", f"{audio_kbps}k"]
    else:
        command += ["-an"]
    command += ["-movflags", "+faststart", str(output_path)]
    result = _run(command, timeout=60 * 60)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "FFmpeg failed").strip()
        raise RuntimeError(message[-1600:])
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg completed without producing an output file")


def compress_file(
    input_path: Path,
    output_dir: Path,
    target_mb: float,
    requested_codec: str,
    audio_kbps: int,
    probe_cache: dict[str, bool],
) -> Path:
    info = _ffprobe(input_path)
    total_kbps = max(64, int(target_mb * 8192 / info["duration"]))
    video_kbps = max(32, total_kbps - (audio_kbps if info["has_audio"] else 0))
    output_path = output_dir / f"{_safe_stem(input_path)}_8mblocal.mp4"
    selected_encoder, init_flags = _select_encoder(requested_codec, probe_cache)
    best_temp: Path | None = None
    current_kbps = video_kbps
    temp_path: Path | None = None
    try:
        for attempt in range(3):
            temp_path = output_dir / f".{output_path.stem}.{uuid.uuid4().hex}.part.mp4"
            try:
                _encode_once(
                    input_path, temp_path, selected_encoder, init_flags,
                    current_kbps, audio_kbps, info["has_audio"],
                )
            except Exception:
                temp_path.unlink(missing_ok=True)
                # A driver can disappear between the one-frame probe and the
                # real encode. Re-select once, allowing CPU fallback.
                if selected_encoder not in CPU_ENCODERS.values():
                    probe_cache[selected_encoder] = False
                    selected_encoder, init_flags = _select_encoder(requested_codec, probe_cache)
                    try:
                        _encode_once(
                            input_path, temp_path, selected_encoder, init_flags,
                            current_kbps, audio_kbps, info["has_audio"],
                        )
                    except Exception:
                        temp_path.unlink(missing_ok=True)
                        raise
                else:
                    raise
            if best_temp is not None and temp_path.stat().st_size < best_temp.stat().st_size:
                best_temp.unlink(missing_ok=True)
                best_temp = temp_path
            elif best_temp is None:
                best_temp = temp_path
            else:
                temp_path.unlink(missing_ok=True)
            size_mb = best_temp.stat().st_size / (1024 * 1024)
            if size_mb <= target_mb * 1.02 or attempt == 2:
                break
            current_kbps = max(32, int(current_kbps * target_mb / size_mb * 0.94))
        if best_temp is None:
            raise RuntimeError("No output was produced")
        os.replace(best_temp, output_path)
        return output_path
    finally:
        if best_temp is not None and best_temp.exists():
            best_temp.unlink(missing_ok=True)
        if temp_path is not None and temp_path.exists() and temp_path != best_temp:
            temp_path.unlink(missing_ok=True)


def _iter_inputs(values: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compress videos without Docker using native Windows FFmpeg codecs.")
    parser.add_argument("inputs", nargs="*", help="one or more video files")
    parser.add_argument("--output-dir", default=".", help="directory for MP4 outputs (default: current directory)")
    parser.add_argument("--target-mb", type=float, default=9.7, help="target output size in MB")
    parser.add_argument("--video-codec", default="auto", help="auto, h264, hevc, av1, or a native FFmpeg encoder")
    parser.add_argument("--audio-kbps", type=int, default=128, help="AAC audio bitrate")
    parser.add_argument("--jobs", type=int, default=1, help="parallel files; keep at 1 for a single GPU")
    parser.add_argument("--list-codecs", action="store_true", help="probe and list native encoder candidates")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)

    if args.list_codecs:
        cache: dict[str, bool] = {}
        for family, candidates in ENCODER_CANDIDATES.items():
            print(f"{family}:")
            for encoder in candidates:
                cache[encoder] = _probe_encoder(encoder)
                print(f"  {'OK' if cache[encoder] else 'unavailable':11} {encoder}")
        return 0
    if not args.inputs:
        parser.error("provide at least one input file, or use --list-codecs")
    if args.target_mb <= 0 or args.audio_kbps < 0 or args.audio_kbps > 2000:
        parser.error("--target-mb must be > 0 and --audio-kbps must be between 0 and 2000")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    try:
        inputs = _iter_inputs(args.inputs)
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        probe_cache: dict[str, bool] = {}
        # Probe once before starting the batch so concurrent files share the
        # result instead of racing several identical FFmpeg startup tests.
        _select_encoder(args.video_codec, probe_cache)

        def one(path: Path) -> tuple[Path, Path]:
            output = compress_file(path, output_dir, args.target_mb, args.video_codec, args.audio_kbps, probe_cache)
            return path, output

        failures = 0
        with ThreadPoolExecutor(max_workers=min(args.jobs, len(inputs))) as pool:
            futures = [pool.submit(one, path) for path in inputs]
            for future in as_completed(futures):
                try:
                    source, output = future.result()
                    print(f"OK  {source.name} -> {output.name} ({output.stat().st_size / (1024 * 1024):.2f} MB)")
                except Exception as exc:
                    failures += 1
                    print(f"ERR {exc}", file=sys.stderr)
        return 1 if failures else 0
    except Exception as exc:
        print(f"ERR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
