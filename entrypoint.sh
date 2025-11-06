#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

# Diagnostic trap so silent failures surface in docker logs
trap 'code=$?; echo "[ENTRYPOINT ERROR] Command \"$BASH_COMMAND\" failed (exit $code) at line $LINENO" >&2; exit $code' ERR

echo "== 8mblocal entrypoint starting (flavor=${BUILD_FLAVOR:-unset}) =="

# Simple dotted version compare: returns 0 if $1 >= $2
ver_ge() {
  # normalize versions to same field count by padding with .0
  local IFS=.
  local i ver1=($1) ver2=($2)
  # fill empty fields in ver1 with zeros
  for ((i=${#ver1[@]}; i<${#ver2[@]}; i++)); do
    ver1[i]=0
  done
  # fill empty fields in ver2 with zeros
  for ((i=${#ver2[@]}; i<${#ver1[@]}; i++)); do
    ver2[i]=0
  done
  for ((i=0; i<${#ver1[@]}; i++)); do
    if ((10#${ver1[i]} > 10#${ver2[i]})); then return 0; fi
    if ((10#${ver1[i]} < 10#${ver2[i]})); then return 1; fi
  done
  return 0
}

bold() { echo -e "\033[1m$*\033[0m"; }
warn() { echo -e "\033[33m$*\033[0m" 1>&2; }
err()  { echo -e "\033[31m$*\033[0m" 1>&2; }

FLAVOR=${BUILD_FLAVOR:-unknown}
CUDA_VER=${BUILD_CUDA:-unknown}
FFMPEG_VER=${BUILD_FFMPEG:-unknown}
DRIVER_MIN=${DRIVER_MIN:-0}

# Ensure common NVIDIA/CUDA library paths are globally available (esp. under WSL2)
# Do this early so every child process inherits it (ffmpeg dlopen for NVENC/NVDEC)
WSL_DRV_DIR=""
if [ -d "/usr/lib/wsl/drivers" ]; then
  # Find first matching WSL driver directory if present; avoid set -e failures when none exist
  WSL_DRV_DIR=$(find /usr/lib/wsl/drivers -type d -name 'nvami.inf_*' -print -quit 2>/dev/null || true)
fi
if [ -n "$WSL_DRV_DIR" ] && [ -f "$WSL_DRV_DIR/libcuda.so.1.1" ]; then
    echo "Using WSL2 CUDA driver for RTX 50-series support: $WSL_DRV_DIR"
    
  # RTX 50-series fix: Work around stub libcuda that blocks the real WSL driver
  # The nvidia/cuda base image includes a 172KB stub libcuda.so.1 that doesn't work with WSL2
  # nvidia-container-toolkit mounts this file, so we can't delete it, but we can use LD_PRELOAD
  if [ -f "/usr/lib/x86_64-linux-gnu/libcuda.so.1" ]; then
    STUB_SIZE=$(stat -c%s "/usr/lib/x86_64-linux-gnu/libcuda.so.1" 2>/dev/null || echo "0")
    if [ "$STUB_SIZE" -lt "1000000" ]; then  # Real driver is ~26MB, stub is ~172KB
      echo "Found stub libcuda.so.1 ($STUB_SIZE bytes), using LD_PRELOAD to force real WSL driver..."
      # Preload core NVIDIA driver libs from WSL driver dir to avoid picking stubs from CUDA compat
      PRELOAD_LIST=""
      for lib in libcuda.so.1.1 libnvcuvid.so.1 libnvidia-encode.so.1; do
        if [ -f "$WSL_DRV_DIR/$lib" ]; then
          PRELOAD_LIST+="$WSL_DRV_DIR/$lib "
        fi
      done
      # Apply LD_PRELOAD if we found any
      if [ -n "$PRELOAD_LIST" ]; then
        export LD_PRELOAD="${PRELOAD_LIST}${LD_PRELOAD:-}"
        echo "✓ LD_PRELOAD set to: $PRELOAD_LIST"
      else
        # Fallback: at least force libcuda if present
        if [ -f "$WSL_DRV_DIR/libcuda.so.1.1" ]; then
          export LD_PRELOAD="$WSL_DRV_DIR/libcuda.so.1.1 ${LD_PRELOAD:-}"
          echo "✓ LD_PRELOAD set to: $WSL_DRV_DIR/libcuda.so.1.1"
        fi
      fi
    fi
  fi
    
    # Set library paths with WSL driver directory prioritized
    LIB_PATHS="${WSL_DRV_DIR}:/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64:/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu"
else
    echo "Warning: WSL driver not found, using standard paths"
    # Fallback: use standard paths
    LIB_PATHS="/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64:/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu"
fi
export LD_LIBRARY_PATH="${LIB_PATHS}:${LD_LIBRARY_PATH:-}"
export PATH="/usr/local/cuda/bin:/usr/local/nvidia/bin:${PATH}"

# Try to detect NVIDIA driver version if GPU is present
DRV=""
if command -v nvidia-smi >/dev/null 2>&1; then
  DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1 || true)
fi
if [[ -z "${DRV}" && -r /proc/driver/nvidia/version ]]; then
  # Example: NVRM version: NVIDIA UNIX x86_64 Kernel Module  535.104.05  Release Date: ...
  DRV=$(grep -oE "[0-9]{3}(\.[0-9]{1,3}){0,2}" /proc/driver/nvidia/version | head -n1 || true)
fi

# If GPU not present, continue (CPU mode is supported)
if [[ -z "${DRV}" ]]; then
  warn "NVIDIA driver not detected inside container. Running in CPU/VAAPI mode if available."
else
  echo "Detected NVIDIA driver: ${DRV} (image flavor: ${FLAVOR}, CUDA ${CUDA_VER}, FFmpeg ${FFMPEG_VER})"
  # Quick diagnostics for NVENC/NVDEC runtime libs
  if command -v ldconfig >/dev/null 2>&1; then
    if ! ldconfig -p | grep -qE 'libnvidia-encode\.so'; then
      warn "libnvidia-encode.so not found via ldconfig search path; NVENC may fail."
    fi
    if ! ldconfig -p | grep -qE 'libnvcuvid\.so'; then
      warn "libnvcuvid.so not found via ldconfig search path; NVDEC/CUVID may fail."
    fi
  fi
  if [[ -n "${DRIVER_MIN}" ]]; then
      if ! ver_ge "${DRV}" "${DRIVER_MIN}"; then
        warn "Your NVIDIA driver (${DRV}) is below the minimum (${DRIVER_MIN}) expected for this image."
        case "${FLAVOR}" in
          cuda13)
            err "This :cuda13 image targets newer GPUs/drivers (Blackwell / driver 550+). Please use :latest on 535.x drivers."
            ;;
          latest)
            warn "Unexpected: :latest min driver should be 535.x. Continuing in degraded (CPU) mode."
            ;;
          *)
            warn "Unknown flavor ${FLAVOR}; continuing."
            ;;
        esac
      fi
  fi
fi

# Optional: quick NVENC probe to provide clearer guidance (best-effort; don't be noisy on false negatives)
if command -v ffmpeg >/dev/null 2>&1; then
  # Capture output to avoid set -o pipefail pitfalls and treat probe as best-effort
  ENC_OUT=$(ffmpeg -hide_banner -encoders 2>/dev/null || true)
  if ! echo "$ENC_OUT" | grep -qiE "(_nvenc)"; then
    warn "NVENC encoders not listed by ffmpeg."
    warn "If you expected NVIDIA acceleration: ensure Docker runs with --gpus all and NVIDIA_DRIVER_CAPABILITIES=compute,video,utility."
    case "${FLAVOR}" in
      latest)
        warn "If you're on RTX 50-series with driver 550+, consider :cuda13 for CUDA 13 / FFmpeg 8."
        ;;
      cuda13)
        warn "If your host driver is 535.x, use :latest instead (this image expects 550+)."
        ;;
    esac
  fi
fi

# Continue to main command
exec "$@"
