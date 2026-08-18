"""Runtime hardware detection and codec mapping.

The important distinction here is between an encoder being present in
``ffmpeg -encoders`` and being usable by the device passed to the container.
Every hardware candidate is therefore validated with a one-frame encode.

Linux Intel Quick Sync is initialized through the VAAPI render node. AMD
hardware uses VAAPI directly; there is no separate Linux ``amf`` path. Native
Windows AMD AMF is probed through the platform's FFmpeg encoder.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from shared.subprocess_utils import hidden_process_kwargs

from .constants import (
    AMF_ENCODERS,
    AV1_NVENC,
    AV1_QSV,
    AV1_VAAPI,
    CODEC_PRIORITY,
    CPU_ENCODERS,
    CPU_FALLBACK,
    H264_NVENC,
    H264_QSV,
    H264_VAAPI,
    HEVC_NVENC,
    HEVC_QSV,
    HEVC_VAAPI,
    HW_ENCODERS,
    LIBX264,
    LIBX265,
    QSV_ENCODERS,
    VAAPI_ENCODERS,
)

logger = logging.getLogger(__name__)

_HW_CACHE: Optional[Dict[str, Any]] = None
_HW_CACHE_LOCK = threading.RLock()


def _reset_hw_cache_lock_after_fork() -> None:
    """Do not let Celery children inherit a mutex held by startup probing.

    The worker starts encoder detection in a background thread while Celery is
    starting.  If Celery forks a pool child during that probe, a normal
    ``threading.RLock`` can be inherited in its locked state even though the
    owning thread does not exist in the child.  Any task calling
    ``get_hw_info`` would then wait forever.  The cache itself is safe to
    inherit; only the process-local lock must be recreated.
    """
    global _HW_CACHE_LOCK
    _HW_CACHE_LOCK = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_hw_cache_lock_after_fork)


def _append_vaapi_driver_paths(env: dict[str, str]) -> None:
    """Keep Intel, AMD, and other installed VAAPI drivers discoverable.

    A container may carry a source-built Intel iHD driver while Mesa provides
    AMD (radeonsi) and other VAAPI drivers in the distro directory.  A single
    Intel-only path makes the latter invisible.  Preserve an operator's
    explicit path, but append the standard runtime directories that exist.
    """
    configured = env.get("LIBVA_DRIVERS_PATH", "")
    parts = [part for part in configured.split(os.pathsep) if part]
    for path in (
        "/usr/local/lib/dri",
        "/usr/lib/x86_64-linux-gnu/dri",
        "/usr/lib/dri",
    ):
        if os.path.isdir(path) and path not in parts:
            parts.append(path)
    if parts:
        env["LIBVA_DRIVERS_PATH"] = os.pathsep.join(parts)


def get_gpu_env() -> dict[str, str]:
    """Return subprocess environment for CUDA and VAAPI probes.

    Do not force ``LIBVA_DRIVER_NAME`` here. Forcing Intel's ``iHD`` driver on
    AMD systems was the source of a historical false-positive/failure mode.
    Users can still explicitly set it in the container environment.
    """
    env = os.environ.copy()
    env.setdefault("NVIDIA_VISIBLE_DEVICES", "all")
    env.setdefault("NVIDIA_DRIVER_CAPABILITIES", "compute,video,utility")

    lib_paths = [
        "/usr/local/nvidia/lib64",
        "/usr/local/nvidia/lib",
        "/usr/local/cuda/lib64",
        "/usr/local/cuda/lib",
        "/usr/lib/wsl/lib",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu/dri",
        "/usr/lib/dri",
    ]
    existing = env.get("LD_LIBRARY_PATH", "")
    additions = [p for p in lib_paths if os.path.isdir(p)]
    if additions:
        env["LD_LIBRARY_PATH"] = ":".join(
            part for part in [existing, *additions] if part
        )
    _append_vaapi_driver_paths(env)
    return env


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value or "")


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=get_gpu_env(),
        **hidden_process_kwargs(),
    )


# ---------------------------------------------------------------------------
# VAAPI device discovery
# ---------------------------------------------------------------------------

def _device_vendor(device: str) -> str:
    """Return ``intel``, ``amd``, ``nvidia``, or ``unknown`` for a DRI node."""
    node = os.path.basename(device)
    vendor_file = f"/sys/class/drm/{node}/device/vendor"
    try:
        with open(vendor_file, "r", encoding="utf-8") as handle:
            vendor = handle.read().strip().lower()
        if vendor in {"0x8086", "8086"}:
            return "intel"
        if vendor in {"0x1002", "1002"}:
            return "amd"
        if vendor in {"0x10de", "10de"}:
            return "nvidia"
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # This is only a hint for systems without readable sysfs. It is never used
    # to claim that a device works; the FFmpeg probe remains authoritative.
    driver = os.environ.get("LIBVA_DRIVER_NAME", "").lower()
    if driver in {"ihd", "i965", "intel-media"}:
        return "intel"
    if driver in {"radeonsi", "mesa"}:
        return "amd"
    return "unknown"


def get_vaapi_devices() -> list[dict[str, str]]:
    """Discover render nodes, honoring ``VAAPI_DEVICE`` when it exists."""
    preferred = os.environ.get("VAAPI_DEVICE", "").strip()
    paths: list[str] = []
    if preferred and os.path.exists(preferred):
        paths.append(preferred)

    try:
        render_nodes = [
            name for name in os.listdir("/dev/dri") if re.fullmatch(r"renderD\d+", name)
        ]
        render_nodes.sort(key=lambda name: int(name.removeprefix("renderD")))
        paths.extend(f"/dev/dri/{name}" for name in render_nodes)
    except (FileNotFoundError, PermissionError, OSError):
        pass

    devices: list[dict[str, str]] = []
    for path in paths:
        if path in {d["path"] for d in devices} or not os.path.exists(path):
            continue
        devices.append({"path": path, "vendor": _device_vendor(path)})
    return devices


def _is_vaapi_device(device: dict[str, str]) -> bool:
    """Return whether a render node is a candidate for the Linux VAAPI path.

    NVIDIA Container Toolkit can expose an NVIDIA render node alongside the
    CUDA device files.  Its presence does not make the VAAPI encoders usable;
    probing that node produces misleading VAAPI failures on an otherwise
    healthy NVENC system.  An explicitly configured ``VAAPI_DEVICE`` remains
    probeable so an operator can validate an unusual but intentional setup.
    """
    vendor = str(device.get("vendor", "")).lower()
    if vendor in {"intel", "amd"}:
        return True
    explicit = os.environ.get("VAAPI_DEVICE", "").strip()
    return bool(explicit and explicit == str(device.get("path", "")))


def _qsv_allowed(device: dict[str, str]) -> bool:
    """QSV is only attempted on Intel render nodes, never merely on VAAPI."""
    return device.get("vendor") == "intel"


# ---------------------------------------------------------------------------
# Encoder initialization tests
# ---------------------------------------------------------------------------

def _encoder_in_list(encoder_name: str, encoder_output: str | None = None) -> bool:
    """Check whether FFmpeg exposes an encoder."""
    if encoder_output is not None:
        return encoder_name in encoder_output
    try:
        result = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=10)
        return result.returncode == 0 and encoder_name in _text(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _test_qsv(encoder_name: str, device: str) -> bool:
    """Probe QSV using the supported VAAPI → QSV device chain."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-init_hw_device", f"vaapi=va:{device}",
        "-init_hw_device", "qsv=hw@va",
        "-filter_hw_device", "hw",
        "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1:r=1",
        "-vf", "format=nv12,hwupload=extra_hw_frames=64",
        "-c:v", encoder_name,
        "-frames:v", "1",
        "-f", "null", "-",
    ]
    try:
        result = _run(cmd, timeout=25)
        if result.returncode == 0:
            logger.info("Encoder %s passed QSV initialization test on %s", encoder_name, device)
            return True
        logger.warning("QSV %s failed on %s: %s", encoder_name, device, _text(result.stderr)[:240])
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("QSV %s probe failed on %s: %s", encoder_name, device, exc)
    return False


