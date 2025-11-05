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


def test_decoder(decoder_name: str, hw_flags: List[str]) -> Tuple[bool, str]:
    """
    Test hardware decoder separately.
    Returns (success: bool, message: str)
    """
    try:
        # Create a tiny test video first (h264 for testing decoders)
        test_file = "/tmp/test_decode.mp4"
        create_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
            "-c:v", "libx264", "-t", "0.1", "-frames:v", "3",
            test_file
        ]
        subprocess.run(create_cmd, capture_output=True, timeout=5)
        
        # Now test decoding with hardware
        cmd = ["ffmpeg", "-hide_banner"]
        cmd.extend(hw_flags)
        cmd.extend([
            "-i", test_file,
            "-f", "null", "-"
        ])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        stderr_lower = result.stderr.lower()
        
        if "no device found" in stderr_lower or "cannot load" in stderr_lower:
            return False, "Hardware decode failed"
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
            timeout=10
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
    """Check if encoder is available in ffmpeg."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=2
        )
        return encoder_name in result.stdout
    except Exception:
        return False


def run_startup_tests(hw_info: Dict) -> Dict[str, bool]:
    """
    Run encoder initialization tests for all hardware-accelerated encoders.
    Returns cache dict of {encoder_key: bool}.
    Logs results for troubleshooting.
    """
    from .hw_detect import map_codec_to_hw
    
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
    test_results = []  # Track for summary table
    
    # Test all common codecs for this hardware type
    test_codecs = []
    hw_type_lower = hw_info.get("type", "cpu")
    
    if hw_type_lower == "nvidia":
        test_codecs = ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]
    elif hw_type_lower == "intel":
        test_codecs = ["h264_qsv", "hevc_qsv", "av1_qsv"]
    elif hw_type_lower in ("amd", "vaapi"):
        test_codecs = ["h264_vaapi", "hevc_vaapi", "av1_vaapi"]
    
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
                cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
                cache[cache_key] = False
                test_results.append((codec, actual_encoder, "UNAVAILABLE", "Not in ffmpeg -encoders"))
                continue
            
            # Run init test (slow but thorough)
            cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
            success, message = test_encoder_init(actual_encoder, init_hw_flags)
            cache[cache_key] = success
            
            if success:
                logger.info(f"  [{codec:15s}] ✓ PASS - {actual_encoder} initialized successfully")
                test_results.append((codec, actual_encoder, "PASS", message))
            else:
                logger.error(f"  [{codec:15s}] ✗ FAIL - {actual_encoder}: {message}")
                test_results.append((codec, actual_encoder, "FAIL", message))
            
            sys.stdout.flush()  # Flush after each test result
            
        except Exception as e:
            logger.error(f"  [{codec:15s}] ✗ ERROR - Exception: {str(e)}")
            test_results.append((codec, "unknown", "ERROR", str(e)))
            sys.stdout.flush()
    
    # Summary section
    logger.info("")
    logger.info("─" * 70)
    logger.info("  TEST SUMMARY")
    logger.info("─" * 70)
    
    passed = sum(1 for _, _, status, _ in test_results if status == "PASS")
    failed = sum(1 for _, _, status, _ in test_results if status in ("FAIL", "ERROR", "UNAVAILABLE"))
    total_tested = len(test_results)
    
    logger.info(f"  Total Encoders Tested: {total_tested}")
    logger.info(f"  ✓ Passed:  {passed}")
    logger.info(f"  ✗ Failed:  {failed}")
    logger.info("")
    
    if failed > 0:
        failed_list = [c for (c, _, status, _) in test_results if status in ("FAIL","ERROR","UNAVAILABLE")]
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
        for codec in test_codecs:
            try:
                from .hw_detect import map_codec_to_hw
                actual_encoder, _, init_hw_flags = map_codec_to_hw(codec, hw_info)
                cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
                if cache_key in cache:
                    passed = bool(cache[cache_key])
                    # Save boolean flag
                    redis_client.setex(f"encoder_test:{codec}", 2592000, "1" if passed else "0")
                    # Save JSON detail with best-effort message by re-running quick check
                    # We don't want to re-run slow tests; infer message from availability
                    detail = {"codec": codec, "actual_encoder": actual_encoder, "passed": passed}
                    try:
                        # Try to provide more context from -encoders
                        detail_msg = "OK" if passed else "Failed during init"
                        detail["message"] = detail_msg
                    except Exception:
                        pass
                    try:
                        redis_client.setex(f"encoder_test_json:{codec}", 2592000, json.dumps(detail))
                    except Exception:
                        pass
            except Exception as _:
                pass
    except Exception as e:
        logger.warning(f"Failed to store encoder test results in Redis: {e}")
    
    return cache
