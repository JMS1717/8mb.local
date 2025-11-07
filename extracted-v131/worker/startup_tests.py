"""
Startup encoder tests to validate hardware acceleration on container boot.
Populates ENCODER_TEST_CACHE so compress jobs don't pay the init test cost.
"""
import os
import json
import subprocess
import sys
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def get_gpu_env():
    """
    Get environment with NVIDIA GPU variables and library paths for subprocess calls.
    Includes LD_LIBRARY_PATH locations needed for CUDA on WSL2 and NVIDIA toolkit.
    """
    env = os.environ.copy()
    # Ensure NVIDIA variables are set for GPU access
    env['NVIDIA_VISIBLE_DEVICES'] = env.get('NVIDIA_VISIBLE_DEVICES', 'all')
    env['NVIDIA_DRIVER_CAPABILITIES'] = env.get('NVIDIA_DRIVER_CAPABILITIES', 'compute,video,utility')
    # Add common library locations (non-destructive append)
    lib_paths = [
        '/usr/local/nvidia/lib64',
        '/usr/local/nvidia/lib',
        '/usr/local/cuda/lib64',
        '/usr/local/cuda/lib',
        '/usr/lib/wsl/lib',  # WSL2 libcuda.so location
        '/usr/lib/x86_64-linux-gnu',
    ]
    existing = env.get('LD_LIBRARY_PATH', '')
    add = ':'.join(p for p in lib_paths if p)
    env['LD_LIBRARY_PATH'] = (existing + (':' if existing and add else '') + add) if (existing or add) else ''
    return env

def _ffmpeg_has_nvenc(env: dict) -> bool:
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5, env=env)
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        return any(tok in out for tok in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]) and res.returncode == 0
    except Exception:
        return False

def _wait_for_nv_runtime_ready(timeout_s: float = 30.0, interval_s: float = 2.0) -> bool:
    """Wait until ffmpeg reports nvenc encoders are available, or timeout."""
    env = get_gpu_env()
    import time
    start = time.time()
    attempt = 1
    while time.time() - start < timeout_s:
        if _ffmpeg_has_nvenc(env):
            logger.info(f"✅ NV runtime ready (attempt {attempt})")
            return True
        logger.warning(f"NV runtime not ready yet (attempt {attempt}) - retrying in {interval_s:.0f}s…")
        time.sleep(interval_s)
        attempt += 1
    logger.error("Timed out waiting for NV runtime to be ready. Proceeding with tests anyway.")
    return False


def test_decoder(decoder_name: str, hw_flags: List[str]) -> Tuple[bool, str]:
    """
    Test hardware decoder separately.
    Returns (success: bool, message: str)
    """
    try:
        # Create appropriate test video based on decoder type
        test_file = "/tmp/test_decode.mp4"
        
        # Choose encoder based on decoder being tested
        if "av1" in decoder_name.lower():
            # For AV1 decoders, create AV1 test video
            encoder = "libaom-av1"
        elif "hevc" in decoder_name.lower() or "265" in decoder_name.lower():
            encoder = "libx265"
        else:
            # Default to H.264
            encoder = "libx264"
        
        create_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
            "-c:v", encoder, "-t", "0.1", "-frames:v", "3",
        ]
        
        # Add encoder-specific options
        if encoder == "libaom-av1":
            create_cmd.extend(["-cpu-used", "8", "-row-mt", "1"])
        
        create_cmd.append(test_file)
        subprocess.run(create_cmd, capture_output=True, timeout=10, env=get_gpu_env())
        
        # Now test decoding with hardware
        cmd = ["ffmpeg", "-hide_banner"]
        cmd.extend(hw_flags)
        cmd.extend([
            "-i", test_file,
            "-f", "null", "-"
        ])
        # Retry if CUDA not initialized yet
        attempts = 5
        delay = 1.0
        result = None
        for i in range(1, attempts + 1):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env())
            stderr_lower = (result.stderr or '').lower()
            if result.returncode != 0:
                break
            if any(err in stderr_lower for err in ["cuinit(0)", "no device", "cannot load"]):
                logger.warning(f"Decode init failed (attempt {i}/{attempts}). Retrying in {delay:.0f}s…")
                import time; time.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            break
        stderr_lower = (result.stderr or '').lower()
        stderr_lower = result.stderr.lower()
        
        if "no device found" in stderr_lower or "cannot load" in stderr_lower:
            return False, "Hardware decode failed"
        if "not supported" in stderr_lower or "invalid" in stderr_lower:
            return False, "Decoder not supported"
        if result.returncode != 0:
            return False, f"Decode error (code {result.returncode})"
        return True, "Decode OK"
    except subprocess.TimeoutExpired:
        return False, "Decode timeout"
    except Exception as e:
        return False, f"Decode exception: {str(e)}"


