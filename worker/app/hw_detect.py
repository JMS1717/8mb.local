"""Hardware acceleration detection and codec mapping."""

import os
import subprocess
from typing import Any, Dict, Optional

# Cache hardware detection result to avoid repeated subprocess calls
_HW_CACHE: Optional[Dict] = None


def detect_hw_accel() -> Dict[str, Any]:
    """
    Detect available hardware acceleration.
    Returns dict with: type (nvidia/intel/amd/cpu), encoders available, etc.
    """
    # Start with CPU defaults
    result: Dict[str, Any] = {
        "type": "cpu",
        "available_encoders": {},
        "decode_method": None,
        "upload_method": None,
        "vaapi_device": None,
    }

    # Check for NVIDIA first (NVENC/NVDEC)
    if _check_nvidia():
        result.update(
            {
                "type": "nvidia",
                "decode_method": "cuda",
                "available_encoders": {
                    "h264": "h264_nvenc",
                    "hevc": "hevc_nvenc",
                    "av1": "av1_nvenc",
                },
            }
        )
        return result

    # Intel Quick Sync Video
    # Check VAAPI first - QSV requires VAAPI as backend on Linux
    vaapi_info = _check_vaapi()
    qsv_available, qsv_device = _check_intel_qsv(vaapi_info.get("device"))

    if qsv_available:
        result.update(
            {
                "type": "intel",
                "decode_method": "qsv",
                "vaapi_device": qsv_device,  # Store VAAPI device for QSV initialization
                "available_encoders": {
                    "h264": "h264_qsv",
                    "hevc": "hevc_qsv",
                    "av1": "av1_qsv",
                },
            }
        )
        return result

    if vaapi_info.get("available"):
        result.update(
            {
                "type": vaapi_info.get("vendor", "unknown"),
                "decode_method": "vaapi",
                "vaapi_device": vaapi_info.get("device"),
                "available_encoders": {
                    "h264": "h264_vaapi",
                    "hevc": "hevc_vaapi",
                },
            }
        )
        if vaapi_info.get("av1_supported"):
            result["available_encoders"]["av1"] = "av1_vaapi"
        return result

    # CPU fallback encoders
    result["available_encoders"] = {
        "h264": "libx264",
        "hevc": "libx265",
        "av1": "libaom-av1",
    }
    return result


def _check_nvidia() -> bool:
    """Check if NVIDIA GPU is available."""
    try:
        # Prefer querying GPU list; treat successful return as presence in constrained envs
        q = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if q.returncode == 0:
            names = [l.strip() for l in (q.stdout or "").splitlines() if l.strip()]
            if len(names) > 0:
                return True
            # Some environments (mocked/tests or restricted containers) may return success with no output
            # Consider NVIDIA present if nvidia-smi responds successfully
            return True
        # Fallback: list mode
        l = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=2
        )
        if l.returncode == 0 and (l.stdout or "").strip():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check for CUDA capability via ffmpeg, but require device nodes to avoid false positives
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if "cuda" in result.stdout.lower():
            # Validate device nodes typical for NVIDIA/WSL GPU
            import os

            if (
                os.path.exists("/dev/nvidiactl")
                or os.path.exists("/dev/nvidia0")
                or os.path.exists("/dev/dxg")
            ):
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return False