def _test_vaapi(encoder_name: str, device: str) -> bool:
    """Probe VAAPI with an explicit render node and hardware upload."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-init_hw_device", f"vaapi=va:{device}",
        "-filter_hw_device", "va",
        "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1:r=1",
        "-vf", "format=nv12|vaapi,hwupload",
        "-c:v", encoder_name,
        "-frames:v", "1",
        "-f", "null", "-",
    ]
    try:
        result = _run(cmd, timeout=20)
        if result.returncode == 0:
            logger.info("Encoder %s passed VAAPI initialization test on %s", encoder_name, device)
            return True
        logger.warning("VAAPI %s failed on %s: %s", encoder_name, device, _text(result.stderr)[:240])
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("VAAPI %s probe failed on %s: %s", encoder_name, device, exc)
    return False


def test_encoder(encoder_name: str) -> bool:
    """Test one encoder using the first compatible VAAPI device when needed."""
    if os.name == "nt" and (encoder_name in QSV_ENCODERS or encoder_name in AMF_ENCODERS):
        return _test_windows_native_encoder(encoder_name)
    if encoder_name in QSV_ENCODERS or encoder_name in VAAPI_ENCODERS:
        devices = get_vaapi_devices()
        for device in devices:
            if encoder_name in QSV_ENCODERS and not _qsv_allowed(device):
                continue
            probe = _test_qsv if encoder_name in QSV_ENCODERS else _test_vaapi
            if probe(encoder_name, device["path"]):
                return True
        return False

    if "nvenc" not in encoder_name:
        return _encoder_in_list(encoder_name)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1:r=1",
        "-c:v", encoder_name,
        "-frames:v", "1",
        "-f", "null", "-",
    ]
    try:
        result = _run(cmd, timeout=15)
        success = result.returncode == 0
        if success:
            logger.info("Encoder %s passed NVENC initialization test", encoder_name)
        else:
            logger.warning("NVENC %s failed: %s", encoder_name, _text(result.stderr)[:240])
        return success
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("NVENC %s probe failed: %s", encoder_name, exc)
        return False


def _test_windows_native_encoder(encoder_name: str) -> bool:
    """Probe QSV/AMF with Windows-native device initialization.

    Windows FFmpeg builds commonly expose QSV through D3D11 rather than the
    Linux VAAPI render-node chain. AMF needs no explicit device flag, but it
    still gets a real one-frame encode probe instead of being trusted from the
    encoder listing alone.
    """
    attempts: list[list[str]] = [[]]
    if encoder_name in QSV_ENCODERS:
        attempts = [["-init_hw_device", "qsv=hw"], []]
    for init_flags in attempts:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *init_flags,
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1:r=1",
            "-pix_fmt", "yuv420p", "-c:v", encoder_name,
            "-frames:v", "1", "-f", "null", "-",
        ]
        try:
            result = _run(cmd, timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("Windows encoder %s probe failed: %s", encoder_name, exc)
            return False
        if result.returncode == 0:
            logger.info("Encoder %s passed Windows native initialization test", encoder_name)
            return True
        logger.debug("Windows encoder %s attempt failed: %s", encoder_name, _text(result.stderr)[:240])
    return False


def _test_encoder_on_device(encoder_name: str, device: str | None) -> bool:
    if encoder_name in QSV_ENCODERS and device:
        return _test_qsv(encoder_name, device)
    if encoder_name in VAAPI_ENCODERS and device:
        return _test_vaapi(encoder_name, device)
    return test_encoder(encoder_name)


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def _check_nvidia() -> bool:
    """Check for an NVIDIA runtime/device, not just CUDA in FFmpeg's build."""
    try:
        query = _run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            timeout=2,
        )
        if query.returncode == 0 and _text(query.stdout).strip():
            return True
        listed = _run(["nvidia-smi", "-L"], timeout=2)
        if listed.returncode == 0 and _text(listed.stdout).strip():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    try:
        hwaccels = _run(["ffmpeg", "-hide_banner", "-hwaccels"], timeout=2)
        output = (_text(hwaccels.stdout) + _text(hwaccels.stderr)).lower()
        if "cuda" in output and any(
            os.path.exists(path) for path in ("/dev/nvidiactl", "/dev/nvidia0", "/dev/dxg")
        ):
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return False