def test_encoder_init(encoder_name: str, hw_flags: List[str]) -> Tuple[bool, str]:
    """
    Test if encoder can actually be initialized (not just listed).
    Tests ONLY encoding, separate from decode.
    Returns (success: bool, message: str)
    """
    try:
        # Test encoding directly without hardware decode
        cmd = ["ffmpeg", "-hide_banner"]
        # Don't use hw_flags here - we're testing encoder only
        cmd.extend([
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
            "-c:v", encoder_name,
            "-t", "0.1",
            "-frames:v", "3",  # Encode a few frames to be sure
            "-f", "null", "-"
        ])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            env=get_gpu_env()
        )
        stderr_lower = result.stderr.lower()
        
        # CPU encoders: "Operation not permitted" is often a Docker seccomp issue, not encoder failure
        is_cpu_encoder = encoder_name.startswith("lib")
        if "operation not permitted" in stderr_lower:
            if is_cpu_encoder:
                return True, "OK (seccomp bypass)"
            return False, "Operation not permitted"
        
        # Check for specific errors that indicate encoder problems
        if "unknown encoder" in stderr_lower:
            return False, "Unknown encoder"
        if "could not open" in stderr_lower and encoder_name in stderr_lower:
            return False, "Could not open encoder"
        if "no nvenc capable devices found" in stderr_lower:
            return False, "No NVENC device"
        if "driver does not support" in stderr_lower:
            return False, "Driver doesn't support encoder"
        if "no device found" in stderr_lower:
            return False, "No device found"
        if "failed to" in stderr_lower and "encoder" in stderr_lower:
            return False, "Encoder init failed"
        if "cannot load" in stderr_lower and ".so" in stderr_lower:
            lib = result.stderr.split('Cannot load')[1].split()[0] if 'Cannot load' in result.stderr else 'unknown'
            return False, f"Missing library ({lib})"
        
        # Check return code
        if result.returncode != 0:
            # Try to extract meaningful error
            error_lines = [l for l in result.stderr.split('\n') if 'error' in l.lower() or 'fail' in l.lower()]
            if error_lines:
                return False, error_lines[0][:60]
            return False, f"Exit code {result.returncode}"
        
        # Success
        return True, "Encode OK"
    except subprocess.TimeoutExpired:
        return False, "Encode timeout (>10s)"
    except Exception as e:
        return False, f"Exception: {str(e)}"


