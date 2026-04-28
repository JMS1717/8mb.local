"""System, hardware, codec, and diagnostics route handlers."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from ..auth import basic_auth
from ..celery_app import celery_app
from ..config import settings
from ..deps import (
    get_hw_info_cached,
    get_hw_info_fresh,
    get_system_capabilities,
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
    ver = os.getenv("APP_VERSION", "136")
    return {"version": ver}


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
    """Get available hardware acceleration info from worker."""
    try:
        info = get_hw_info_fresh(timeout=5) or get_hw_info_cached()
    except Exception:
        info = get_hw_info_cached()

    try:
        from worker.hw_detect import choose_best_codec
        preferred = choose_best_codec(info or {}, encoder_test_cache=None, redis_url=settings.REDIS_URL)
        if preferred:
            info = dict(info or {})
            info["preferred"] = preferred
    except Exception:
        pass

    return info


@router.get("/api/codecs/available")
async def get_available_codecs() -> AvailableCodecsResponse:
    """Get available codecs based on hardware detection, user settings, and encoder tests."""
    try:
        hw_info = get_hw_info_cached()

        codec_settings = settings_manager.get_codec_visibility_settings()
        
        enabled_codecs = []
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
            'libx264': codec_settings.get('libx264', True),
            'libx265': codec_settings.get('libx265', True),
            'libsvtav1': codec_settings.get('libsvtav1', True),
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
            enabled_codecs=["libx264", "libx265", "libsvtav1"],
        )


@router.get("/api/system/capabilities")
async def system_capabilities():
    """Return detailed system capabilities including CPU, memory, GPUs and worker HW type."""
    from .. import deps as _deps_mod
    if _deps_mod.SYSTEM_CAPS_CACHE is None:
        caps = get_system_capabilities()
        caps["hardware"] = get_hw_info_cached()
        _deps_mod.SYSTEM_CAPS_CACHE = caps
    return _deps_mod.SYSTEM_CAPS_CACHE


@router.get("/api/system/encoder-tests")
async def system_encoder_tests():
    """Return encoder startup test results and a simple summary."""
    try:
        hw_info = get_hw_info_cached()
    except Exception:
        hw_info = {"type": "cpu", "available_encoders": {}}

    test_codecs = [
        "h264_nvenc","hevc_nvenc","av1_nvenc",
        "h264_qsv","hevc_qsv","av1_qsv",
        "h264_vaapi","hevc_vaapi","av1_vaapi",
        "libx264","libx265","libsvtav1",
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
            
            is_hardware = actual_encoder.endswith("_nvenc") or actual_encoder.endswith("_qsv") or actual_encoder.endswith("_vaapi")
            if overall_passed and is_hardware:
                any_hw_passed = True

        hw_type = (hw_info.get("type") or "cpu").lower()
        def _matches_hw(c: str) -> bool:
            if c.startswith("lib"):
                return True
            if hw_type == "nvidia":
                return c.endswith("_nvenc")
            if hw_type == "qsv":
                return c.endswith("_qsv") or c.endswith("_vaapi")
            if hw_type == "vaapi":
                return c.endswith("_vaapi")
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
    """Trigger a fresh run of encoder/decoder startup tests on the worker and return updated results."""
    try:
        task = celery_app.send_task("worker.worker.run_hardware_tests")
        try:
            _ = task.get(timeout=90)
        except Exception:
            pass
        return await system_encoder_tests()
    except Exception as e:
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
    """Run basic GPU checks inside the container to validate NVIDIA and NVENC."""
    def run_cmd(cmd: list[str], timeout: int = 6):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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

    checks["nvidia_smi_L"] = run_cmd(["nvidia-smi", "-L"], timeout=4)
    checks["ffmpeg_hwaccels"] = run_cmd(["ffmpeg", "-hide_banner", "-hwaccels"], timeout=4)
    checks["ffmpeg_encoders"] = run_cmd(["ffmpeg", "-hide_banner", "-encoders"], timeout=6)

    # VAAPI diagnostics
    checks["vainfo"] = run_cmd(["vainfo", "--display", "drm", "--device", "/dev/dri/renderD128"], timeout=6)
    checks["dri_devices"] = run_cmd(["ls", "-la", "/dev/dri/"], timeout=2)

    nvenc_test = run_cmd([
        "ffmpeg", "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=0.1",
        "-c:v", "h264_nvenc",
        "-f", "null", "-",
    ], timeout=8)
    checks["nvenc_smoke_test"] = nvenc_test

    # VAAPI smoke test
    vaapi_test = run_cmd([
        "ffmpeg", "-hide_banner", "-v", "error",
        "-hwaccel", "vaapi", "-hwaccel_device", "/dev/dri/renderD128",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
        "-vf", "format=nv12|vaapi,hwupload",
        "-c:v", "h264_vaapi", "-frames:v", "1",
        "-f", "null", "-",
    ], timeout=10)
    checks["vaapi_smoke_test"] = vaapi_test

    summary = {
        "nvidia_device_present": any(x.get("exists") for x in checks.get("device_files", [])),
        "nvidia_smi_ok": checks["nvidia_smi_L"]["rc"] == 0 and bool(checks["nvidia_smi_L"].get("stdout")),
        "ffmpeg_sees_cuda": "cuda" in (checks["ffmpeg_hwaccels"].get("stdout", "") + checks["ffmpeg_hwaccels"].get("stderr", "")).lower(),
        "ffmpeg_sees_vaapi": "vaapi" in (checks["ffmpeg_hwaccels"].get("stdout", "") + checks["ffmpeg_hwaccels"].get("stderr", "")).lower(),
        "ffmpeg_has_nvenc": any(tok in checks["ffmpeg_encoders"].get("stdout", "") for tok in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]),
        "ffmpeg_has_qsv": any(tok in checks["ffmpeg_encoders"].get("stdout", "") for tok in ["h264_qsv", "hevc_qsv", "av1_qsv"]),
        "ffmpeg_has_vaapi": any(tok in checks["ffmpeg_encoders"].get("stdout", "") for tok in ["h264_vaapi", "hevc_vaapi", "av1_vaapi"]),
        "nvenc_encode_ok": nvenc_test["rc"] == 0 and "error" not in (nvenc_test.get("stderr", "").lower()),
        "vaapi_encode_ok": vaapi_test["rc"] == 0 and "error" not in (vaapi_test.get("stderr", "").lower()),
        "vainfo_ok": checks["vainfo"]["rc"] == 0,
        "dri_device_present": os.path.exists("/dev/dri/renderD128"),
    }

    return {"summary": summary, "checks": checks}


# ---------------------------------------------------------------------------
# FFmpeg Management (Jellyfin FFmpeg)
# ---------------------------------------------------------------------------

JELLYFIN_FFMPEG_DIR = "/usr/lib/jellyfin-ffmpeg"
JELLYFIN_REPO_BASE = "https://repo.jellyfin.org/files/ffmpeg/ubuntu/latest-7.x/amd64/"


def _get_installed_ffmpeg_version() -> dict:
    """Get currently installed Jellyfin FFmpeg version."""
    try:
        # Prefer dpkg for accurate version (ffmpeg banner shows "7.1.3-Jellyfin"
        # which the regex truncates to "7.1.3-", missing the build number like "-5")
        dpkg_ver = None
        try:
            dpkg = subprocess.run(
                ["dpkg-query", "-W", "-f", "${Version}", "jellyfin-ffmpeg7"],
                capture_output=True, text=True, timeout=5,
            )
            if dpkg.returncode == 0 and dpkg.stdout.strip():
                # Strip distro suffix: "7.1.3-5-jammy" -> "7.1.3-5"
                dpkg_ver = re.sub(r"-(jammy|focal|bullseye|bookworm)$", "", dpkg.stdout.strip())
        except Exception:
            pass

        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=5,
        )
        first_line = (result.stdout or "").split("\n")[0]

        if not dpkg_ver:
            # Fallback: parse banner, ensure trailing dash is stripped
            m = re.search(r"version\s+([\d.\-]+[0-9])", first_line)
            dpkg_ver = m.group(1) if m else "unknown"

        return {"version": dpkg_ver, "full": first_line, "path": JELLYFIN_FFMPEG_DIR}
    except Exception as e:
        return {"version": "unknown", "full": str(e), "path": JELLYFIN_FFMPEG_DIR}


def _get_latest_jellyfin_ffmpeg() -> dict:
    """Check the Jellyfin repo for the latest FFmpeg 7.x .deb filename and version."""
    import urllib.request
    try:
        req = urllib.request.Request(JELLYFIN_REPO_BASE, headers={"User-Agent": "8mb.local/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Find all .deb links like jellyfin-ffmpeg7_7.1.3-5-jammy_amd64.deb
        pattern = r'href="(jellyfin-ffmpeg7_([^"]+)-jammy_amd64\.deb)"'
        matches = re.findall(pattern, html)
        if not matches:
            return {"error": "No .deb packages found on Jellyfin repo", "url": JELLYFIN_REPO_BASE}
        # Sort by parsed numeric tuple to handle multi-digit version parts correctly
        # e.g. "7.1.10-5" > "7.1.2-5" (lexicographic sort would fail here)
        def _ver_key(m: tuple) -> tuple:
            import re as _re
            return tuple(int(x) for x in _re.split(r"[.\-]", m[1]) if x.isdigit())
        matches.sort(key=_ver_key)
        latest_filename, latest_version = matches[-1]
        return {
            "version": latest_version,
            "filename": latest_filename,
            "url": JELLYFIN_REPO_BASE + latest_filename,
        }
    except Exception as e:
        return {"error": str(e), "url": JELLYFIN_REPO_BASE}


@router.get("/api/system/ffmpeg-version")
async def ffmpeg_version():
    """Return installed FFmpeg version and check for updates."""
    installed = _get_installed_ffmpeg_version()
    latest = _get_latest_jellyfin_ffmpeg()

    update_available = False
    if "error" not in latest and installed.get("version") != "unknown":
        update_available = latest.get("version", "") != installed.get("version", "")

    return {
        "installed": installed,
        "latest": latest,
        "update_available": update_available,
    }


@router.post("/api/system/ffmpeg-update", dependencies=[Depends(basic_auth)])
async def ffmpeg_update():
    """Download and install the latest Jellyfin FFmpeg package.

    This replaces the FFmpeg binaries in-place inside the running container.
    A container restart is recommended afterwards.
    """
    latest = _get_latest_jellyfin_ffmpeg()
    if "error" in latest:
        raise HTTPException(status_code=502, detail=f"Cannot reach Jellyfin repo: {latest['error']}")

    url = latest["url"]
    deb_path = "/tmp/jellyfin-ffmpeg-update.deb"

    try:
        # Download
        dl = subprocess.run(
            ["wget", "-q", "-O", deb_path, url],
            capture_output=True, text=True, timeout=120,
        )
        if dl.returncode != 0:
            raise HTTPException(status_code=502, detail=f"Download failed: {dl.stderr[:500]}")

        # Install
        inst = subprocess.run(
            ["dpkg", "-i", deb_path],
            capture_output=True, text=True, timeout=60,
        )
        # Fix any missing deps
        if inst.returncode != 0:
            repair = subprocess.run(
                ["apt-get", "install", "-y", "-f"],
                capture_output=True, text=True, timeout=60,
            )
            if repair.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"dpkg install failed and dependency repair also failed: {repair.stderr[:400]}",
                )

        # Ensure symlinks are up-to-date
        subprocess.run(
            ["ln", "-sf", f"{JELLYFIN_FFMPEG_DIR}/ffmpeg", "/usr/local/bin/ffmpeg"],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["ln", "-sf", f"{JELLYFIN_FFMPEG_DIR}/ffprobe", "/usr/local/bin/ffprobe"],
            capture_output=True, timeout=5,
        )

        # Cleanup
        try:
            os.remove(deb_path)
        except OSError:
            pass

        new_version = _get_installed_ffmpeg_version()
        return {
            "status": "success",
            "message": f"Updated to FFmpeg {new_version['version']}. Restart recommended.",
            "installed": new_version,
            "restart_required": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        try:
            os.remove(deb_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")
