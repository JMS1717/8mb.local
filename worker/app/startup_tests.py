"""
Startup encoder tests to validate NVIDIA hardware acceleration on container boot.
Populates ENCODER_TEST_CACHE so compress jobs don't pay the init test cost.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def get_gpu_env() -> dict[str, str]:
    """Get environment with NVIDIA GPU variables and library paths."""
    env = os.environ.copy()
    env["NVIDIA_VISIBLE_DEVICES"] = env.get("NVIDIA_VISIBLE_DEVICES", "all")
    env["NVIDIA_DRIVER_CAPABILITIES"] = env.get(
        "NVIDIA_DRIVER_CAPABILITIES", "compute,video,utility"
    )
    lib_paths = [
        "/usr/local/nvidia/lib64",
        "/usr/local/nvidia/lib",
        "/usr/local/cuda/lib64",
        "/usr/local/cuda/lib",
        "/usr/lib/wsl/lib",
        "/usr/lib/x86_64-linux-gnu",
    ]
    existing = env.get("LD_LIBRARY_PATH", "")
    add = ":".join(p for p in lib_paths if p)
    env["LD_LIBRARY_PATH"] = (
        (existing + (":" if existing and add else "") + add)
        if (existing or add)
        else ""
    )
    return env


def _ffmpeg_has_nvenc(env: dict[str, str]) -> bool:
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5, env=env,
        )
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        return (
            any(tok in out for tok in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"])
            and res.returncode == 0
        )
    except Exception:
        return False


def _wait_for_nv_runtime_ready(
    timeout_s: float = 30.0, interval_s: float = 2.0
) -> bool:
    """Wait until ffmpeg reports nvenc encoders are available, or timeout."""
    env = get_gpu_env()
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5, env=env,
        )
        if res.returncode == 0:
            out = (res.stdout or "") + "\n" + (res.stderr or "")
            if not any(tok in out for tok in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]):
                logger.info(
                    "NVENC encoders not present in ffmpeg build; skipping NV runtime wait."
                )
                return True
    except Exception:
        pass
    import time

    start = time.time()
    attempt = 1
    while time.time() - start < timeout_s:
        if _ffmpeg_has_nvenc(env):
            logger.info(f"NV runtime ready (attempt {attempt})")
            return True
        logger.warning(
            f"NV runtime not ready yet (attempt {attempt}) - retrying in {interval_s:.0f}s"
        )
        time.sleep(interval_s)
        attempt += 1
    logger.error(
        "Timed out waiting for NV runtime to be ready. Proceeding with tests anyway."
    )
    return False


def test_decoder(decoder_name: str, hw_flags: List[str]) -> Tuple[bool, str]:
    """Test hardware decoder separately."""
    try:
        test_file = "/tmp/test_decode.mp4"

        if "av1" in decoder_name.lower():
            encoder = "libaom-av1"
        elif "hevc" in decoder_name.lower() or "265" in decoder_name.lower():
            encoder = "libx265"
        else:
            encoder = "libx264"

        create_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
            "-c:v", encoder, "-t", "0.1", "-frames:v", "3",
        ]
        if encoder == "libaom-av1":
            create_cmd.extend(["-cpu-used", "8", "-row-mt", "1"])
        create_cmd.append(test_file)
        subprocess.run(create_cmd, capture_output=True, timeout=10, env=get_gpu_env())

        cmd = ["ffmpeg", "-hide_banner"]
        cmd.extend(hw_flags)
        cmd.extend(["-i", test_file, "-f", "null", "-"])

        attempts = 5
        delay = 1.0
        result = None
        for i in range(1, attempts + 1):
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env()
            )
            stderr_lower = (result.stderr or "").lower()
            if result.returncode != 0:
                break
            if any(
                err in stderr_lower for err in ["cuinit(0)", "no device", "cannot load"]
            ):
                logger.warning(
                    f"Decode init failed (attempt {i}/{attempts}). Retrying in {delay:.0f}s"
                )
                import time
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            break
        if result is None:
            return False, "Decode did not execute"
        stderr_lower = (result.stderr or "").lower()

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
    """Test if encoder can actually be initialized."""
    try:
        common_args = [
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
            "-c:v", encoder_name, "-t", "0.1", "-frames:v", "3",
            "-f", "null", "-",
        ]
        cmd = ["ffmpeg", "-hide_banner", *common_args]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=8, env=get_gpu_env()
        )

        if result is None:
            return False, "Encode did not execute"

        stderr_lower = result.stderr.lower()

        is_cpu_encoder = encoder_name.startswith("lib")
        if "operation not permitted" in stderr_lower:
            if is_cpu_encoder:
                return True, "OK (seccomp bypass)"
            return False, "Operation not permitted"

        if "unknown encoder" in stderr_lower:
            return False, "Unknown encoder"
        if "could not open" in stderr_lower and encoder_name in stderr_lower:
            return False, "Could not open encoder"
        if "no nvenc capable devices found" in stderr_lower:
            return False, "No NVENC device"
        if "driver does not support" in stderr_lower and "profile" in stderr_lower:
            return False, "Driver doesn't support encoder profile"
        if "no device found" in stderr_lower:
            return False, "No device found"
        if "failed to" in stderr_lower and (
            "initialize" in stderr_lower or "create" in stderr_lower
        ):
            return False, "Encoder init failed"
        if "cannot load" in stderr_lower and ".so" in stderr_lower:
            lib = (
                result.stderr.split("Cannot load")[1].split()[0]
                if "Cannot load" in result.stderr
                else "unknown"
            )
            return False, f"Missing library ({lib})"

        if result.returncode != 0:
            error_lines = [
                l for l in result.stderr.split("\n")
                if "error" in l.lower() or "fail" in l.lower()
            ]
            if error_lines:
                return False, error_lines[0][:60]
            return False, f"Exit code {result.returncode}"

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
            capture_output=True, text=True, timeout=2, env=get_gpu_env(),
        )
        for line in result.stdout.split("\n"):
            if encoder_name in line:
                parts = [p for p in line.split() if p]
                if encoder_name in parts:
                    return True
        return False
    except Exception as e:
        logger.warning(f"Failed to check encoder availability: {e}")
        return False


def run_startup_tests(hw_info: dict[str, Any]) -> Dict[str, bool]:
    """Run encoder initialization tests for NVIDIA and CPU encoders."""
    from .hw_detect import map_codec_to_hw

    logger.info("GPU Environment Check:")
    logger.info(
        f"  NVIDIA_VISIBLE_DEVICES: {os.environ.get('NVIDIA_VISIBLE_DEVICES', 'NOT SET')}"
    )
    logger.info(
        f"  NVIDIA_DRIVER_CAPABILITIES: {os.environ.get('NVIDIA_DRIVER_CAPABILITIES', 'NOT SET')}"
    )
    logger.info(f"  LD_LIBRARY_PATH: {os.environ.get('LD_LIBRARY_PATH', 'NOT SET')}")
    logger.info("")

    _wait_for_nv_runtime_ready(timeout_s=30.0, interval_s=2.0)

    logger.info("")
    logger.info("=" * 70)
    logger.info("          ENCODER VALIDATION TESTS")
    logger.info("=" * 70)
    logger.info("")
    sys.stdout.flush()

    hw_type = hw_info.get("type", "unknown").upper()
    hw_device = hw_info.get("device", "N/A")
    logger.info(f"  Hardware Type:   {hw_type}")
    logger.info(f"  Hardware Device: {hw_device}")
    logger.info("")
    logger.info("-" * 70)
    sys.stdout.flush()

    cache: Dict[str, bool] = {}
    test_results = {}

    test_codecs = []
    hw_type_lower = hw_info.get("type", "cpu")
    hw_decoders = {}

    if hw_type_lower == "nvidia":
        test_codecs = ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]
        hw_decoders = {
            "h264_nvenc": ("h264", ["-hwaccel", "cuda", "-c:v", "h264_cuvid"]),
            "hevc_nvenc": ("hevc", ["-hwaccel", "cuda", "-c:v", "hevc_cuvid"]),
            "av1_nvenc": ("av1", ["-hwaccel", "cuda", "-c:v", "av1_cuvid"]),
        }

    test_codecs.extend(["libx264", "libx265", "libaom-av1"])

    logger.info(f"  Testing {len(test_codecs)} encoder(s)...")
    logger.info("-" * 70)
    logger.info("")

    for codec in test_codecs:
        try:
            actual_encoder, v_flags, init_hw_flags = map_codec_to_hw(codec, hw_info)

            if actual_encoder in ("libx264", "libx265", "libaom-av1"):
                if codec not in ("libx264", "libx265", "libaom-av1"):
                    logger.info(
                        f"  [{codec:15s}] SKIPPED - Maps to CPU fallback: {actual_encoder}"
                    )
                    continue

            if not is_encoder_available(actual_encoder):
                logger.warning(
                    f"  [{codec:15s}] UNAVAILABLE - Not in ffmpeg -encoders list"
                )
                if actual_encoder.endswith("_nvenc"):
                    try:
                        enc_result = subprocess.run(
                            ["ffmpeg", "-hide_banner", "-encoders"],
                            capture_output=True, text=True, timeout=2, env=get_gpu_env(),
                        )
                        similar = [
                            line.strip()
                            for line in enc_result.stdout.split("\n")
                            if "nvenc" in line.lower()
                        ]
                        if similar:
                            logger.info(
                                f"    Available hardware encoders: {', '.join(similar[:3])}"
                            )
                        else:
                            logger.warning(
                                f"    No hardware encoders found in ffmpeg build"
                            )
                    except Exception:
                        pass
                cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
                cache[cache_key] = False
                test_results[codec] = (
                    actual_encoder, "UNAVAILABLE", None, "Not in ffmpeg -encoders",
                )
                continue

            decode_passed = None
            decode_message = "N/A"
            if codec in hw_decoders:
                format_name, dec_flags = hw_decoders[codec]
                logger.info(
                    f"  [{codec:15s}] Testing decoder: {format_name} with {' '.join(dec_flags)}"
                )
                decode_success, decode_message = test_decoder(format_name, dec_flags)
                decode_passed = decode_success
                decode_status = "PASS" if decode_success else "FAIL"
                logger.info(
                    f"                  Decode: {decode_status} - {decode_message}"
                )

            cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
            success, message = test_encoder_init(actual_encoder, init_hw_flags)
            cache[cache_key] = success

            encode_status = "PASS" if success else "FAIL"
            logger.info(f"                  Encode: {encode_status} - {message}")

            overall_passed = success and (decode_passed is None or decode_passed)
            if overall_passed:
                logger.info(f"  [{codec:15s}] OVERALL PASS")
                test_results[codec] = (actual_encoder, "PASS", decode_passed, message)
            else:
                logger.error(f"  [{codec:15s}] OVERALL FAIL")
                test_results[codec] = (actual_encoder, "FAIL", decode_passed, message)

            sys.stdout.flush()

        except Exception as e:
            logger.error(f"  [{codec:15s}] ERROR - Exception: {str(e)}")
            test_results[codec] = ("unknown", "ERROR", None, str(e))
            sys.stdout.flush()

    logger.info("")
    logger.info("-" * 70)
    logger.info("  TEST SUMMARY")
    logger.info("-" * 70)

    passed = sum(1 for _, status, _, _ in test_results.values() if status == "PASS")
    failed = sum(
        1 for _, status, _, _ in test_results.values()
        if status in ("FAIL", "ERROR", "UNAVAILABLE")
    )
    total_tested = len(test_results)

    logger.info(f"  Total Encoders Tested: {total_tested}")
    logger.info(f"  Passed:  {passed}")
    logger.info(f"  Failed:  {failed}")
    logger.info("")

    if failed > 0:
        failed_list = [
            c for c, (_, status, _, _) in test_results.items()
            if status in ("FAIL", "ERROR", "UNAVAILABLE")
        ]
        if failed_list:
            logger.warning("  Failing encoders: %s", ", ".join(failed_list))
        logger.warning(
            "  Failed encoders will automatically fall back to CPU encoding."
        )

    logger.info("-" * 70)
    logger.info("")
    sys.stdout.flush()

    # Store results in Redis
    try:
        from redis import Redis
        redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        for codec, (actual_encoder, encode_status, decode_status, encode_msg) in test_results.items():
            try:
                _, _, init_hw_flags = map_codec_to_hw(codec, hw_info)
                cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
                encode_passed = encode_status == "PASS"
                overall_passed = encode_passed and (
                    decode_status is None or decode_status is True
                )

                redis_client.setex(
                    f"encoder_test:{codec}", 2592000, "1" if overall_passed else "0"
                )
                encode_detail = {
                    "codec": codec,
                    "actual_encoder": actual_encoder,
                    "passed": encode_passed,
                    "message": encode_msg or ("OK" if encode_passed else "Failed during init"),
                }
                try:
                    redis_client.setex(
                        f"encoder_test_json:{codec}", 2592000, json.dumps(encode_detail)
                    )
                except Exception:
                    pass

                if decode_status is not None:
                    decode_detail = {
                        "codec": codec,
                        "passed": decode_status,
                        "message": "OK" if decode_status else "Decoder failed",
                    }
                    try:
                        redis_client.setex(
                            f"encoder_test_decode_json:{codec}",
                            2592000, json.dumps(decode_detail),
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Failed to store test result for {codec}: {e}")
    except Exception as e:
        logger.warning(f"Failed to store encoder test results in Redis: {e}")

    return cache
