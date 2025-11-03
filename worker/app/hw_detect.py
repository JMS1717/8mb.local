"""Hardware acceleration detection and codec mapping."""
import os
import subprocess
from typing import Dict, Optional


def detect_hw_accel() -> Dict[str, any]:
    """
    Detect available hardware acceleration.
    Returns dict with: type (nvidia/intel/amd/cpu), encoders available, etc.
    """
    result = {
        "type": "cpu",
        "available_encoders": {},
        "decode_method": None,
        "upload_method": None,
        "vaapi_device": None,
    }
    
    # Check for NVIDIA
    if _check_nvidia():
        result["type"] = "nvidia"
        result["decode_method"] = "cuda"
        result["available_encoders"] = {
            "h264": "h264_nvenc",
            "hevc": "hevc_nvenc",
            "av1": "av1_nvenc",
        }
        return result
    
    # Check for Intel QSV
    qsv_available = _check_intel_qsv()
    
    # Check for VAAPI (Intel/AMD on Linux)
    vaapi_info = _check_vaapi()
    
    if qsv_available:
        result["type"] = "intel"
        result["decode_method"] = "qsv"
        result["available_encoders"] = {
            "h264": "h264_qsv",
            "hevc": "hevc_qsv",
            "av1": "av1_qsv",  # Available on Arc and newer
        }
        return result
    
    if vaapi_info["available"]:
        # VAAPI detected - could be Intel or AMD
        result["type"] = vaapi_info["vendor"]
        result["decode_method"] = "vaapi"
        result["vaapi_device"] = vaapi_info["device"]
        result["available_encoders"] = {
            "h264": "h264_vaapi",
            "hevc": "hevc_vaapi",
        }
        # AV1 VAAPI support is newer, check if available
        if vaapi_info["av1_supported"]:
            result["available_encoders"]["av1"] = "av1_vaapi"
        return result
    
    # CPU fallback
    result["available_encoders"] = {
        "h264": "libx264",
        "hevc": "libx265",
        "av1": "libaom-av1",  # widely available CPU AV1 encoder
    }
    
    return result


def _check_nvidia() -> bool:
    """Check if NVIDIA GPU is available."""
    try:
        # Check nvidia-smi
        result = subprocess.run(
            ["nvidia-smi"], 
            capture_output=True, 
            timeout=2
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    # Check for CUDA device via ffmpeg
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if "cuda" in result.stdout.lower():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return False


def _check_intel_qsv() -> bool:
    """Check if Intel QSV is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if "qsv" in result.stdout.lower():
            # Verify encoder is available
            encoders = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if "h264_qsv" in encoders.stdout:
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return False


def _check_vaapi() -> Dict[str, any]:
    """Check if VAAPI is available (Intel/AMD on Linux)."""
    result = {
        "available": False,
        "vendor": "unknown",
        "device": "/dev/dri/renderD128",
        "av1_supported": False,
    }
    
    try:
        # Check for VAAPI hwaccel
        hwaccels = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if "vaapi" not in hwaccels.stdout.lower():
            return result
        
        # Check for VAAPI encoders
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if "h264_vaapi" not in encoders.stdout:
            return result
        
        result["available"] = True
        
        # Check for AV1 VAAPI support
        if "av1_vaapi" in encoders.stdout:
            result["av1_supported"] = True
        
        # Try to detect vendor (Intel vs AMD) via device info
        # Check for multiple render nodes
        import glob
        render_nodes = glob.glob("/dev/dri/renderD*")
        if render_nodes:
            result["device"] = render_nodes[0]
        
        # Attempt to identify vendor via vainfo or lspci
        try:
            vainfo = subprocess.run(
                ["vainfo", "--display", "drm", "--device", result["device"]],
                capture_output=True,
                text=True,
                timeout=2
            )
            output = vainfo.stdout.lower() + vainfo.stderr.lower()
            if "intel" in output:
                result["vendor"] = "intel"
            elif "amd" in output or "radeon" in output:
                result["vendor"] = "amd"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # Fallback: check lspci
            try:
                lspci = subprocess.run(
                    ["lspci"], 
                    capture_output=True, 
                    text=True,
                    timeout=2
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
    if requested_codec in ("h264_nvenc", "hevc_nvenc", "av1_nvenc",
                           "h264_qsv", "hevc_qsv", "av1_qsv",
                           "h264_vaapi", "hevc_vaapi", "av1_vaapi"):
        encoder = requested_codec
        flags = []
        init_flags = []
        
        # Add hardware-specific flags based on encoder type
        if encoder.endswith("_nvenc"):
            flags = ["-pix_fmt", "yuv420p"]
            if "h264" in encoder:
                flags += ["-profile:v", "high"]
            elif "hevc" in encoder:
                flags += ["-profile:v", "main"]
        elif encoder.endswith("_qsv"):
            init_flags = ["-init_hw_device", "qsv=hw", "-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
            flags = ["-pix_fmt", "nv12"]
            if "h264" in encoder:
                flags += ["-profile:v", "high"]
        elif encoder.endswith("_vaapi"):
            vaapi_device = hw_info.get("vaapi_device") or "/dev/dri/renderD128"
            init_flags = ["-init_hw_device", f"vaapi=va:{vaapi_device}", "-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi", "-hwaccel_device", "va"]
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
        flags = ["-pix_fmt", "yuv420p"]
        if base == "h264":
            flags += ["-profile:v", "high"]
        elif base == "hevc":
            flags += ["-profile:v", "main"]
    elif encoder.endswith("_qsv"):
        init_flags = ["-init_hw_device", "qsv=hw", "-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
        flags = ["-pix_fmt", "nv12"]
        if base == "h264":
            flags += ["-profile:v", "high"]
    elif encoder.endswith("_vaapi"):
        vaapi_device = hw_info.get("vaapi_device") or "/dev/dri/renderD128"
        init_flags = ["-init_hw_device", f"vaapi=va:{vaapi_device}", "-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi", "-hwaccel_device", "va"]
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
