#!/usr/bin/env python3
"""Run disposable end-to-end scenarios against a local or Docker runtime.

The test owns its temporary data directory and runtime process/container.  It
uploads real generated media, queues multiple codecs, downloads the results,
and exercises the batch ZIP path. Explicit hardware codec requests are useful
even on CPU-only hosts because the worker should report a controlled fallback
rather than fail the job. Use ``--require-exact-codecs`` for hardware proof;
that mode rejects CPU fallback and unknown final encoders.
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


def _cleanup_temporary_context(context: tempfile.TemporaryDirectory) -> None:
    """Retry Windows file cleanup after taskkill/FFmpeg shutdown.

    The packaged app writes a diagnostic log from a child process. Windows can
    release that handle a moment after the process tree exits; treating the
    brief release delay as an application failure makes an otherwise complete
    E2E run misleading.
    """
    last_error: PermissionError | None = None
    for _ in range(20):
        try:
            context.cleanup()
            return
        except PermissionError as exc:
            last_error = exc
            if os.name != "nt":
                raise
            time.sleep(0.25)
    if last_error is not None:
        raise last_error


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


def _multipart_error(
    url: str,
    *,
    fields: dict[str, str],
    files: Iterable[tuple[str, Path]],
    timeout: float = 60,
) -> tuple[int, dict]:
    """Submit multipart data and return a structured expected HTTP error."""
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
            raise E2EError(f"POST {url} unexpectedly returned HTTP {response.status}: {raw[:400]!r}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as parse_error:
            raise E2EError(f"POST {url} returned non-JSON HTTP {exc.code}: {raw[:400]!r}") from parse_error
        if not isinstance(payload, dict):
            raise E2EError(f"POST {url} returned non-object HTTP {exc.code} JSON")
        return exc.code, payload


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


def _wait_for_states(base_url: str, task_id: str, states: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    expected = {state.upper() for state in states}
    while time.monotonic() < deadline:
        last = _json_request(f"{base_url}/api/jobs/{task_id}/status", timeout=10)
        if str(last.get("state") or "").upper() in expected:
            return last
        time.sleep(0.25)
    raise E2EError(f"Job {task_id} did not reach {sorted(expected)} within {timeout:.0f}s: {last}")


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
        "format=duration,size,format_name:stream=codec_name,codec_type,width,height",
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
    return {
        "duration_s": duration,
        "size_bytes": size,
        "format_name": str((metadata.get("format") or {}).get("format_name") or ""),
        "streams": metadata.get("streams") or [],
    }


def _expected_video_codec(encoder: str) -> str:
    if encoder.startswith("h264_") or encoder == "libx264":
        return "h264"
    if encoder.startswith("hevc_") or encoder == "libx265":
        return "hevc"
    if encoder.startswith("av1_") or encoder in {"libsvtav1", "libaom-av1"}:
        return "av1"
    raise E2EError(f"No stream-codec expectation is defined for encoder {encoder!r}")


def _assert_output_shape(
    metadata: dict,
    *,
    encoder: str,
    audio_codec: str,
    audio_only: bool,
) -> None:
    streams = metadata["streams"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if audio_only:
        if video_streams or not audio_streams:
            raise E2EError(f"Audio-only output has unexpected streams: {streams}")
    else:
        if len(video_streams) != 1:
            raise E2EError(f"Expected exactly one video stream: {streams}")
        video = video_streams[0]
        expected_codec = _expected_video_codec(encoder)
        if video.get("codec_name") != expected_codec:
            raise E2EError(
                f"Encoder {encoder!r} produced stream codec "
                f"{video.get('codec_name')!r}, expected {expected_codec!r}"
            )
        if int(video.get("width") or 0) <= 0 or int(video.get("height") or 0) <= 0:
            raise E2EError(f"Video output has invalid dimensions: {video}")
        if audio_codec == "none" and audio_streams:
            raise E2EError(f"Muted output unexpectedly contains audio: {audio_streams}")
        if audio_codec != "none" and not audio_streams:
            raise E2EError("Output unexpectedly lost its audio stream")
    if "mp4" not in metadata["format_name"] and "mov" not in metadata["format_name"]:
        raise E2EError(f"Expected an MP4/M4A container, got {metadata['format_name']!r}")


def _run_single(
    base_url: str,
    source: Path,
    codec: str,
    output_dir: Path,
    ffprobe: str,
    *,
    audio_codec: str = "aac",
    audio_only: bool = False,
    require_exact_codec: bool = False,
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
    actual_encoder = status.get("encoder")
    if not audio_only and require_exact_codec and actual_encoder != codec:
        raise E2EError(
            f"Requested encoder {codec!r}, but final encoder was "
            f"{actual_encoder or 'unknown'!r}"
        )
    data = _download_after_completion(base_url, task_id)
    suffix = ".m4a" if audio_only else ".mp4"
    output_path = output_dir / f"{_safe_label(source.stem)}-{_safe_label(codec)}{suffix}"
    output_path.write_bytes(data)
    metadata = _verify_media(output_path, ffprobe)
    verified_encoder = str(actual_encoder or codec)
    _assert_output_shape(
        metadata,
        encoder=verified_encoder,
        audio_codec=audio_codec,
        audio_only=audio_only,
    )
    return {
        "source": source.name,
        "requested_codec": codec,
        "actual_encoder": actual_encoder,
        "task_id": task_id,
        "state": status.get("state"),
        "output": str(output_path),
        **metadata,
    }


def _enqueue_single(base_url: str, source: Path, codec: str, *, preset: str = "p1") -> str:
    upload = _multipart_request(
        f"{base_url}/api/upload",
        fields={"target_size_mb": "1", "audio_bitrate_kbps": "64"},
        files=[("file", source)],
    )
    queued = _json_request(
        f"{base_url}/api/compress",
        method="POST",
        payload={
            "job_id": str(upload["job_id"]),
            "filename": str(upload["filename"]),
            "target_size_mb": 1,
            "target_video_bitrate_kbps": 500,
            "video_codec": codec,
            "audio_codec": "aac",
            "audio_bitrate_kbps": 64,
            "preset": preset,
            "container": "mp4",
            "tune": "hq",
            "fast_mp4_finalize": True,
        },
    )
    return str(queued["task_id"])


def _assert_canceled(base_url: str, task_id: str, timeout: float = 45) -> dict:
    status = _wait_for_states(base_url, task_id, {"REVOKED", "CANCELED", "CANCELLED"}, timeout)
    try:
        _download(f"{base_url}/api/jobs/{task_id}/download", timeout=10)
    except E2EError as exc:
        if "HTTP 404" not in str(exc):
            raise
    else:
        raise E2EError(f"Canceled job {task_id} incorrectly exposed a successful download")
    return status


def _run_cancellation_scenarios(base_url: str, long_source: Path, *, queued: bool) -> dict:
    active_task = _enqueue_single(base_url, long_source, "libsvtav1", preset="p7")
    _wait_for_states(base_url, active_task, {"STARTED", "PROGRESS"}, timeout=30)
    time.sleep(0.5)
    _json_request(f"{base_url}/api/jobs/{active_task}/cancel", method="POST", timeout=15)
    active_status = _assert_canceled(base_url, active_task)
    result: dict = {"active": {"task_id": active_task, "state": active_status.get("state")}}

    if queued:
        blocker = _enqueue_single(base_url, long_source, "libsvtav1", preset="p7")
        _wait_for_states(base_url, blocker, {"STARTED", "PROGRESS"}, timeout=30)
        victim = _enqueue_single(base_url, long_source, "libx264", preset="p1")
        # With one local worker, the second Future must remain pending until
        # the blocker exits. Canceling it proves queued cancellation without
        # racing a very fast encoder startup.
        time.sleep(0.5)
        victim_initial = _json_request(f"{base_url}/api/jobs/{victim}/status", timeout=10)
        if str(victim_initial.get("state") or "").upper() != "PENDING":
            raise E2EError(f"Queued cancellation precondition failed: {victim_initial}")
        _json_request(f"{base_url}/api/jobs/{victim}/cancel", method="POST", timeout=15)
        victim_status = _assert_canceled(base_url, victim)
        _json_request(f"{base_url}/api/jobs/{blocker}/cancel", method="POST", timeout=15)
        blocker_status = _assert_canceled(base_url, blocker)
        result["queued"] = {"task_id": victim, "state": victim_status.get("state")}
        result["blocker_cleanup"] = {"task_id": blocker, "state": blocker_status.get("state")}
    return result


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
            if any(Path(name).name != name or name.startswith(("/", "\\")) for name in names):
                raise E2EError(f"Batch ZIP contains unsafe member names: {names}")
            if len(set(name.casefold() for name in names)) != len(names):
                raise E2EError(f"Batch ZIP contains colliding member names: {names}")
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


def _run_mixed_batch(base_url: str, valid: Path, invalid: Path, output_dir: Path) -> dict:
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
        files=[("files", valid), ("files", invalid)],
    )
    batch_id = str(response["batch_id"])
    status = _wait_batch(base_url, batch_id, timeout=180)
    items = status.get("items") or []
    states = [str(item.get("state") or "").lower() for item in items]
    if len(items) != 2 or sorted(states) != ["completed", "failed"]:
        raise E2EError(f"Mixed batch did not preserve one success and one failure: {status}")
    if str(status.get("state") or "").lower() != "completed_with_errors":
        raise E2EError(f"Mixed batch has wrong terminal state: {status}")
    zip_bytes = _download(f"{base_url}/api/batches/{batch_id}/download.zip", timeout=30)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        if len(names) != 1 or Path(names[0]).name != names[0]:
            raise E2EError(f"Mixed batch ZIP should contain only its successful safe output: {names}")
    zip_path = output_dir / f"mixed-batch-{batch_id}.zip"
    zip_path.write_bytes(zip_bytes)
    return {"batch_id": batch_id, "state": status.get("state"), "states": states, "zip": str(zip_path)}


def _verify_terminal_sse_replay(base_url: str, task_id: str) -> dict:
    request = urllib.request.Request(
        f"{base_url}/api/stream/{task_id}",
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    )
    events: list[dict] = []
    with urllib.request.urlopen(request, timeout=15) as response:
        if "text/event-stream" not in response.headers.get("Content-Type", ""):
            raise E2EError(f"SSE endpoint returned wrong content type: {response.headers}")
        while len(events) < 4:
            line = response.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded.startswith("data:"):
                continue
            event = json.loads(decoded[5:].strip())
            events.append(event)
            if event.get("type") in {"done", "error", "canceled"}:
                break
    kinds = [event.get("type") for event in events]
    if not kinds or kinds[0] != "connected" or "done" not in kinds:
        raise E2EError(f"Completed job SSE reconnect missed connected/done replay: {events}")
    return {"task_id": task_id, "events": kinds}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "docker", "auto"), default="local")
    parser.add_argument("--codecs", default=DEFAULT_CODECS, help="Comma-separated video codecs to exercise")
    parser.add_argument(
        "--require-exact-codecs",
        action="store_true",
        help="Fail if a video job falls back or does not report its final encoder",
    )
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
        "--allow-existing-data-dir",
        action="store_true",
        help="Explicitly allow tests to use a non-empty app-data directory",
    )
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
    parser.add_argument("--docker-image", default="jms1717/8mblocal:latest")
    parser.add_argument("--docker-build", action="store_true", help="Build --docker-image before starting Docker")
    parser.add_argument("--docker-gpu", choices=("none", "nvidia", "vaapi"), default="none")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if os.name == "nt":
        bundled_bin = ROOT / "windows" / "ffmpeg" / "bin"
        if args.ffmpeg == "ffmpeg" and shutil.which(args.ffmpeg) is None:
            bundled_ffmpeg = bundled_bin / "ffmpeg.exe"
            if bundled_ffmpeg.is_file():
                args.ffmpeg = str(bundled_ffmpeg)
        if args.ffprobe == "ffprobe" and shutil.which(args.ffprobe) is None:
            bundled_ffprobe = bundled_bin / "ffprobe.exe"
            if bundled_ffprobe.is_file():
                args.ffprobe = str(bundled_ffprobe)
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
        if (
            app_data.exists()
            and any(app_data.iterdir())
            and not args.allow_existing_data_dir
        ):
            raise E2EError(
                f"Refusing to test against non-empty app data: {app_data}. "
                "Use a disposable empty directory, or pass "
                "--allow-existing-data-dir after verifying the target."
            )
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
        corrupt_media = media_dir / "corrupt-video.mp4"
        corrupt_media.write_bytes(b"not a media file\x00\xff" * 64)
        cancel_media = media_dir / "cancellation-source.mp4"
        if args.profile == "extended":
            _run_checked([
                args.ffmpeg,
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000",
                "-t", "12", "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "64k", str(cancel_media),
            ], timeout=180)
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
                    require_exact_codec=args.require_exact_codecs,
                    timeout=args.timeout,
                )
                summary["jobs"].append(result)
                actual = result.get("actual_encoder")
                if actual == codec:
                    label = f"codec={codec}"
                elif actual:
                    label = f"fallback={codec}->{actual}"
                else:
                    label = f"codec={codec} actual=unknown"
                print(f"PASS {label} output={result['size_bytes']} bytes")
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
                        require_exact_codec=args.require_exact_codecs,
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

        if args.profile == "extended":
            try:
                before_uploads = set((app_data / "uploads").glob("*")) if not args.base_url else set()
                error_status, error_payload = _multipart_error(
                    f"{runtime.base_url}/api/upload",
                    fields={"target_size_mb": "0.5", "audio_bitrate_kbps": "64"},
                    files=[("file", corrupt_media)],
                )
                if error_status != 400 or "analy" not in str(error_payload.get("detail") or "").lower():
                    raise E2EError(f"Invalid upload returned an unclear response: HTTP {error_status} {error_payload}")
                if not args.base_url:
                    after_uploads = set((app_data / "uploads").glob("*"))
                    leaked = after_uploads - before_uploads
                    if leaked:
                        raise E2EError(f"Invalid upload leaked temporary input files: {sorted(map(str, leaked))}")
                summary["invalid_upload"] = {"status": error_status, "detail": error_payload.get("detail")}
                print("PASS invalid media rejected and cleaned up")
            except (E2EError, OSError) as exc:
                summary["failures"].append({"scenario": "invalid-upload", "error": str(exc)})
                print(f"FAIL invalid upload: {exc}")

            if not args.skip_batch:
                try:
                    summary["mixed_batch"] = _run_mixed_batch(
                        runtime.base_url, generated["baseline.mp4"], corrupt_media, output_dir
                    )
                    print("PASS mixed batch preserved success/failure and safe ZIP")
                except (E2EError, KeyError, zipfile.BadZipFile) as exc:
                    summary["failures"].append({"scenario": "mixed-batch", "error": str(exc)})
                    print(f"FAIL mixed batch: {exc}")

                try:
                    duplicate_a = media_dir / "duplicate-a" / "same name.mp4"
                    duplicate_b = media_dir / "duplicate-b" / "same name.mp4"
                    duplicate_a.parent.mkdir()
                    duplicate_b.parent.mkdir()
                    shutil.copyfile(generated["baseline.mp4"], duplicate_a)
                    shutil.copyfile(generated["baseline.mp4"], duplicate_b)
                    summary["duplicate_batch"] = _run_batch(
                        runtime.base_url, [duplicate_a, duplicate_b], output_dir
                    )
                    print("PASS duplicate filenames produced collision-free ZIP entries")
                except (E2EError, OSError, zipfile.BadZipFile) as exc:
                    summary["failures"].append({"scenario": "duplicate-batch", "error": str(exc)})
                    print(f"FAIL duplicate batch: {exc}")

            if summary["jobs"]:
                try:
                    summary["sse_reconnect"] = _verify_terminal_sse_replay(
                        runtime.base_url, str(summary["jobs"][0]["task_id"])
                    )
                    print("PASS SSE reconnect replayed terminal done event")
                except (E2EError, OSError, ValueError, json.JSONDecodeError) as exc:
                    summary["failures"].append({"scenario": "sse-reconnect", "error": str(exc)})
                    print(f"FAIL SSE reconnect: {exc}")

            try:
                summary["cancellation"] = _run_cancellation_scenarios(
                    runtime.base_url,
                    cancel_media,
                    queued=(not args.base_url and args.mode == "local" and args.local_workers == 1),
                )
                label = "active and queued" if "queued" in summary["cancellation"] else "active"
                print(f"PASS {label} cancellation with no successful download")
            except (E2EError, KeyError, OSError) as exc:
                summary["failures"].append({"scenario": "cancellation", "error": str(exc)})
                print(f"FAIL cancellation: {exc}")

        if not args.base_url and summary["jobs"]:
            try:
                recovery_job = summary["jobs"][0]
                runtime.stop()
                runtime.start()
                _wait_for_health(runtime.base_url, runtime.process, timeout=90)
                history = _json_request(f"{runtime.base_url}/api/history?limit=200", timeout=15)
                entries = history.get("entries") or history.get("history") or []
                if not any(str(entry.get("task_id")) == str(recovery_job["task_id"]) for entry in entries):
                    raise E2EError(f"Completed job missing from history after restart: {history}")
                recovered = _download_after_completion(runtime.base_url, str(recovery_job["task_id"]))
                recovered_path = output_dir / f"restart-{recovery_job['task_id']}.mp4"
                recovered_path.write_bytes(recovered)
                recovered_meta = _verify_media(recovered_path, args.ffprobe)
                summary["restart_recovery"] = {
                    "task_id": recovery_job["task_id"],
                    "history_entries": len(entries),
                    "output": str(recovered_path),
                    **recovered_meta,
                }
                print("PASS history and repeated download recovered after runtime restart")
            except (E2EError, KeyError, OSError) as exc:
                summary["failures"].append({"scenario": "restart-recovery", "error": str(exc)})
                print(f"FAIL restart recovery: {exc}")
    finally:
        summary["runtime_log_tail"] = runtime.logs()
        runtime.stop()
        if temp_context is not None:
            _cleanup_temporary_context(temp_context)

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