def _encoder_list() -> str:
    try:
        result = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=10)
        return _text(result.stdout) if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _choose_cpu_encoder(priority: list[str], encoder_output: str) -> str:
    """Choose a listed CPU encoder, with a deterministic build fallback."""
    last_cpu: str | None = None
    for encoder in priority:
        if encoder not in CPU_ENCODERS:
            continue
        last_cpu = encoder
        if encoder in encoder_output:
            return encoder
    # A missing/temporarily unavailable -encoders command must not remove CPU
    # support from the UI. The last entry is the least surprising fallback.
    return last_cpu or LIBX264


def detect_hw_accel() -> Dict[str, Any]:
    """Detect all working hardware encoders and their device assignments."""
    result: Dict[str, Any] = {
        "type": "cpu",
        "available_types": [],
        "available_encoders": {},
        # Kept separate from the preferred-per-family map above so the API
        # can avoid offering a CPU encoder that is not in this FFmpeg build
        # (especially useful for different native Windows FFmpeg packages).
        # The key is added only when `ffmpeg -encoders` returned usable text;
        # older/failed probes keep the legacy CPU fallback behavior.
        "tested_encoders": {},
        "encoder_devices": {},
        "vaapi_devices": get_vaapi_devices(),
        "vaapi_device": None,
        "decode_method": None,
        "upload_method": None,
        "probe_generation": uuid.uuid4().hex,
        "probe_timestamp": int(time.time()),
    }

    devices = [
        device for device in result["vaapi_devices"]
        if _is_vaapi_device(device)
    ]
    result["vaapi_devices"] = devices
    has_nvidia = _check_nvidia()
    candidates: list[tuple[str, str | None, str]] = []
    encoder_output = _encoder_list()
    if encoder_output:
        result["available_cpu_encoders"] = [
            encoder for encoder in CPU_ENCODERS if encoder in encoder_output
        ]
    if os.name == "nt":
        # Native Windows hardware paths do not have Linux /dev/dri nodes.
        if has_nvidia or any(
            encoder in encoder_output
            for encoder in (H264_NVENC, HEVC_NVENC, AV1_NVENC)
        ):
            candidates.extend(
                (encoder, None, "nvidia")
                for encoder in (H264_NVENC, HEVC_NVENC, AV1_NVENC)
                if encoder in encoder_output
            )
        candidates.extend(
            (encoder, None, "intel_qsv")
            for encoder in (H264_QSV, HEVC_QSV, AV1_QSV)
            if encoder in encoder_output
        )
        candidates.extend(
            (encoder, None, "amd_amf")
            for encoder in AMF_ENCODERS
            if encoder in encoder_output
        )
    elif has_nvidia:
        candidates.extend((encoder, None, "nvidia") for encoder in (H264_NVENC, HEVC_NVENC, AV1_NVENC))
    for device in devices:
        if _qsv_allowed(device):
            candidates.extend((encoder, device["path"], "intel_qsv") for encoder in (H264_QSV, HEVC_QSV, AV1_QSV))
        candidates.extend((encoder, device["path"], f"{device['vendor']}_vaapi") for encoder in (H264_VAAPI, HEVC_VAAPI, AV1_VAAPI))

    tested: dict[str, bool] = {}
    encoder_devices: dict[str, str] = {}
    encoder_types: dict[str, str] = {}
    for encoder, device, hw_type in candidates:
        if tested.get(encoder) is True:
            continue
        passed = _test_encoder_on_device(encoder, device)
        tested[encoder] = bool(passed)
        if passed and device:
            encoder_devices[encoder] = device
        if passed:
            encoder_types[encoder] = hw_type

    result["tested_encoders"] = tested
    result["encoder_devices"] = encoder_devices

    for family, priority in CODEC_PRIORITY.items():
        selected: str | None = None
        for encoder in priority:
            if encoder in CPU_ENCODERS:
                selected = _choose_cpu_encoder(priority, encoder_output)
                break
            if tested.get(encoder):
                selected = encoder
                break
        if selected:
            result["available_encoders"][family] = selected

    available_types: list[str] = []
    for encoder, hw_type in encoder_types.items():
        if encoder in result["available_encoders"].values() or tested.get(encoder):
            if hw_type not in available_types:
                available_types.append(hw_type)
    result["available_types"] = available_types

    selected_hardware = [
        encoder for encoder in result["available_encoders"].values()
        if encoder in HW_ENCODERS
    ]
    if any(encoder.endswith("_nvenc") for encoder in selected_hardware):
        result["type"] = "nvidia"
        result["decode_method"] = "cuda"
        result["upload_method"] = "cuda"
    elif any(encoder in QSV_ENCODERS for encoder in selected_hardware):
        result["type"] = "intel_qsv"
        result["decode_method"] = "vaapi"
        result["upload_method"] = "qsv"
    elif any(encoder in AMF_ENCODERS for encoder in selected_hardware):
        result["type"] = "amd_amf"
        result["decode_method"] = "software"
        result["upload_method"] = "amf"
    elif any(encoder in VAAPI_ENCODERS for encoder in selected_hardware):
        selected_device = next(
            (encoder_devices.get(encoder) for encoder in selected_hardware if encoder in VAAPI_ENCODERS),
            None,
        )
        vendor = next(
            (d["vendor"] for d in devices if d["path"] == selected_device),
            "unknown",
        )
        result["type"] = "amd_vaapi" if vendor == "amd" else ("intel_vaapi" if vendor == "intel" else "vaapi")
        result["decode_method"] = "vaapi"
        result["upload_method"] = "vaapi"

    # Keep the first compatible render node available even when every encoder
    # probe failed.  A transient driver init failure should not erase the
    # device path needed by an explicit startup-test rerun.  Do not retain an
    # NVIDIA render node here: CUDA/NVENC and Linux VAAPI are separate paths.
    selected_device = next(iter(encoder_devices.values()), None)
    if selected_device is None and devices:
        selected_device = devices[0]["path"]
    result["vaapi_device"] = selected_device
    return result


