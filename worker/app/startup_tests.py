"""Startup encoder tests for NVIDIA, Intel QSV, VAAPI, and CPU encoders.

The worker caches the result so normal jobs do not pay the device-init cost.
Hardware tests intentionally encode one frame with the same initialization and
upload filters used by the job path; an ``ffmpeg -encoders`` listing alone is
not sufficient evidence that a passed-through GPU works.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Tuple

from shared.subprocess_utils import hidden_process_kwargs

from .constants import AMF_ENCODERS, CPU_ENCODERS, QSV_ENCODERS, VAAPI_ENCODERS
from .qsv_filters import qsv_input_filter, qsv_probe_size

logger = logging.getLogger(__name__)


def get_gpu_env() -> dict[str, str]:
    """Get the canonical subprocess environment for GPU probes.

    Keep startup probes on the same VAAPI driver search path as detection and
    real jobs.  Having a second environment builder was an easy way for a
    probe to pass while the worker later loaded a different driver.
    """
    from .hw_detect import get_gpu_env as _get_gpu_env

    return _get_gpu_env()


def _ffmpeg_has_nvenc(env: dict[str, str]) -> bool:
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5, env=env,
            **hidden_process_kwargs(),
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
            **hidden_process_kwargs(),
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
    test_file: str | None = None
    try:
        # Use the container's TMPDIR (set to the mounted upload volume by the
        # entrypoint) and a unique name so concurrent worker starts cannot
        # overwrite one another's seed file.
        with tempfile.NamedTemporaryFile(
            prefix="8mb_decode_", suffix=".mp4", delete=False
        ) as handle:
            test_file = handle.name

        if "av1" in decoder_name.lower():
            # SVT-AV1 is the approved software AV1 path. Do not hide a missing
            # SVT build by creating the seed with the much slower libaom path.
            encoder = "libsvtav1"
            if not is_encoder_available(encoder):
                try:
                    os.unlink(test_file)
                except OSError:
                    pass
                return False, "SVT-AV1 (libsvtav1) is not available for the decoder seed"
        elif "hevc" in decoder_name.lower() or "265" in decoder_name.lower():
            encoder = "libx265"
        else:
            encoder = "libx264"

        create_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
            "-c:v", encoder, "-t", "0.1", "-frames:v", "3",
        ]
        if encoder == "libsvtav1":
            create_cmd.extend(["-preset", "12"])
        create_cmd.append(test_file)
        logger.debug("test_decoder: seed-encode cmd = %s", " ".join(create_cmd))
        seed = subprocess.run(
            create_cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env(),
            **hidden_process_kwargs(),
        )
        if seed.returncode != 0:
            return False, "Could not create decoder test file"

        cmd = ["ffmpeg", "-hide_banner"]
        cmd.extend(hw_flags)
        cmd.extend(["-i", test_file, "-f", "null", "-"])

        attempts = 5
        delay = 1.0
        result = None
        for i in range(1, attempts + 1):
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, env=get_gpu_env(),
                **hidden_process_kwargs(),
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
    finally:
        if test_file:
            try:
                os.unlink(test_file)
            except OSError:
                pass


def test_encoder_init(encoder_name: str, hw_flags: List[str]) -> Tuple[bool, str]:
    """Test if encoder can actually be initialized.

    ``hw_flags`` are deliberately placed before the input. QSV needs the
    VAAPI-to-QSV two-step device initialization and both QSV/VAAPI need a
    hardware upload from the lavfi source.
    """
    try:
        source_size = qsv_probe_size(sys.platform) if encoder_name in QSV_ENCODERS else "256x256"
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            *hw_flags,
            "-f", "lavfi", "-i", f"color=black:s={source_size}:d=0.1:r=1",
        ]
        if encoder_name in QSV_ENCODERS or encoder_name in VAAPI_ENCODERS:
            upload_filter = qsv_input_filter(sys.platform)
            if encoder_name in VAAPI_ENCODERS:
                upload_filter = "format=nv12|vaapi,hwupload"
            cmd += ["-vf", upload_filter]
        elif encoder_name in AMF_ENCODERS:
            # AMF is a native Windows path and is most portable with the
            # explicit 4:2:0 format used by the real job command.
            cmd += ["-pix_fmt", "yuv420p"]
        cmd += [
            "-c:v", encoder_name, "-t", "0.1", "-frames:v", "3",
            "-f", "null", "-",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15 if (encoder_name in QSV_ENCODERS or encoder_name in VAAPI_ENCODERS) else 8,
            env=get_gpu_env(),
            **hidden_process_kwargs(),
        )

        if result is None:
            return False, "Encode did not execute"

        stderr_lower = (result.stderr or "").lower()

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
        return False, "Encode timeout"
    except Exception as e:
        return False, f"Exception: {str(e)}"


def is_encoder_available(encoder_name: str) -> bool:
    """Check if encoder is available in ffmpeg -encoders list."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=2, env=get_gpu_env(),
            **hidden_process_kwargs(),
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
    """Run and cache one-frame tests for each relevant encoder family."""
    from .hw_detect import _is_vaapi_device, map_codec_to_hw

    hw_type = str(hw_info.get("type", "cpu")).lower()
    available_types = {str(value).lower() for value in (hw_info.get("available_types") or [])}
    available_encoders = set((hw_info.get("available_encoders") or {}).values())
    vaapi_devices = [
        device for device in (hw_info.get("vaapi_devices") or [])
        if isinstance(device, dict) and _is_vaapi_device(device)
    ]
    # ``vaapi_device`` may be a stale field from an older worker. Only a
    # compatible, currently discovered device makes the Linux VAAPI path
    # eligible; CUDA's NVIDIA render node must not trigger VAAPI probes.
    has_dri = bool(vaapi_devices)
    has_intel_dri = any(
        str(device.get("vendor", "")).lower() == "intel"
        for device in vaapi_devices
        if isinstance(device, dict)
    )

    has_nvenc = hw_type == "nvidia" or "nvidia" in available_types or any(
        encoder.endswith("_nvenc") for encoder in available_encoders
    )
    # Probe discovered DRI families again even if the first detection pass
    # failed.  This makes the Settings rerun useful after a driver/container
    # race, while the vendor check prevents AMD from being misidentified as QSV.
    has_qsv = (
        has_intel_dri
        or "intel_qsv" in available_types
        or hw_type == "intel_qsv"
        or any("_qsv" in e for e in available_encoders)
    ) and (os.name == "nt" or has_dri or hw_type == "intel_qsv")
    has_vaapi = (
        has_dri
        or any("vaapi" in value for value in available_types)
        or "vaapi" in hw_type
        or hw_type == "intel_qsv"
    )
    has_amf = (
        os.name == "nt"
        and (
            "amd_amf" in available_types
            or hw_type == "amd_amf"
            or any(encoder in AMF_ENCODERS for encoder in available_encoders)
        )
    )

    if has_nvenc:
        _wait_for_nv_runtime_ready(timeout_s=30.0, interval_s=2.0)

    test_codecs: list[str] = []
    if has_nvenc:
        test_codecs.extend(["h264_nvenc", "hevc_nvenc", "av1_nvenc"])
    if has_qsv:
        test_codecs.extend(["h264_qsv", "hevc_qsv", "av1_qsv"])
    if has_vaapi:
        test_codecs.extend(["h264_vaapi", "hevc_vaapi", "av1_vaapi"])
    if has_amf:
        test_codecs.extend(["h264_amf", "hevc_amf", "av1_amf"])
    test_codecs.extend(["libx264", "libx265", "libsvtav1"])

    hw_decoders = {}
    if has_nvenc:
        hw_decoders = {
            "h264_nvenc": ("h264", ["-hwaccel", "cuda", "-c:v", "h264_cuvid"]),
            "hevc_nvenc": ("hevc", ["-hwaccel", "cuda", "-c:v", "hevc_cuvid"]),
            "av1_nvenc": ("av1", ["-hwaccel", "cuda", "-c:v", "av1_cuvid"]),
        }

    logger.info(
        "Encoder validation: type=%s vaapi_device=%s candidates=%s",
        hw_type, hw_info.get("vaapi_device"), ", ".join(test_codecs),
    )
    cache: Dict[str, bool] = {}
    test_results: dict[str, tuple[str, str, bool | None, str, bool]] = {}

    for codec in test_codecs:
        try:
            actual_encoder, _v_flags, init_hw_flags = map_codec_to_hw(codec, hw_info)
            if not is_encoder_available(actual_encoder):
                cache[f"{actual_encoder}:{':'.join(init_hw_flags)}"] = False
                test_results[codec] = (actual_encoder, "UNAVAILABLE", None, "Not in ffmpeg -encoders", False)
                logger.warning("[%s] unavailable in ffmpeg build", codec)
                continue

            decode_passed: bool | None = None
            if codec in hw_decoders:
                format_name, decoder_flags = hw_decoders[codec]
                decode_passed, decode_message = test_decoder(format_name, decoder_flags)
                logger.info("[%s] decode=%s (%s)", codec, decode_passed, decode_message)

            cache_key = f"{actual_encoder}:{':'.join(init_hw_flags)}"
            encode_passed, encode_message = test_encoder_init(actual_encoder, init_hw_flags)
            cache[cache_key] = encode_passed
            test_results[codec] = (
                actual_encoder,
                "PASS" if encode_passed and (decode_passed is None or decode_passed) else "FAIL",
                decode_passed,
                encode_message,
                encode_passed,
            )
            logger.info("[%s] encode=%s (%s)", codec, encode_passed, encode_message)
            sys.stdout.flush()
        except Exception as exc:
            logger.exception("[%s] startup test failed", codec)
            test_results[codec] = ("unknown", "ERROR", None, str(exc), False)

    passed = sum(status == "PASS" for _, status, _, _, _ in test_results.values())
    failed = len(test_results) - passed
    logger.info("Encoder validation complete: tested=%s passed=%s failed=%s", len(test_results), passed, failed)

    # Make this rerun the worker's authoritative snapshot.  In particular,
    # do not leave a previous PASS for a device/codec that disappeared.
    generation = str(hw_info.get("probe_generation") or "")
    hw_info["encoder_test_results"] = {
        codec: {
            "codec": codec,
            "actual_encoder": actual_encoder,
            "encode_passed": encode_passed,
            "decode_passed": decode_status,
            "passed": status == "PASS",
            "message": message or ("OK" if encode_passed else "Failed during init"),
            "probe_generation": generation,
        }
        for codec, (actual_encoder, status, decode_status, message, encode_passed)
        in test_results.items()
    }
    hw_info["tested_encoders"] = {
        actual_encoder: bool(encode_passed)
        for actual_encoder, _status, _decode_status, _message, encode_passed
        in test_results.values()
        if actual_encoder.endswith(("_nvenc", "_qsv", "_vaapi", "_amf"))
    }
    hw_info["encoder_test_generation"] = generation
    hw_info["encoder_test_timestamp"] = int(time.time())

    # Persist both the compact status and details consumed by the API.
    try:
        if os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}:
            from shared.local_runtime import get_sync_redis

            redis_client = get_sync_redis()
        else:
            from redis import Redis

            redis_client = Redis.from_url(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                decode_responses=True,
            )
        # These are the only encoder-test keys owned by this application.
        # Clear the complete known set before writing the new snapshot so a
        # removed GPU or unsupported codec cannot survive for 30 days in Redis.
        known_codecs = [
            "h264_nvenc", "hevc_nvenc", "av1_nvenc",
            "h264_qsv", "hevc_qsv", "av1_qsv",
            "h264_vaapi", "hevc_vaapi", "av1_vaapi",
            "h264_amf", "hevc_amf", "av1_amf",
            "libx264", "libx265", "libsvtav1",
        ]
        redis_client.delete(*[
            key
            for codec in known_codecs
            for key in (
                f"encoder_test:{codec}",
                f"encoder_test_json:{codec}",
                f"encoder_test_decode_json:{codec}",
            )
        ])
        for codec, (actual_encoder, status, decode_status, message, encode_passed) in test_results.items():
            overall_passed = status == "PASS"
            redis_client.setex(
                f"encoder_test:{codec}", 2592000, "1" if overall_passed else "0"
            )
            redis_client.setex(
                f"encoder_test_json:{codec}",
                2592000,
                json.dumps({
                    "codec": codec,
                    "actual_encoder": actual_encoder,
                    "passed": encode_passed,
                    "message": message or ("OK" if encode_passed else "Failed during init"),
                    "probe_generation": generation,
                }),
            )
            if decode_status is not None:
                redis_client.setex(
                    f"encoder_test_decode_json:{codec}",
                    2592000,
                    json.dumps({
                        "codec": codec,
                        "passed": decode_status,
                        "message": "OK" if decode_status else "Decoder failed",
                        "probe_generation": generation,
                    }),
                )
    except Exception as exc:
        logger.warning("Failed to store encoder test results in Redis: %s", exc)

    return cache
