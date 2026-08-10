#!/usr/bin/env python3
"""Run disposable end-to-end scenarios against a local or Docker runtime.

The test owns its temporary data directory and runtime process/container.  It
uploads real generated media, queues multiple codecs, downloads the results,
and exercises the batch ZIP path.  Explicit hardware codec requests are
useful even on CPU-only hosts because the worker should report a controlled
fallback rather than fail the job.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "tests" / "media" / "generate_media.py"
DEFAULT_CODECS = (
    "libx264,h264_qsv,h264_vaapi,h264_nvenc,h264_amf,"
    "hevc_qsv,hevc_vaapi,hevc_nvenc,hevc_amf,"
    "av1_qsv,av1_vaapi,av1_nvenc,av1_amf,libx265,libsvtav1"
)


class E2EError(RuntimeError):
    """A failure that should be reported as a failed scenario."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _run_checked(command: list[str], *, cwd: Path = ROOT, timeout: float | None = None) -> None:
    print("$ " + " ".join(command))
    try:
        subprocess.run(command, cwd=cwd, check=True, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise E2EError(f"Command failed: {' '.join(command)}") from exc


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 30,
) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise E2EError(f"{method} {url} returned HTTP {exc.code}: {body}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise E2EError(f"{method} {url} failed: {exc}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise E2EError(f"{method} {url} returned invalid JSON: {raw[:400]!r}") from exc
    if not isinstance(result, dict):
        raise E2EError(f"{method} {url} returned a non-object JSON response")
    return result


def _download(url: str, *, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise E2EError(f"GET {url} returned HTTP {exc.code}: {body}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise E2EError(f"GET {url} failed: {exc}") from exc


def _multipart(
    fields: dict[str, str],
    files: Iterable[tuple[str, Path]],
) -> tuple[bytes, str]:
    boundary = f"----8mb-local-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ]
    for field_name, path in files:
        filename = path.name
        chunks += [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            b"\r\n",
        ]
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _multipart_request(
    url: str,
    *,
    fields: dict[str, str],
    files: Iterable[tuple[str, Path]],
    timeout: float = 60,
) -> dict:
    body, content_type = _multipart(fields, files)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:800]
        raise E2EError(f"POST {url} returned HTTP {exc.code}: {body_text}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise E2EError(f"POST {url} failed: {exc}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise E2EError(f"POST {url} returned invalid JSON: {raw[:400]!r}") from exc
    if not isinstance(result, dict):
        raise E2EError(f"POST {url} returned a non-object JSON response")
    return result


def _wait_for_health(base_url: str, process: subprocess.Popen[bytes] | None, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise E2EError(f"Runtime exited during startup with code {process.returncode}")
        try:
            health = _json_request(f"{base_url}/healthz", timeout=2)
            if health.get("ok") is True:
                return health
            last_error = f"unexpected health response: {health}"
        except E2EError as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise E2EError(f"Runtime did not become healthy within {timeout:.0f}s: {last_error}")


class Runtime:
    def __init__(self, mode: str, app_data: Path, port: int, args: argparse.Namespace):
        self.mode = mode
        self.app_data = app_data
        self.port = port
        self.args = args
        self.process: subprocess.Popen[bytes] | None = None
        self.log_handle = None
        self.container_name = f"8mblocal-e2e-{uuid.uuid4().hex[:12]}"
        self.log_path = app_data.parent / "runtime.log"

    @property
    def base_url(self) -> str:
        if self.args.base_url:
            return self.args.base_url.rstrip("/")
        return f"http://127.0.0.1:{self.port}"

    def _docker_available(self) -> bool:
        docker = shutil.which("docker")
        if not docker:
            return False
        result = subprocess.run(
            [docker, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return result.returncode == 0

    def start(self) -> dict:
        self.app_data.mkdir(parents=True, exist_ok=True)
        (self.app_data / "uploads").mkdir(exist_ok=True)
        (self.app_data / "outputs").mkdir(exist_ok=True)
        (self.app_data / "state").mkdir(exist_ok=True)
        (self.app_data / "redis").mkdir(exist_ok=True)
        self.log_handle = self.log_path.open("wb")

        if self.args.base_url:
            return {"mode": "external", "base_url": self.base_url}

        if self.mode == "local":
            env = os.environ.copy()
            env.update(
                {
                    "AUTH_ENABLED": "false",
                    "HISTORY_ENABLED": "true",
                    "WORKER_CONCURRENCY": str(self.args.local_workers),
                    "LOCAL_RUNTIME": "1",
                }
            )
            executable = self.args.local_executable
            if executable:
                command = [str(executable.expanduser().resolve())]
            else:
                command = [sys.executable, str(ROOT / "windows" / "desktop_app.py")]
            command += [
                "--data-dir", str(self.app_data),
                "--port", str(self.port),
                "--no-browser",
            ]
            self.process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
            )
            return {"mode": "local", "command": command}

        docker = shutil.which("docker")
        if not docker or not self._docker_available():
            raise E2EError("Docker mode requested, but no usable Docker daemon is available")
        image = self.args.docker_image
        if self.args.docker_build:
            _run_checked([docker, "build", "-t", image, "."], timeout=3600)

        command = [
            docker,
            "run",
            "--rm",
            "--name",
            self.container_name,
            "--publish",
            f"127.0.0.1:{self.port}:8001",
            "--mount",
            f"type=bind,source={self.app_data / 'uploads'},target=/app/uploads",
            "--mount",
            f"type=bind,source={self.app_data / 'outputs'},target=/app/outputs",
            "--mount",
            f"type=bind,source={self.app_data / 'state'},target=/app/state",
            "--mount",
            f"type=bind,source={self.app_data / 'redis'},target=/var/lib/redis",
            "--env",
            "AUTH_ENABLED=false",
            "--env",
            "HISTORY_ENABLED=true",
            "--env",
            "WORKER_CONCURRENCY=2",
            "--env",
            "APP_DATA_DIR=/app",
            "--env",
            "UPLOADS_DIR=/app/uploads",
            "--env",
            "OUTPUTS_DIR=/app/outputs",
            "--env",
            "ENV_FILE=/app/.env",
            "--env",
            "SETTINGS_FILE=/app/state/settings.json",
            "--env",
            "HISTORY_FILE=/app/state/history.json",
            "--env",
            "TMPDIR=/app/uploads/.tmp",
        ]
        if self.args.docker_gpu == "nvidia":
            command += ["--gpus", "all"]
        elif self.args.docker_gpu == "vaapi":
            command += ["--device", "/dev/dri:/dev/dri"]
        command.append(image)
        self.process = subprocess.Popen(command, stdout=self.log_handle, stderr=subprocess.STDOUT)
        return {"mode": "docker", "command": command, "container": self.container_name}

    def logs(self) -> str:
        try:
            if self.log_handle:
                self.log_handle.flush()
            return self.log_path.read_text(errors="replace")[-8000:]
        except OSError:
            return "<runtime log unavailable>"

    def stop(self) -> None:
        if not self.process:
            if self.log_handle:
                self.log_handle.close()
            return
        if self.mode == "docker":
            docker = shutil.which("docker")
            if docker:
                subprocess.run(
                    [docker, "stop", "--time", "10", self.container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
                subprocess.run(
                    [docker, "rm", "-f", self.container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
        else:
            # On Windows a frozen, windowless PyInstaller process can outlive
            # the Python parent even after ``Popen.terminate`` returns.  Use
            # taskkill only for the exact PID we started, and include its
            # children so an FFmpeg process cannot be orphaned between tests.
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            else:
                self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None


def _wait_job(base_url: str, task_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = _json_request(f"{base_url}/api/jobs/{task_id}/status", timeout=10)
        state = str(last.get("state") or "").upper()
        if state in {"SUCCESS", "COMPLETED", "COMPLETED_WITH_ERRORS"}:
            return last
        if state in {"FAILURE", "FAILED", "REVOKED", "CANCELED", "CANCELLED"}:
            raise E2EError(f"Job {task_id} ended in {state}: {last.get('detail') or last}")
        time.sleep(0.5)
    raise E2EError(f"Job {task_id} did not finish within {timeout:.0f}s: {last}")


def _wait_batch(base_url: str, batch_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = _json_request(f"{base_url}/api/batches/{batch_id}/status", timeout=10)
        state = str(last.get("state") or "").lower()
        if state in {"completed", "completed_with_errors"}:
            return last
        if state == "failed":
            raise E2EError(f"Batch {batch_id} failed: {last}")
        time.sleep(0.5)
    raise E2EError(f"Batch {batch_id} did not finish within {timeout:.0f}s: {last}")


def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "output"


def _download_after_completion(base_url: str, task_id: str) -> bytes:
    last_error: Exception | None = None
    for _ in range(8):
        try:
            return _download(f"{base_url}/api/jobs/{task_id}/download?wait=2", timeout=15)
        except E2EError as exc:
            last_error = exc
            time.sleep(0.5)
    raise E2EError(f"Output for {task_id} never became downloadable: {last_error}")


def _verify_media(path: Path, ffprobe: str) -> dict:
    command = [
        ffprobe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_name,codec_type,width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise E2EError(f"Output is not readable by ffprobe: {path}: {result.stderr[-500:]}")
    try:
        metadata = json.loads(result.stdout)
        duration = float((metadata.get("format") or {}).get("duration") or 0)
        size = int(float((metadata.get("format") or {}).get("size") or 0))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise E2EError(f"ffprobe returned malformed metadata for {path}") from exc
    if duration <= 0 or size <= 0:
        raise E2EError(f"Output has invalid duration/size: {path} ({duration}s, {size} bytes)")
    return {"duration_s": duration, "size_bytes": size, "streams": metadata.get("streams") or []}


def _run_single(
    base_url: str,
    source: Path,
    codec: str,
    output_dir: Path,
    ffprobe: str,
    *,
    audio_codec: str = "aac",
    audio_only: bool = False,
    timeout: float,
) -> dict:
    upload = _multipart_request(
        f"{base_url}/api/upload",
        fields={"target_size_mb": "0.5", "audio_bitrate_kbps": "64"},
        files=[("file", source)],
    )
    job_id = str(upload["job_id"])
    request = {
        "job_id": job_id,
        "filename": upload["filename"],
        "target_size_mb": 0.5,
        "target_video_bitrate_kbps": 300,
        "video_codec": codec,
        "audio_codec": audio_codec,
        "audio_bitrate_kbps": 64,
        "preset": "p1",
        "container": "mp4",
        "tune": "hq",
        "fast_mp4_finalize": True,
        "audio_only": audio_only,
    }
    if audio_only:
        request["container"] = "mp4"
    queued = _json_request(f"{base_url}/api/compress", method="POST", payload=request, timeout=30)
    task_id = str(queued["task_id"])
    status = _wait_job(base_url, task_id, timeout)
    data = _download_after_completion(base_url, task_id)
    suffix = ".m4a" if audio_only else ".mp4"
    output_path = output_dir / f"{_safe_label(source.stem)}-{_safe_label(codec)}{suffix}"
    output_path.write_bytes(data)
    metadata = _verify_media(output_path, ffprobe)
    return {
        "source": source.name,
        "requested_codec": codec,
        "task_id": task_id,
        "state": status.get("state"),
        "output": str(output_path),
        **metadata,
    }


def _run_batch(base_url: str, sources: list[Path], output_dir: Path) -> dict:
    fields = {
        "target_size_mb": "0.5",
        "video_codec": "libx264",
        "audio_codec": "aac",
        "audio_bitrate_kbps": "64",
        "preset": "p1",
        "container": "mp4",
        "tune": "hq",
        "target_video_bitrate_kbps": "300",
        "fast_mp4_finalize": "true",
    }
    response = _multipart_request(
        f"{base_url}/api/batches/upload",
        fields=fields,
        files=[("files", source) for source in sources],
        timeout=60,
    )
    batch_id = str(response["batch_id"])
    status = _wait_batch(base_url, batch_id, timeout=180)
    items = status.get("items") or []
    if len(items) != len(sources) or any(str(item.get("state")).lower() != "completed" for item in items):
        raise E2EError(f"Batch completed with unexpected item states: {status}")
    zip_bytes = _download(f"{base_url}/api/batches/{batch_id}/download.zip", timeout=30)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            if len(names) != len(sources):
                raise E2EError(f"Batch ZIP contains {len(names)} files, expected {len(sources)}")
            zip_path = output_dir / f"batch-{batch_id}.zip"
            zip_path.write_bytes(zip_bytes)
    except zipfile.BadZipFile as exc:
        raise E2EError("Batch download was not a valid ZIP archive") from exc
    return {
        "batch_id": batch_id,
        "state": status.get("state"),
        "item_count": len(items),
        "zip": str(zip_path),
        "zip_entries": names,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "docker", "auto"), default="local")
    parser.add_argument("--codecs", default=DEFAULT_CODECS, help="Comma-separated video codecs to exercise")
    parser.add_argument("--profile", choices=("quick", "extended"), default="extended")
    parser.add_argument("--skip-edge-cases", action="store_true")
    parser.add_argument("--skip-batch", action="store_true")
    parser.add_argument("--timeout", type=float, default=180, help="Per-job timeout in seconds")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--base-url",
        default="",
        help="Exercise an already-running HTTP server instead of starting a local runtime",
    )
    parser.add_argument("--data-dir", type=Path, help="Use this app-data directory instead of a temporary one")
    parser.add_argument(
        "--local-executable",
        type=Path,
        help="Run this packaged local executable instead of windows/desktop_app.py",
    )
    parser.add_argument(
        "--local-workers",
        type=int,
        default=2,
        help="Worker threads for local runtime tests (use 1 to force a queue)",
    )
    parser.add_argument("--keep", action="store_true", help="Keep generated media, outputs, and runtime log")
    parser.add_argument("--docker-image", default="8mb.local:e2e")
    parser.add_argument("--docker-build", action="store_true", help="Build --docker-image before starting Docker")
    parser.add_argument("--docker-gpu", choices=("none", "nvidia", "vaapi"), default="none")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if shutil.which(args.ffmpeg) is None or shutil.which(args.ffprobe) is None:
        raise SystemExit(f"Both {args.ffmpeg!r} and {args.ffprobe!r} must be available on PATH")

    if args.mode == "auto":
        docker = shutil.which("docker")
        docker_ok = False
        if docker:
            try:
                docker_ok = subprocess.run(
                    [docker, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15
                ).returncode == 0
            except (OSError, subprocess.SubprocessError):
                docker_ok = False
        args.mode = "docker" if docker_ok else "local"

    temp_context = None
    if args.data_dir:
        root = args.data_dir.expanduser().resolve().parent / f"8mblocal-e2e-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=True)
        app_data = args.data_dir.expanduser().resolve()
    elif args.keep:
        root = Path(tempfile.mkdtemp(prefix="8mblocal-e2e-"))
        app_data = root / "app-data"
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="8mblocal-e2e-")
        root = Path(temp_context.name)
        app_data = root / "app-data"

    media_dir = root / "media"
    output_dir = root / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    port = args.port or _free_port()
    runtime = Runtime(args.mode, app_data, port, args)
    summary: dict = {"mode": args.mode, "port": port, "root": str(root), "jobs": [], "failures": []}

    try:
        _run_checked(
            [
                sys.executable,
                str(GENERATOR),
                "--output-dir",
                str(media_dir),
                "--profile",
                args.profile,
                "--ffmpeg",
                args.ffmpeg,
                "--ffprobe",
                args.ffprobe,
            ]
        )
        generated = {path.name: path for path in media_dir.iterdir() if path.is_file()}
        runtime_info = runtime.start()
        summary["runtime"] = runtime_info
        health = _wait_for_health(runtime.base_url, runtime.process, timeout=90)
        summary["health"] = health

        for codec in [item.strip() for item in args.codecs.split(",") if item.strip()]:
            try:
                result = _run_single(
                    runtime.base_url,
                    generated["baseline.mp4"],
                    codec,
                    output_dir,
                    args.ffprobe,
                    timeout=args.timeout,
                )
                summary["jobs"].append(result)
                print(f"PASS codec={codec} output={result['size_bytes']} bytes")
            except (E2EError, KeyError) as exc:
                failure = {"scenario": f"codec:{codec}", "error": str(exc)}
                summary["failures"].append(failure)
                print(f"FAIL codec={codec}: {exc}")

        if not args.skip_edge_cases:
            edge_cases = [
                ("no_audio.mp4", "libx264", "none", False),
                ("vertical.mp4", "libx264", "aac", False),
                ("audio_only.m4a", "libx264", "aac", True),
            ]
            for filename, codec, audio_codec, audio_only in edge_cases:
                try:
                    result = _run_single(
                        runtime.base_url,
                        generated[filename],
                        codec,
                        output_dir,
                        args.ffprobe,
                        audio_codec=audio_codec,
                        audio_only=audio_only,
                        timeout=args.timeout,
                    )
                    summary["jobs"].append(result)
                    print(f"PASS edge={filename} output={result['size_bytes']} bytes")
                except (E2EError, KeyError) as exc:
                    failure = {"scenario": f"edge:{filename}", "error": str(exc)}
                    summary["failures"].append(failure)
                    print(f"FAIL edge={filename}: {exc}")

        if not args.skip_batch:
            try:
                batch_sources = [generated["baseline.mp4"], generated["unicode name [sample] — 01.mp4"]]
                summary["batch"] = _run_batch(runtime.base_url, batch_sources, output_dir)
                print(f"PASS batch ZIP entries={summary['batch']['item_count']}")
            except (E2EError, KeyError) as exc:
                summary["failures"].append({"scenario": "batch", "error": str(exc)})
                print(f"FAIL batch: {exc}")
    finally:
        summary["runtime_log_tail"] = runtime.logs()
        runtime.stop()
        if temp_context is not None:
            temp_context.cleanup()

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["failures"]:
        print(f"E2E FAILED: {len(summary['failures'])} scenario(s) failed", file=sys.stderr)
        return 1
    print(f"E2E PASSED: {len(summary['jobs'])} codec/edge job(s) and batch scenario")
    if args.keep or args.data_dir:
        print(f"Artifacts kept at: {root}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except E2EError as exc:
        print(f"E2E ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