# ---------------------------------------------------------------------------
# Codec → hardware encoder mapping
# ---------------------------------------------------------------------------

def _base_codec(codec: str) -> str:
    value = (codec or "").lower()
    if "h264" in value or value in {"h264", "avc"}:
        return "h264"
    if "hevc" in value or "h265" in value:
        return "hevc"
    if "av1" in value:
        return "av1"
    return "h264"


def _device_for_encoder(encoder: str, hw_info: Dict[str, Any]) -> str:
    devices = hw_info.get("encoder_devices") or {}
    selected = devices.get(encoder)
    if selected:
        return str(selected)
    selected = hw_info.get("vaapi_device") or os.environ.get("VAAPI_DEVICE")
    if selected:
        return str(selected)
    discovered = get_vaapi_devices()
    return discovered[0]["path"] if discovered else "/dev/dri/renderD128"


def _hardware_mapping(encoder: str, hw_info: Dict[str, Any]) -> tuple[list[str], list[str]]:
    if encoder in QSV_ENCODERS:
        if os.name == "nt":
            # Native Windows QSV does not have a /dev/dri render node.  Let
            # FFmpeg choose the working Intel adapter by default, or honor an
            # explicitly configured DirectX adapter through the QSV-specific
            # option.  ``-hwaccel_device`` only selects a decode device and is
            # not a substitute for QSV encoder selection.
            qsv_device = os.getenv("QSV_DEVICE", "").strip()
            if qsv_device:
                return [], ["-qsv_device", qsv_device]
            return [], [
                "-init_hw_device", "qsv=hw",
                "-filter_hw_device", "hw",
            ]
        device = _device_for_encoder(encoder, hw_info)
        return [], [
            "-init_hw_device", f"vaapi=va:{device}",
            "-init_hw_device", "qsv=hw@va",
            "-filter_hw_device", "hw",
        ]
    if encoder in VAAPI_ENCODERS:
        device = _device_for_encoder(encoder, hw_info)
        return [], [
            "-init_hw_device", f"vaapi=va:{device}",
            "-filter_hw_device", "va",
        ]

    # NVENC and Windows AMF accept software frames directly.  Keep the
    # conservative pixel format/profile flags that are broadly supported by
    # current FFmpeg driver integrations.
    flags = ["-pix_fmt", "yuv420p"]
    if "h264" in encoder:
        flags += ["-profile:v", "high"]
    elif "hevc" in encoder:
        flags += ["-profile:v", "main"]
    return flags, []