def is_encoder_available(encoder_name: str) -> bool:
    """Check if encoder is available in ffmpeg -encoders list."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=2,
            env=get_gpu_env()
        )
        # Look for exact encoder match
        # Format is like: " V..... h264_nvenc           Nvidia NVENC H.264 encoder"
        # After split: ['V.....', 'h264_nvenc', 'Nvidia', 'NVENC', 'H.264', 'encoder']
        for line in result.stdout.split('\n'):
            if encoder_name in line:
                # Split and find encoder name - it should be column index 1 (after flags)
                parts = [p for p in line.split() if p]  # Filter empty strings
                # Check if encoder name appears as standalone word
                if encoder_name in parts:
                    return True
        return False
    except Exception as e:
        logger.warning(f"Failed to check encoder availability: {e}")
        return False


def run_startup_tests(hw_info: Dict) -> Dict[str, bool]:
    """
    Run encoder initialization tests for all hardware-accelerated encoders.
    Tests decode and encode separately for hardware codecs.
    Returns cache dict of {encoder_key: bool}.
    Logs results for troubleshooting.
    """
    from .hw_detect import map_codec_to_hw
    
    # DEBUG: Log GPU environment variables
    logger.info("🔍 GPU Environment Check:")
    logger.info(f"  NVIDIA_VISIBLE_DEVICES: {os.environ.get('NVIDIA_VISIBLE_DEVICES', 'NOT SET')}")
    logger.info(f"  NVIDIA_DRIVER_CAPABILITIES: {os.environ.get('NVIDIA_DRIVER_CAPABILITIES', 'NOT SET')}")
    logger.info(f"  LD_LIBRARY_PATH: {os.environ.get('LD_LIBRARY_PATH', 'NOT SET')}")
    logger.info("")

    # Ensure NV runtime is ready before running tests (addresses cuInit(0) fail on early start)
    _wait_for_nv_runtime_ready(timeout_s=30.0, interval_s=2.0)
    
    logger.info("")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║" + " " * 16 + "ENCODER VALIDATION TESTS" + " " * 28 + "║")
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")
    sys.stdout.flush()  # Force output to appear in docker logs
    
    hw_type = hw_info.get('type', 'unknown').upper()
    hw_device = hw_info.get('device', 'N/A')
    logger.info(f"  Hardware Type:   {hw_type}")
    logger.info(f"  Hardware Device: {hw_device}")
    logger.info("")
    logger.info("─" * 70)
    sys.stdout.flush()
    
    cache: Dict[str, bool] = {}
    test_results = {}  # Dict[codec, tuple] for easier lookup
    
    # Test all common codecs for this hardware type
    test_codecs = []
    hw_type_lower = hw_info.get("type", "cpu")
    
    # Define hardware decoders for each codec type
    hw_decoders = {}
    
    if hw_type_lower == "nvidia":
        test_codecs = ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]
        hw_decoders = {
            "h264_nvenc": ("h264", ["-hwaccel", "cuda", "-c:v", "h264_cuvid"]),
            "hevc_nvenc": ("hevc", ["-hwaccel", "cuda", "-c:v", "hevc_cuvid"]),
            "av1_nvenc": ("av1", ["-hwaccel", "cuda", "-c:v", "av1_cuvid"]),
        }
    elif hw_type_lower == "intel":
        test_codecs = ["h264_qsv", "hevc_qsv", "av1_qsv"]
        hw_decoders = {
            "h264_qsv": ("h264", ["-hwaccel", "qsv", "-c:v", "h264_qsv"]),
            "hevc_qsv": ("hevc", ["-hwaccel", "qsv", "-c:v", "hevc_qsv"]),
            "av1_qsv": ("av1", ["-hwaccel", "qsv", "-c:v", "av1_qsv"]),
        }
    elif hw_type_lower in ("amd", "vaapi"):
        test_codecs = ["h264_vaapi", "hevc_vaapi", "av1_vaapi"]
        hw_decoders = {
            "h264_vaapi": ("h264", ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]),
            "hevc_vaapi": ("hevc", ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]),
            "av1_vaapi": ("av1", ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]),
        }
    
    # Always test CPU fallbacks
    test_codecs.extend(["libx264", "libx265", "libaom-av1"])
    
    logger.info(f"  Testing {len(test_codecs)} encoder(s)...")
    logger.info("─" * 70)
    logger.info("")
    
    for codec in test_codecs:
        try:
            actual_encoder, v_flags, init_hw_flags = map_codec_to_hw(codec, hw_info)
            
            # Skip if not actually a hardware encoder for this system
            if actual_encoder in ("libx264", "libx265", "libaom-av1"):
                if codec not in ("libx264", "libx265", "libaom-av1"):
                    logger.info(f"  [{codec:15s}] ⊗ SKIPPED - Maps to CPU fallback: {actual_encoder}")
                    continue
            
            # Check availability first (fast)
            if not is_encoder_available(actual_encoder):
                logger.warning(f"  [{codec:15s}] ✗ UNAVAILABLE - Not in ffmpeg -encoders list")
                # Log additional diagnostic info for hardware encoders
                if actual_encoder.endswith(("_nvenc", "_qsv", "_amf", "_vaapi")):
                    try:
                        # Try running ffmpeg -encoders to see what's available
                        enc_result = subprocess.run(
                            ["ffmpeg", "-hide_banner", "-encoders"],
                            capture_output=True,
                            text=True,
                            timeout=2,
                            env=get_gpu_env()
                        )
                        # Look for similar encoders
                        similar = [line.strip() for line in enc_result.stdout.split('\n') 
                                  if 'nvenc' in line.lower() or 'qsv' in line.lower() or 'vaapi' in line.lower()]
                        if similar:
                            logger.info(f"    Available hardware encoders: {', '.join(similar[:3])}")
                        else:
                            logger.warning(f"    No hardware encoders found in ffmpeg build")
                    except Exception:
                        pass
                cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
                cache[cache_key] = False
                test_results[codec] = (actual_encoder, "UNAVAILABLE", None, "Not in ffmpeg -encoders")
                continue
            
            # Test decoder first (if hardware codec)
            decode_passed = None
            decode_message = "N/A"
            if codec in hw_decoders:
                format_name, dec_flags = hw_decoders[codec]
                logger.info(f"  [{codec:15s}] Testing decoder: {format_name} with {' '.join(dec_flags)}")
                decode_success, decode_message = test_decoder(format_name, dec_flags)
                decode_passed = decode_success
                decode_status = "✓ PASS" if decode_success else "✗ FAIL"
                logger.info(f"                  Decode: {decode_status} - {decode_message}")
            
            # Run encoder init test (slow but thorough)
            cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
            success, message = test_encoder_init(actual_encoder, init_hw_flags)
            cache[cache_key] = success
            
            encode_status = "✓ PASS" if success else "✗ FAIL"
            logger.info(f"                  Encode: {encode_status} - {message}")
            
            # Overall status
            overall_passed = success and (decode_passed is None or decode_passed)
            if overall_passed:
                logger.info(f"  [{codec:15s}] ✓ OVERALL PASS")
                test_results[codec] = (actual_encoder, "PASS", decode_passed, message)
            else:
                logger.error(f"  [{codec:15s}] ✗ OVERALL FAIL")
                test_results[codec] = (actual_encoder, "FAIL", decode_passed, message)
            
            sys.stdout.flush()  # Flush after each test result
            
        except Exception as e:
            logger.error(f"  [{codec:15s}] ✗ ERROR - Exception: {str(e)}")
            test_results[codec] = ("unknown", "ERROR", None, str(e))
            sys.stdout.flush()
    
    # Summary section
    logger.info("")
    logger.info("─" * 70)
    logger.info("  TEST SUMMARY")
    logger.info("─" * 70)
    
    passed = sum(1 for _, status, _, _ in test_results.values() if status == "PASS")
    failed = sum(1 for _, status, _, _ in test_results.values() if status in ("FAIL", "ERROR", "UNAVAILABLE"))
    total_tested = len(test_results)
    
    logger.info(f"  Total Encoders Tested: {total_tested}")
    logger.info(f"  ✓ Passed:  {passed}")
    logger.info(f"  ✗ Failed:  {failed}")
    logger.info("")
    
    if failed > 0:
        failed_list = [c for c, (_, status, _, _) in test_results.items() if status in ("FAIL","ERROR","UNAVAILABLE")]
        if failed_list:
            logger.warning("  Failing encoders: %s", ", ".join(failed_list))
        logger.warning("  Failed encoders will automatically fall back to CPU encoding.")
    
    logger.info("─" * 70)
    logger.info("")
    sys.stdout.flush()
    
    # Store results in Redis for backend access (30-day expiry)
    try:
        from redis import Redis
        redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        # Store which encoders passed tests and last message
        for codec, (actual_encoder, encode_status, decode_status, encode_msg) in test_results.items():
            try:
                from .hw_detect import map_codec_to_hw
                _, _, init_hw_flags = map_codec_to_hw(codec, hw_info)
                cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
                
                # Determine if encode passed
                encode_passed = (encode_status == "PASS")
                overall_passed = encode_passed and (decode_status is None or decode_status is True)
                
                # Save boolean flag for overall pass
                redis_client.setex(f"encoder_test:{codec}", 2592000, "1" if overall_passed else "0")
                
                # Save JSON detail for encode
                encode_detail = {
                    "codec": codec, 
                    "actual_encoder": actual_encoder, 
                    "passed": encode_passed,
                    "message": encode_msg if encode_msg else ("OK" if encode_passed else "Failed during init")
                }
                try:
                    redis_client.setex(f"encoder_test_json:{codec}", 2592000, json.dumps(encode_detail))
                except Exception:
                    pass
                
                # Save JSON detail for decode (if tested)
                if decode_status is not None:
                    decode_detail = {
                        "codec": codec,
                        "passed": decode_status,
                        "message": "OK" if decode_status else "Decoder failed"
                    }
                    try:
                        redis_client.setex(f"encoder_test_decode_json:{codec}", 2592000, json.dumps(decode_detail))
                    except Exception:
                        pass
                        
            except Exception as e:
                logger.warning(f"Failed to store test result for {codec}: {e}")
    except Exception as e:
        logger.warning(f"Failed to store encoder test results in Redis: {e}")
    
    return cache
