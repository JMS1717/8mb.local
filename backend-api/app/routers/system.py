"""System, hardware, codec, and diagnostics route handlers."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time

from fastapi import APIRouter, Depends, HTTPException

from shared.subprocess_utils import hidden_process_kwargs

from ..auth import basic_auth
from ..celery_app import celery_app
from ..config import settings
from ..deps import (
    get_hw_info_cached,
    get_hw_info_cached_async,
    get_hw_info_fresh,
    get_hw_info_fresh_async,
    get_system_capabilities,
    invalidate_hw_info_cache,
    redis,
    sync_codec_settings_from_tests,
)
from .. import settings_manager
from ..models import AvailableCodecsResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

_HARDWARE_ENCODERS = {
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
    "h264_qsv", "hevc_qsv", "av1_qsv",
    "h264_vaapi", "hevc_vaapi", "av1_vaapi",
    "h264_amf", "hevc_amf", "av1_amf",
}
_CPU_ENCODERS = {"libx264", "libx265", "libsvtav1"}


def _working_hardware_encoders(hw_info: dict) -> set[str]:
    """Return hardware encoders that passed an actual runtime probe.

    ``ffmpeg -encoders`` only says that an encoder was compiled in. Prefer
    the probe results when present; otherwise use the already-probed
    ``available_encoders`` map from the worker. This prevents a cold-start
    page from offering NVENC/QSV/VAAPI entries that can only fall back to
    CPU, while still working with older workers that do not expose the
    detailed probe map.
    """
    tested = hw_info.get("tested_encoders") or {}
    available = {
        str(encoder)
        for encoder in (hw_info.get("available_encoders") or {}).values()
        if str(encoder) in _HARDWARE_ENCODERS
    }
    if tested:
        return {
            str(encoder)
            for encoder, passed in tested.items()
            if passed and str(encoder) in _HARDWARE_ENCODERS
        }
    return available


def _working_cpu_encoders(hw_info: dict) -> set[str]:
    """Return CPU encoders known to exist in the worker's FFmpeg build.

    The field is optional for compatibility with older workers. If an older
    or failed hardware probe does not provide it, expose only the conservative
    x264/x265 baseline; claiming SVT-AV1 exists when the worker cannot
    enumerate it turns a visible option into a guaranteed failed job.
    """
    listed = hw_info.get("available_cpu_encoders")
    if not isinstance(listed, list):
        return {"libx264", "libx265"}
    return {str(encoder) for encoder in listed if str(encoder) in _CPU_ENCODERS}


@router.get("/healthz")
async def health():
    return {"ok": True}


@router.get("/api/version")
async def api_version():
    """Return application version baked at build time."""
    return {"version": settings.APP_VERSION}


@router.get("/api/startup/info")
async def startup_info():
    """Expose container boot id and codec sync status for lightweight UI banners."""
    try:
        boot_id = await redis.get("startup:boot_id")
        boot_ts = await redis.get("startup:boot_ts")
        synced = await redis.get("startup:codec_visibility_synced")
        synced_at = await redis.get("startup:codec_visibility_synced_at")
        return {
            "boot_id": boot_id,
            "boot_ts": int(boot_ts) if boot_ts else None,
            "codec_visibility_synced": (synced == "1"),
            "codec_visibility_synced_at": int(synced_at) if synced_at else None,
        }
    except Exception:
        return {
            "boot_id": None,
            "boot_ts": None,
            "codec_visibility_synced": False,
            "codec_visibility_synced_at": None,
        }


@router.get("/api/hardware", dependencies=[Depends(basic_auth)])
async def get_hardware_info():
    """Get available hardware acceleration info from worker.

    Uses the short-TTL cached value to avoid firing a Celery RPC on every
    page load. Forcing a fresh probe is handled by the re-run encoder tests
    endpoint, which calls ``invalidate_hw_info_cache`` after running.
    """
    logger.debug("/api/hardware called")
    try:
        info = await get_hw_info_cached_async()
    except Exception as e:
        logger.warning("/api/hardware: cached lookup failed: %s", e)
        info = {"type": "cpu", "available_encoders": {}}

    try:
        # The worker includes a preferred codec selected with its in-process
        # startup cache. Do not replace a validated choice with a fresh Redis
        # lookup that may be empty or stale during worker startup.
        preferred = (info or {}).get("preferred")
        if not preferred:
            from worker.hw_detect import choose_best_codec
            preferred = choose_best_codec(info or {}, encoder_test_cache=None, redis_url=settings.REDIS_URL)
        if preferred:
            info = dict(info or {})
            info["preferred"] = preferred
            logger.debug("/api/hardware: preferred codec = %s", preferred)
    except Exception as e:
        logger.debug("/api/hardware: choose_best_codec failed: %s", e)

    return info


@router.get("/api/codecs/available", dependencies=[Depends(basic_auth)])
async def get_available_codecs() -> AvailableCodecsResponse:
    """Get available codecs based on hardware detection, user settings, and encoder tests."""
    logger.debug("/api/codecs/available called")
    try:
        hw_info = await get_hw_info_cached_async()

        codec_settings = settings_manager.get_codec_visibility_settings()
        
        enabled_codecs = []
        working_hardware = _working_hardware_encoders(hw_info)
        working_cpu = _working_cpu_encoders(hw_info)
        codec_map = {
            'h264_nvenc': codec_settings.get('h264_nvenc', True),
            'hevc_nvenc': codec_settings.get('hevc_nvenc', True),
            'av1_nvenc': codec_settings.get('av1_nvenc', True),
            'h264_qsv': codec_settings.get('h264_qsv', True),
            'hevc_qsv': codec_settings.get('hevc_qsv', True),
            'av1_qsv': codec_settings.get('av1_qsv', True),
            'h264_vaapi': codec_settings.get('h264_vaapi', True),
            'hevc_vaapi': codec_settings.get('hevc_vaapi', True),
            'av1_vaapi': codec_settings.get('av1_vaapi', True),
            'h264_amf': codec_settings.get('h264_amf', True),
            'hevc_amf': codec_settings.get('hevc_amf', True),
            'av1_amf': codec_settings.get('av1_amf', True),
            'libx264': codec_settings.get('libx264', True),
            'libx265': codec_settings.get('libx265', True),
            'libsvtav1': codec_settings.get('libsvtav1', True),
        }
        for codec, is_enabled in codec_map.items():
            # CPU codecs are always candidates. Hardware codecs are shown
            # only after the worker has confirmed the corresponding device
            # can initialize them; the visibility setting remains an
            # operator-controlled additional filter.
            is_hardware = codec in _HARDWARE_ENCODERS
            is_available = codec in working_hardware if is_hardware else codec in working_cpu
            if is_enabled and is_available:
                enabled_codecs.append(codec)
        
        return AvailableCodecsResponse(
            hardware_type=hw_info.get("type", "cpu"),
            available_encoders=hw_info.get("available_encoders", {}),
            enabled_codecs=enabled_codecs,
        )
    except Exception as e:
        # Keep the error response truthful even when the worker is still
        # starting or unavailable.  In particular, do not advertise SVT-AV1
        # merely because it is part of the historical default settings.
        return AvailableCodecsResponse(
            hardware_type="cpu",
            available_encoders={"h264": "libx264", "hevc": "libx265"},
            enabled_codecs=["libx264", "libx265"],
        )


@router.get("/api/system/capabilities", dependencies=[Depends(basic_auth)])
async def system_capabilities():
    """Return detailed system capabilities including CPU, memory, GPUs and worker HW type."""
    from .. import deps as _deps_mod
    now = time.monotonic()
    cache_expired = (
        _deps_mod.SYSTEM_CAPS_CACHE is None
        or (now - _deps_mod.SYSTEM_CAPS_CACHE_TS) >= _deps_mod.SYSTEM_CAPS_TTL_SECONDS
    )
    if cache_expired:
        # get_system_capabilities() shells out to nvidia-smi and reads procfs;
        # offload to a thread so we don't block the event loop on cold start.
        caps = await asyncio.to_thread(get_system_capabilities)
        caps["hardware"] = await get_hw_info_cached_async()
        _deps_mod.SYSTEM_CAPS_CACHE = caps
        _deps_mod.SYSTEM_CAPS_CACHE_TS = time.monotonic()
        logger.debug("system_capabilities: cached fresh snapshot")
    return _deps_mod.SYSTEM_CAPS_CACHE


@router.get("/api/system/encoder-tests", dependencies=[Depends(basic_auth)])
async def system_encoder_tests():
    """Return encoder startup test results and a simple summary."""
    logger.debug("/api/system/encoder-tests called")
    try:
        hw_info = await get_hw_info_cached_async()
    except Exception as e:
        logger.warning("encoder-tests: cached hw_info lookup failed: %s", e)
        hw_info = {"type": "cpu", "available_encoders": {}}

    test_codecs = [
        "h264_nvenc","hevc_nvenc","av1_nvenc",
        "h264_qsv","hevc_qsv","av1_qsv",
        "h264_vaapi","hevc_vaapi","av1_vaapi",
        "h264_amf","hevc_amf","av1_amf",
        "libx264","libx265","libsvtav1",
    ]

    results = []
    any_hw_passed = False
    # Docker persists detailed startup-test results in Redis. The native
    # runtime has no Redis server; its worker probe returns the authoritative
    # per-encoder result in ``tested_encoders`` instead.
    detected_tests = hw_info.get("tested_encoders") or {}
    available_cpu = set(hw_info.get("available_cpu_encoders") or [])
    # A non-empty runtime probe map is authoritative for this boot. Redis
    # retains diagnostics for a long TTL, so reusing an old result for a
    # codec that is no longer present (or has just failed) can show stale
    # VAAPI/QSV/NVENC status after a driver or container change.
    runtime_probe_authoritative = bool(detected_tests)
    try:
        for codec in test_codecs:
            encode_detail_raw = await redis.get(f"encoder_test_json:{codec}")
            encode_passed = False
            encode_msg = "Unknown"
            actual_encoder = codec
            persisted_result = False

            if runtime_probe_authoritative and codec in _HARDWARE_ENCODERS:
                # Redis may contain a result from another GPU, driver, or
                # container boot. The current worker probe is authoritative.
                encode_detail_raw = None
            elif encode_detail_raw:
                persisted_result = True
                try:
                    encode_detail = json.loads(encode_detail_raw)
                    encode_passed = bool(encode_detail.get("passed"))
                    encode_msg = encode_detail.get("message") or (
                        "OK" if encode_passed else "Failed"
                    )
                    actual_encoder = encode_detail.get("actual_encoder", codec)
                except Exception:
                    pass
            else:
                flag = await redis.get(f"encoder_test:{codec}")
                if flag is not None:
                    persisted_result = True
                    encode_passed = str(flag) == "1"
                    encode_msg = "OK" if encode_passed else "Failed"

            if not persisted_result:
                if codec in detected_tests:
                    encode_passed = bool(detected_tests[codec])
                    encode_msg = (
                        "OK (runtime probe)"
                        if encode_passed
                        else "Hardware initialization failed"
                    )
                elif codec in _CPU_ENCODERS and codec in available_cpu:
                    encode_passed = True
                    encode_msg = "Available in FFmpeg build"
                else:
                    # This codec was neither probed nor listed by the active
                    # FFmpeg build. It is not a failure of the machine.
                    encode_passed = None
                    encode_msg = "Not available or not tested"

            decode_detail_raw = await redis.get(f"encoder_test_decode_json:{codec}")
            if (
                runtime_probe_authoritative
                and codec in _HARDWARE_ENCODERS
                and codec not in detected_tests
            ):
                decode_detail_raw = None
            decode_passed = None
            decode_msg = None
            if decode_detail_raw:
                try:
                    decode_detail = json.loads(decode_detail_raw)
                    decode_passed = bool(decode_detail.get("passed"))
                    decode_msg = decode_detail.get("message") or (
                        "OK" if decode_passed else "Failed"
                    )
                except Exception:
                    pass

            overall_passed = (
                None
                if encode_passed is None
                else encode_passed and (decode_passed is None or decode_passed)
            )

            results.append({
                "codec": codec,
                "actual_encoder": actual_encoder,
                "passed": overall_passed,
                "encode_passed": encode_passed,
                "encode_message": encode_msg,
                "decode_passed": decode_passed,
                "decode_message": decode_msg,
            })

            is_hardware = actual_encoder.endswith(("_nvenc", "_qsv", "_vaapi", "_amf"))
            if overall_passed is True and is_hardware:
                any_hw_passed = True

        return {
            "hardware_type": hw_info.get("type", "cpu"),
            "any_hardware_passed": any_hw_passed,
            # Mixed systems are valid (e.g. an NVIDIA dGPU plus Intel iGPU).
            # Returning all tested results avoids silently hiding a working
            # encoder because ``type`` represents only the preferred family.
            "results": results,
        }
    except Exception as e:
        logger.warning(f"encoder-tests endpoint error: {e}")
        return {
            "hardware_type": hw_info.get("type", "cpu"),
            "any_hardware_passed": False,
            "results": [],
        }


@router.post("/api/system/encoder-tests/rerun", dependencies=[Depends(basic_auth)])
async def rerun_encoder_tests():
    """Trigger a fresh run of encoder/decoder startup tests on the worker and return updated results.

    Offloads the blocking ``task.get(timeout=90)`` to a thread so the API
    worker's event loop remains responsive while the ~minute-long hardware
    validation runs on the Celery worker.
    """
    logger.info("encoder-tests/rerun: dispatching worker.run_hardware_tests")
    try:
        task = celery_app.send_task("worker.worker.run_hardware_tests")

        def _wait() -> None:
            try:
                task.get(timeout=90)
            except Exception as e:
                logger.warning("rerun_encoder_tests: task.get raised: %s", e)

        await asyncio.to_thread(_wait)
        invalidate_hw_info_cache()
        logger.info("encoder-tests/rerun: completed")
        return await system_encoder_tests()
    except Exception as e:
        logger.error("encoder-tests/rerun failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/codecs/sync-from-hardware", dependencies=[Depends(basic_auth)])
async def sync_codecs_from_hardware():
    """Manually trigger a codec visibility sync based on detected hardware."""
    try:
        await sync_codec_settings_from_tests(timeout_s=15)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/diagnostics/gpu", dependencies=[Depends(basic_auth)])
async def gpu_diagnostics():
    """Run one-frame NVIDIA, QSV, and VAAPI checks inside the container.

    All subprocess work is executed inside ``asyncio.to_thread`` so the API
    event loop continues serving SSE progress streams while the checks run.
    """
    logger.debug("/api/diagnostics/gpu called")

    def run_cmd(cmd: list[str], timeout: int = 6):
        try:
            p = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                **hidden_process_kwargs(),
            )
            logger.debug(
                "diagnostics cmd=%s rc=%s stderr_len=%d",
                cmd[0], p.returncode, len(p.stderr or ""),
            )
            return {
                "cmd": " ".join(cmd),
                "rc": p.returncode,
                "stdout": (p.stdout or "")[-4000:],
                "stderr": (p.stderr or "")[-4000:],
            }
        except FileNotFoundError:
            return {"cmd": " ".join(cmd), "rc": 127, "stdout": "", "stderr": "command not found"}
        except subprocess.TimeoutExpired:
            return {"cmd": " ".join(cmd), "rc": 124, "stdout": "", "stderr": "timeout"}
        except Exception as e:
            return {"cmd": " ".join(cmd), "rc": 1, "stdout": "", "stderr": str(e)}

    async def run_async(cmd: list[str], timeout: int = 6):
        return await asyncio.to_thread(run_cmd, cmd, timeout)

    render_nodes: list[str] = []
    preferred_device = os.environ.get("VAAPI_DEVICE", "").strip()
    if preferred_device and os.path.exists(preferred_device):
        render_nodes.append(preferred_device)
    try:
        render_nodes.extend(
            f"/dev/dri/{name}"
            for name in sorted(os.listdir("/dev/dri"))
            if name.startswith("renderD")
        )
    except (FileNotFoundError, PermissionError, OSError):
        pass
    render_nodes = list(dict.fromkeys(render_nodes))
    vaapi_device = render_nodes[0] if render_nodes else None
    checks: dict = {"dri_devices": {"paths": render_nodes, "exists": bool(render_nodes)}}

    try:
        devs = []
        for d in ("/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-modeset"):
            try:
                devs.append({"path": d, "exists": os.path.exists(d)})
            except Exception:
                devs.append({"path": d, "exists": False})
        checks["device_files"] = devs
    except Exception:
        checks["device_files"] = []

    vaapi_smoke = ["true"]
    qsv_smoke = ["true"]
    amf_smoke = ["true"]
    vainfo_cmd = ["true"]
    if vaapi_device:
        vainfo_cmd = ["vainfo", "--display", "drm", "--device", vaapi_device]
        vaapi_smoke = [
            "ffmpeg", "-hide_banner", "-v", "error",
            "-init_hw_device", f"vaapi=va:{vaapi_device}",
            "-filter_hw_device", "va",
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
            "-vf", "format=nv12|vaapi,hwupload", "-c:v", "h264_vaapi",
            "-frames:v", "1", "-f", "null", "-",
        ]
        qsv_smoke = [
            "ffmpeg", "-hide_banner", "-v", "error",
            "-init_hw_device", f"vaapi=va:{vaapi_device}",
            "-init_hw_device", "qsv=hw@va", "-filter_hw_device", "hw",
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
            "-vf", "format=nv12,hwupload", "-c:v", "h264_qsv",
            "-frames:v", "1", "-f", "null", "-",
        ]
    if os.name == "nt":
        amf_smoke = [
            "ffmpeg", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
            "-c:v", "h264_amf", "-frames:v", "1", "-f", "null", "-",
        ]

    # Each subprocess is offloaded so diagnostics cannot block SSE or upload
    # requests while a driver probe is waiting.
    (
        checks["nvidia_smi_L"],
        checks["ffmpeg_hwaccels"],
        checks["ffmpeg_encoders"],
        checks["vainfo"],
        checks["nvenc_smoke_test"],
        checks["vaapi_smoke_test"],
        checks["qsv_smoke_test"],
        checks["amf_smoke_test"],
    ) = await asyncio.gather(
        run_async(["nvidia-smi", "-L"], 4),
        run_async(["ffmpeg", "-hide_banner", "-hwaccels"], 4),
        run_async(["ffmpeg", "-hide_banner", "-encoders"], 6),
        run_async(vainfo_cmd, 6),
        run_async([
            "ffmpeg", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=0.1",
            "-c:v", "h264_nvenc", "-frames:v", "1", "-f", "null", "-",
        ], 8),
        run_async(vaapi_smoke, 10),
        run_async(qsv_smoke, 10),
        run_async(amf_smoke, 10),
    )

    hwaccel_text = checks["ffmpeg_hwaccels"].get("stdout", "") + checks["ffmpeg_hwaccels"].get("stderr", "")
    encoder_text = checks["ffmpeg_encoders"].get("stdout", "") + checks["ffmpeg_encoders"].get("stderr", "")

    def smoke_ok(check: dict) -> bool:
        return check.get("rc") == 0 and "error" not in check.get("stderr", "").lower()

    summary = {
        "nvidia_device_present": any(x.get("exists") for x in checks.get("device_files", [])),
        "nvidia_smi_ok": checks["nvidia_smi_L"]["rc"] == 0 and bool(checks["nvidia_smi_L"].get("stdout")),
        "ffmpeg_sees_cuda": "cuda" in hwaccel_text.lower(),
        "ffmpeg_sees_vaapi": "vaapi" in hwaccel_text.lower(),
        "ffmpeg_has_nvenc": any(tok in encoder_text for tok in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]),
        "ffmpeg_has_qsv": any(tok in encoder_text for tok in ["h264_qsv", "hevc_qsv", "av1_qsv"]),
        "ffmpeg_has_vaapi": any(tok in encoder_text for tok in ["h264_vaapi", "hevc_vaapi", "av1_vaapi"]),
        "ffmpeg_has_amf": any(tok in encoder_text for tok in ["h264_amf", "hevc_amf", "av1_amf"]),
        "nvenc_encode_ok": smoke_ok(checks["nvenc_smoke_test"]),
        "vaapi_encode_ok": bool(vaapi_device) and smoke_ok(checks["vaapi_smoke_test"]),
        "qsv_encode_ok": bool(vaapi_device) and smoke_ok(checks["qsv_smoke_test"]),
        "amf_encode_ok": os.name == "nt" and smoke_ok(checks["amf_smoke_test"]),
        "vainfo_ok": bool(vaapi_device) and checks["vainfo"].get("rc") == 0,
        "dri_device_present": bool(vaapi_device),
    }

    return {"summary": summary, "checks": checks}
