"""System, hardware, codec, and diagnostics route handlers."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException

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


@router.get("/api/hardware")
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
        from worker.hw_detect import choose_best_codec
        preferred = choose_best_codec(info or {}, encoder_test_cache=None, redis_url=settings.REDIS_URL)
        if preferred:
            info = dict(info or {})
            info["preferred"] = preferred
            logger.debug("/api/hardware: preferred codec = %s", preferred)
    except Exception as e:
        logger.debug("/api/hardware: choose_best_codec failed: %s", e)

    return info


@router.get("/api/codecs/available")
async def get_available_codecs() -> AvailableCodecsResponse:
    """Get available codecs based on hardware detection, user settings, and encoder tests."""
    logger.debug("/api/codecs/available called")
    try:
        hw_info = await get_hw_info_cached_async()

        codec_settings = settings_manager.get_codec_visibility_settings()
        
        enabled_codecs = []
        codec_map = {
            'h264_nvenc': codec_settings.get('h264_nvenc', True),
            'hevc_nvenc': codec_settings.get('hevc_nvenc', True),
            'av1_nvenc': codec_settings.get('av1_nvenc', True),
            'libx264': codec_settings.get('libx264', True),
            'libx265': codec_settings.get('libx265', True),
            'libsvtav1': codec_settings.get('libsvtav1', True),
            'libaom-av1': codec_settings.get('libaom_av1', True),
        }
        for codec, is_enabled in codec_map.items():
            if is_enabled:
                enabled_codecs.append(codec)

        try:
            avail_map = hw_info.get("available_encoders", {}) or {}
            for enc in avail_map.values():
                if enc not in enabled_codecs:
                    enabled_codecs.append(enc)
        except Exception:
            pass
        
        return AvailableCodecsResponse(
            hardware_type=hw_info.get("type", "cpu"),
            available_encoders=hw_info.get("available_encoders", {}),
            enabled_codecs=enabled_codecs,
        )
    except Exception as e:
        return AvailableCodecsResponse(
            hardware_type="cpu",
            available_encoders={"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1"},
            enabled_codecs=["libx264", "libx265", "libsvtav1", "libaom-av1"],
        )


@router.get("/api/system/capabilities")
async def system_capabilities():
    """Return detailed system capabilities including CPU, memory, GPUs and worker HW type."""
    from .. import deps as _deps_mod
    if _deps_mod.SYSTEM_CAPS_CACHE is None:
        # get_system_capabilities() shells out to nvidia-smi and reads procfs;
        # offload to a thread so we don't block the event loop on cold start.
        caps = await asyncio.to_thread(get_system_capabilities)
        caps["hardware"] = await get_hw_info_cached_async()
        _deps_mod.SYSTEM_CAPS_CACHE = caps
        logger.debug("system_capabilities: cached fresh snapshot")
    return _deps_mod.SYSTEM_CAPS_CACHE


@router.get("/api/system/encoder-tests")
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
        "libx264","libx265","libsvtav1","libaom-av1",
    ]

    results = []
    any_hw_passed = False
    try:
        for codec in test_codecs:
            encode_detail_raw = await redis.get(f"encoder_test_json:{codec}")
            encode_passed = False
            encode_msg = "Unknown"
            actual_encoder = codec
            
            if encode_detail_raw:
                try:
                    encode_detail = json.loads(encode_detail_raw)
                    encode_passed = bool(encode_detail.get("passed"))
                    encode_msg = encode_detail.get("message") or ("OK" if encode_passed else "Failed")
                    actual_encoder = encode_detail.get("actual_encoder", codec)
                except Exception:
                    pass
            else:
                flag = await redis.get(f"encoder_test:{codec}")
                if flag is not None:
                    encode_passed = (str(flag) == "1")
                    encode_msg = "OK" if encode_passed else "Failed"
            
            decode_detail_raw = await redis.get(f"encoder_test_decode_json:{codec}")
            decode_passed = None
            decode_msg = None
            
            if decode_detail_raw:
                try:
                    decode_detail = json.loads(decode_detail_raw)
                    decode_passed = bool(decode_detail.get("passed"))
                    decode_msg = decode_detail.get("message") or ("OK" if decode_passed else "Failed")
                except Exception:
                    pass
            
            overall_passed = encode_passed and (decode_passed is None or decode_passed)
            
            results.append({
                "codec": codec,
                "actual_encoder": actual_encoder,
                "passed": overall_passed,
                "encode_passed": encode_passed,
                "encode_message": encode_msg,
                "decode_passed": decode_passed,
                "decode_message": decode_msg,
            })
            
            is_hardware = actual_encoder.endswith("_nvenc")
            if overall_passed and is_hardware:
                any_hw_passed = True

        hw_type = (hw_info.get("type") or "cpu").lower()
        def _matches_hw(c: str) -> bool:
            if c.startswith("lib"):
                return True
            if hw_type == "nvidia":
                return c.endswith("_nvenc")
            return False
        filtered = [r for r in results if _matches_hw(r["codec"])]

        return {
            "hardware_type": hw_info.get("type", "cpu"),
            "any_hardware_passed": any_hw_passed,
            "results": filtered or results,
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
    """Run basic GPU checks inside the container to validate NVIDIA and NVENC.

    All subprocess work is executed inside ``asyncio.to_thread`` so the API
    event loop continues serving SSE progress streams while the checks run.
    """
    logger.debug("/api/diagnostics/gpu called")

    def run_cmd(cmd: list[str], timeout: int = 6):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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

    checks: dict = {}

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

    # Run the checks concurrently; each lives in a thread so they don't stall
    # the event loop collectively.
    (
        checks["nvidia_smi_L"],
        checks["ffmpeg_hwaccels"],
        checks["ffmpeg_encoders"],
        nvenc_test,
    ) = await asyncio.gather(
        run_async(["nvidia-smi", "-L"], 4),
        run_async(["ffmpeg", "-hide_banner", "-hwaccels"], 4),
        run_async(["ffmpeg", "-hide_banner", "-encoders"], 6),
        run_async([
            "ffmpeg", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=0.1",
            "-c:v", "h264_nvenc",
            "-f", "null", "-",
        ], 8),
    )
    checks["nvenc_smoke_test"] = nvenc_test

    summary = {
        "nvidia_device_present": any(x.get("exists") for x in checks.get("device_files", [])),
        "nvidia_smi_ok": checks["nvidia_smi_L"]["rc"] == 0 and bool(checks["nvidia_smi_L"].get("stdout")),
        "ffmpeg_sees_cuda": "cuda" in (checks["ffmpeg_hwaccels"].get("stdout", "") + checks["ffmpeg_hwaccels"].get("stderr", "")).lower(),
        "ffmpeg_has_nvenc": any(tok in checks["ffmpeg_encoders"].get("stdout", "") for tok in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]),
        "nvenc_encode_ok": nvenc_test["rc"] == 0 and "error" not in (nvenc_test.get("stderr", "").lower()),
    }

    return {"summary": summary, "checks": checks}