def _cpu_flags(encoder: str) -> list[str]:
    if encoder == LIBX264:
        return ["-pix_fmt", "yuv420p", "-profile:v", "high"]
    return ["-pix_fmt", "yuv420p"]


def map_codec_to_hw(
    requested_codec: str,
    hw_info: Dict[str, Any],
) -> Tuple[str, list[str], list[str]]:
    """Return ``(encoder, encoder_flags, init_flags)`` for a request."""
    if requested_codec in CPU_ENCODERS:
        return requested_codec, _cpu_flags(requested_codec), []

    if requested_codec in HW_ENCODERS:
        flags, init_flags = _hardware_mapping(requested_codec, hw_info)
        return requested_codec, flags, init_flags

    base = _base_codec(requested_codec)
    encoder = (hw_info.get("available_encoders") or {}).get(base, LIBX264)
    if encoder in CPU_ENCODERS:
        return encoder, _cpu_flags(encoder), []
    flags, init_flags = _hardware_mapping(encoder, hw_info)
    return encoder, flags, init_flags


# ---------------------------------------------------------------------------
# Cached accessor and preferred codec
# ---------------------------------------------------------------------------

def get_hw_info(force_refresh: bool = False) -> Dict[str, Any]:
    """Get the worker hardware snapshot, optionally rediscovering devices.

    Normal jobs use the process snapshot.  An explicit Settings rerun must
    bypass it so driver/device changes and newly available render nodes cannot
    leave the worker using stale QSV/VAAPI results.
    """
    global _HW_CACHE
    # Keep the lock out of the slow subprocess probe.  Apart from reducing
    # contention, this prevents a Celery fork from inheriting a lock held by
    # the startup-detection thread.  The lock protects only the short cache
    # read/write operations.
    with _HW_CACHE_LOCK:
        cached = _HW_CACHE
    if cached is not None and not force_refresh:
        return cached

    detected = detect_hw_accel()
    with _HW_CACHE_LOCK:
        if force_refresh or _HW_CACHE is None:
            _HW_CACHE = detected
            logger.info(
                "Hardware detection: generation=%s type=%s vaapi_device=%s encoders=%s",
                _HW_CACHE.get("probe_generation"),
                _HW_CACHE.get("type"),
                _HW_CACHE.get("vaapi_device"),
                _HW_CACHE.get("available_encoders"),
            )
        return _HW_CACHE