def _check_intel_qsv(vaapi_device: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Check if Intel QSV is available.

    QSV on Linux requires VAAPI as a backend, so we initialize VAAPI first then derive QSV.
    Returns (available: bool, device_path: str | None).

    Notes:
    - Requires access to /dev/dri on Linux hosts. Under WSL2, /dev/dri is not exposed
      to Linux containers, so QSV should be considered unavailable to avoid confusing
      initialization errors (e.g., "Function not implemented").
    """
    # Require a DRI render node to be present
    try:
        import glob

        render_nodes = glob.glob("/dev/dri/renderD*")
        if not render_nodes:
            # If this is WSL (or any env) without /dev/dri, QSV cannot work
            return (False, None)
        # Use first render node or provided VAAPI device
        device = vaapi_device or render_nodes[0]
    except Exception:
        # If we can't check, proceed with ffmpeg probes below
        device = "/dev/dri/renderD128"  # Default fallback

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if "qsv" in result.stdout.lower():
            # Verify encoder is available
            encoders = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if "h264_qsv" in encoders.stdout:
                # Test QSV initialization with VAAPI backend
                # QSV on Linux needs VAAPI initialized first, then derive QSV from it
                try:
                    test_result = subprocess.run(
                        [
                            "ffmpeg",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-init_hw_device",
                            f"vaapi=va:{device}",
                            "-init_hw_device",
                            "qsv=hw@va",
                            "-f",
                            "lavfi",
                            "-i",
                            "nullsrc=s=64x64:d=0.1",
                            "-frames:v",
                            "1",
                            "-c:v",
                            "h264_qsv",
                            "-f",
                            "null",
                            "-",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    # QSV initialization succeeded only if return code is 0
                    if test_result.returncode == 0:
                        return (True, device)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
                return (False, None)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return (False, None)


def _check_vaapi() -> Dict[str, Any]:
    """Check if VAAPI is available (Intel/AMD on Linux)."""
    result = {
        "available": False,
        "vendor": "unknown",
        "device": "/dev/dri/renderD128",
        "av1_supported": False,
    }

    try:
        # VAAPI requires a render node; if missing, bail early
        import glob

        render_nodes = glob.glob("/dev/dri/renderD*")
        if not render_nodes:
            return result

        # Check for VAAPI hwaccel
        hwaccels = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if "vaapi" not in hwaccels.stdout.lower():
            return result

        # Check for VAAPI encoders
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if "h264_vaapi" not in encoders.stdout:
            return result

        result["available"] = True

        # Check for AV1 VAAPI support
        if "av1_vaapi" in encoders.stdout:
            result["av1_supported"] = True

        # Try to detect vendor (Intel vs AMD) via device info
        # Check for multiple render nodes
        if render_nodes:
            result["device"] = render_nodes[0]

        # Attempt to identify vendor via multiple methods
        # Method 1: Check DRM device uevent files
        try:
            for render_node in render_nodes:
                # Extract card number from renderD128 -> card0
                device_name = os.path.basename(render_node)
                # Try to find the corresponding card path
                card_paths = [
                    f"/sys/class/drm/{device_name}/device/uevent",
                    "/sys/class/drm/card0/device/uevent",
                    "/sys/class/drm/card1/device/uevent",
                ]
                for card_path in card_paths:
                    if os.path.exists(card_path):
                        with open(card_path, "r") as f:
                            content = f.read().lower()
                            if "pci:v00008086" in content or "intel" in content:
                                result["vendor"] = "intel"
                                break
                            elif (
                                "pci:v00001002" in content
                                or "amd" in content
                                or "radeon" in content
                            ):
                                result["vendor"] = "amd"
                                break
                if result["vendor"] != "unknown":
                    break
        except Exception:
            pass

        # Method 2: Try vainfo if available
        if result["vendor"] == "unknown":
            try:
                vainfo = subprocess.run(
                    ["vainfo", "--display", "drm", "--device", result["device"]],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                output = vainfo.stdout.lower() + vainfo.stderr.lower()
                if "intel" in output:
                    result["vendor"] = "intel"
                elif "amd" in output or "radeon" in output:
                    result["vendor"] = "amd"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Method 3: Fallback to lspci
        if result["vendor"] == "unknown":
            try:
                lspci = subprocess.run(
                    ["lspci"], capture_output=True, text=True, timeout=2
                )
                output = lspci.stdout.lower()
                if "intel" in output and "vga" in output:
                    result["vendor"] = "intel"
                elif ("amd" in output or "radeon" in output) and "vga" in output:
                    result["vendor"] = "amd"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        return result

    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return result


def map_codec_to_hw(requested_codec: str, hw_info: Dict) -> tuple[str, list, list]:
    """
    Map user-requested codec to appropriate hardware encoder.
    Returns: (encoder_name, extra_flags, init_hw_flags)
    init_hw_flags are used before -i for hardware decode/upload setup
    """
    # If user explicitly requested a CPU encoder, honor it
    if requested_codec in ("libx264", "libx265", "libsvtav1", "libaom-av1"):
        encoder = requested_codec if requested_codec != "libsvtav1" else "libaom-av1"
        flags: list[str] = []
        init_flags: list[str] = []
        if encoder == "libx264":
            flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
        elif encoder == "libx265":
            flags = ["-pix_fmt", "yuv420p"]
        return encoder, flags, init_flags

    # If user explicitly requested a specific hardware encoder, honor it
    # (e.g., h264_nvenc, hevc_amf, av1_vaapi, etc.)
    if requested_codec in (
        "h264_nvenc",
        "hevc_nvenc",
        "av1_nvenc",
        "h264_qsv",
        "hevc_qsv",
        "av1_qsv",
        "h264_vaapi",
        "hevc_vaapi",
        "av1_vaapi",
    ):
        encoder = requested_codec
        flags = []
        init_flags = []

        # Add hardware-specific flags based on encoder type
        if encoder.endswith("_nvenc"):
            # Keep pix_fmt; decide on hardware decode in worker based on input codec support
            flags = ["-pix_fmt", "yuv420p"]
            if "h264" in encoder:
                flags += ["-profile:v", "high"]
            elif "hevc" in encoder:
                flags += ["-profile:v", "main"]
        elif encoder.endswith("_qsv"):
            # QSV on Linux requires VAAPI as backend
            # Initialize VAAPI device first, then derive QSV from it
            vaapi_device = hw_info.get("vaapi_device") or "/dev/dri/renderD128"
            init_flags = [
                "-init_hw_device",
                f"vaapi=va:{vaapi_device}",
                "-init_hw_device",
                "qsv=hw@va",
                "-hwaccel",
                "qsv",
                "-hwaccel_output_format",
                "qsv",
            ]
            flags = ["-pix_fmt", "nv12"]
            if "h264" in encoder:
                flags += ["-profile:v", "high"]
        elif encoder.endswith("_vaapi"):
            vaapi_device = hw_info.get("vaapi_device") or "/dev/dri/renderD128"
            init_flags = [
                "-init_hw_device",
                f"vaapi=va:{vaapi_device}",
                "-hwaccel",
                "vaapi",
                "-hwaccel_output_format",
                "vaapi",
                "-hwaccel_device",
                "va",
            ]
            flags = ["-vf", "format=nv12|vaapi,hwupload"]

        return encoder, flags, init_flags

    # Legacy fallback: extract base codec and use hardware detection
    if "h264" in requested_codec:
        base = "h264"
    elif "hevc" in requested_codec or "h265" in requested_codec:
        base = "hevc"
    elif "av1" in requested_codec:
        base = "av1"
    else:
        base = "h264"

    encoder = hw_info["available_encoders"].get(base, "libx264")
    flags = []
    init_flags = []

    # Add hardware-specific flags
    if encoder.endswith("_nvenc"):
        # Decide on hardware decode in worker based on input codec support
        flags = ["-pix_fmt", "yuv420p"]
        if base == "h264":
            flags += ["-profile:v", "high"]
        elif base == "hevc":
            flags += ["-profile:v", "main"]
    elif encoder.endswith("_qsv"):
        # QSV on Linux requires VAAPI as backend
        # Initialize VAAPI device first, then derive QSV from it
        vaapi_device = hw_info.get("vaapi_device") or "/dev/dri/renderD128"
        init_flags = [
            "-init_hw_device",
            f"vaapi=va:{vaapi_device}",
            "-init_hw_device",
            "qsv=hw@va",
            "-hwaccel",
            "qsv",
            "-hwaccel_output_format",
            "qsv",
        ]
        flags = ["-pix_fmt", "nv12"]
        if base == "h264":
            flags += ["-profile:v", "high"]
    elif encoder.endswith("_vaapi"):
        vaapi_device = hw_info.get("vaapi_device") or "/dev/dri/renderD128"
        init_flags = [
            "-init_hw_device",
            f"vaapi=va:{vaapi_device}",
            "-hwaccel",
            "vaapi",
            "-hwaccel_output_format",
            "vaapi",
            "-hwaccel_device",
            "va",
        ]
        flags = ["-vf", "format=nv12|vaapi,hwupload"]
    elif encoder == "libx264":
        flags = ["-pix_fmt", "yuv420p", "-profile:v", "high"]
    elif encoder == "libx265":
        flags = ["-pix_fmt", "yuv420p"]

    return encoder, flags, init_flags


# Cache hardware detection on module load
_HW_INFO = None


def get_hw_info() -> Dict:
    """Get cached hardware info."""
    global _HW_INFO
    if _HW_INFO is None:
        _HW_INFO = detect_hw_accel()
    return _HW_INFO


def choose_best_codec(
    hw_info: Dict,
    encoder_test_cache: Dict[str, bool] | None = None,
    redis_url: str | None = None,
) -> Dict:
    """
    Choose the preferred codec/encoder using priority:
      1) Hardware AV1 > HEVC > H264 (only if startup tests indicate pass)
      2) CPU AV1 > HEVC > H264

    Prefers in-process `encoder_test_cache` results, falls back to Redis keys
    created by startup tests (e.g., `encoder_test:av1_nvenc`). If no test
    information is available, hardware presence from `hw_info` is used.

    Returns: {"base": <av1|hevc|h264>, "encoder": <encoder_name>, "hardware": bool,
              "flags": [...], "init_flags": [...]}.
    """
    hw_priority = ["av1", "hevc", "h264"]

    def _encoder_passed(
        base_codec: str, encoder_name: str, init_flags: list[str]
    ) -> bool | None:
        # 1) exact in-process cache lookup
        if encoder_test_cache is not None:
            cache_key = f"{encoder_name}:{':'.join(init_flags)}"
            if cache_key in encoder_test_cache:
                return bool(encoder_test_cache[cache_key])
            # fallback: any cache key that starts with encoder_name:
            for k, v in encoder_test_cache.items():
                if k.startswith(f"{encoder_name}:") or k == encoder_name:
                    return bool(v)

        # 2) Redis lookup for several likely key forms
        try:
            from redis import Redis

            redis_client = Redis.from_url(
                redis_url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                decode_responses=True,
            )
            candidates = [encoder_name, base_codec]
            # Also check common explicit test names used during startup (e.g. av1_nvenc / libaom-av1)
            for cand in candidates:
                try:
                    flag = redis_client.get(f"encoder_test:{cand}")
                    if flag is not None:
                        return str(flag) == "1"
                except Exception:
                    continue
        except Exception:
            # Redis unavailable: unknown
            return None

        return None

    # Build candidate list from hw_info and encoder_test_cache
    candidates: list[tuple[str, str, list[str], list[str], bool]] = []

    # From hw_info.available_encoders
    for base, enc in (hw_info.get("available_encoders", {}) or {}).items():
        try:
            encoder_name, flags, init_flags = map_codec_to_hw(base, hw_info)
        except Exception:
            encoder_name, flags, init_flags = enc, [], []
        is_hw = not encoder_name.startswith("lib")
        candidates.append((base, encoder_name, flags, init_flags, is_hw))

    # From in-process cache keys include encoders even if hw_info lacks them
    if encoder_test_cache is not None:
        for cache_key in encoder_test_cache.keys():
            try:
                enc_name = cache_key.split(":", 1)[0]
                if "av1" in enc_name:
                    base = "av1"
                elif "hevc" in enc_name or "h265" in enc_name:
                    base = "hevc"
                elif "h264" in enc_name:
                    base = "h264"
                elif enc_name.startswith("lib") and "av1" in enc_name:
                    base = "av1"
                else:
                    base = enc_name
                if not any(c[1] == enc_name for c in candidates):
                    candidates.append(
                        (base, enc_name, [], [], not enc_name.startswith("lib"))
                    )
            except Exception:
                continue

    # Evaluate in priority order
    for base in hw_priority:
        # Prefer encoders that explicitly passed
        for c_base, c_enc, c_flags, c_init, c_is_hw in candidates:
            if c_base != base:
                continue
            passed = _encoder_passed(c_base, c_enc, c_init)
            if passed is True:
                return {
                    "base": c_base,
                    "encoder": c_enc,
                    "hardware": c_is_hw,
                    "flags": c_flags,
                    "init_flags": c_init,
                }

        # Next prefer hardware presence when test result unknown
        for c_base, c_enc, c_flags, c_init, c_is_hw in candidates:
            if c_base != base:
                continue
            if c_is_hw:
                passed = _encoder_passed(c_base, c_enc, c_init)
                if passed is None:
                    return {
                        "base": c_base,
                        "encoder": c_enc,
                        "hardware": True,
                        "flags": c_flags,
                        "init_flags": c_init,
                    }

        # Finally pick a CPU encoder for this base if present
        for c_base, c_enc, c_flags, c_init, c_is_hw in candidates:
            if c_base != base:
                continue
            if not c_is_hw:
                return {
                    "base": c_base,
                    "encoder": c_enc,
                    "hardware": False,
                    "flags": c_flags,
                    "init_flags": c_init,
                }

    # Default to h264 CPU
    try:
        encoder_name, flags, init_flags = map_codec_to_hw("h264", hw_info)
    except Exception:
        encoder_name, flags, init_flags = (
            "libx264",
            ["-pix_fmt", "yuv420p", "-profile:v", "high"],
            [],
        )
    return {
        "base": "h264",
        "encoder": encoder_name,
        "hardware": False,
        "flags": flags,
        "init_flags": init_flags,
    }