def invalidate_hw_cache() -> None:
    """Invalidate the worker snapshot before a manual hardware rerun."""
    global _HW_CACHE
    with _HW_CACHE_LOCK:
        _HW_CACHE = None


def refresh_hw_info() -> Dict[str, Any]:
    """Rediscover hardware and return the new authoritative snapshot."""
    invalidate_hw_cache()
    return get_hw_info(force_refresh=True)


def choose_best_codec(
    hw_info: Dict[str, Any],
    encoder_test_cache: Optional[Dict[str, bool]] = None,
    redis_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Choose the preferred working codec using AV1 → HEVC → H.264 priority."""
    def encoder_passed(base_codec: str, encoder: str, init_flags: list[str]) -> Optional[bool]:
        if encoder_test_cache is not None:
            key = f"{encoder}:{':'.join(init_flags)}"
            if key in encoder_test_cache:
                return bool(encoder_test_cache[key])
            for candidate, value in encoder_test_cache.items():
                if candidate == encoder or candidate.startswith(f"{encoder}:"):
                    return bool(value)

        try:
            if os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}:
                # The native desktop runtime intentionally has no Redis
                # server.  Use the same process-local store as the rest of
                # the worker so codec preference lookup cannot block for the
                # Redis socket timeout during every startup refresh.
                from shared.local_runtime import get_sync_redis

                client = get_sync_redis()
            else:
                from redis import Redis as SyncRedis

                client = SyncRedis.from_url(
                    redis_url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                    decode_responses=True,
                )
            for candidate in (encoder, base_codec):
                flag = client.get(f"encoder_test:{candidate}")
                if flag is not None:
                    return str(flag) == "1"
        except Exception:
            return None
        return None

    candidates: list[tuple[str, str, list[str], list[str], bool]] = []
    for base, encoder in (hw_info.get("available_encoders") or {}).items():
        try:
            actual, flags, init_flags = map_codec_to_hw(base, hw_info)
        except Exception:
            actual, flags, init_flags = encoder, [], []
        candidates.append((base, actual, flags, init_flags, actual in HW_ENCODERS))

    if encoder_test_cache:
        for key in encoder_test_cache:
            encoder = key.split(":", 1)[0]
            base = _base_codec(encoder)
            if not any(item[1] == encoder for item in candidates):
                candidates.append((base, encoder, [], [], encoder in HW_ENCODERS))

    for base in ("av1", "hevc", "h264"):
        base_candidates = [candidate for candidate in candidates if candidate[0] == base]
        for c_base, encoder, flags, init_flags, is_hardware in base_candidates:
            if encoder_passed(c_base, encoder, init_flags) is True:
                return {
                    "base": c_base, "encoder": encoder, "hardware": is_hardware,
                    "flags": flags, "init_flags": init_flags,
                }
        for c_base, encoder, flags, init_flags, is_hardware in base_candidates:
            if is_hardware and encoder_passed(c_base, encoder, init_flags) is None:
                return {
                    "base": c_base, "encoder": encoder, "hardware": True,
                    "flags": flags, "init_flags": init_flags,
                }
        for c_base, encoder, flags, init_flags, is_hardware in base_candidates:
            if not is_hardware:
                return {
                    "base": c_base, "encoder": encoder, "hardware": False,
                    "flags": flags, "init_flags": init_flags,
                }

    encoder, flags, init_flags = map_codec_to_hw("h264", hw_info)
    return {
        "base": "h264", "encoder": encoder, "hardware": encoder in HW_ENCODERS,
        "flags": flags, "init_flags": init_flags,
    }
