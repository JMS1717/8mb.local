#!/usr/bin/env bash
set -euo pipefail

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
  if [[ -n "${DRIVER_MIN}" ]]; then
    if ! ver_ge "${DRV}" "${DRIVER_MIN}"; then
      warn "Your NVIDIA driver (${DRV}) is below the minimum (${DRIVER_MIN}) expected for this image."
      if [[ "${FLAVOR}" == "latest" ]]; then
        err  "This :latest image targets newer GPUs/drivers. Please use the :legacy tag on older drivers (e.g., 535.x)."
      else
        warn "This :legacy image is intended for older drivers. If you're on a 50-series (Blackwell) GPU, use :latest."
      fi
    fi
  fi
fi

# Optional: quick NVENC probe to provide clearer guidance
if command -v ffmpeg >/dev/null 2>&1; then
  if ! ffmpeg -hide_banner -encoders 2>&1 | grep -qi nvenc; then
    warn "NVENC encoders not listed by ffmpeg."
    warn "If you expected NVIDIA acceleration: ensure Docker runs with --gpus all and NVIDIA_DRIVER_CAPABILITIES=compute,video,utility."
    if [[ "${FLAVOR}" == "legacy" ]]; then
      warn "If you are on RTX 50-series (Blackwell), this legacy image may fail NV runtime init. Use :latest instead."
    elif [[ "${FLAVOR}" == "latest" ]]; then
      warn "If your host driver is older (e.g., 535.x), this latest image may be incompatible. Use :legacy instead."
    fi
  fi
fi

# Continue to main command
exec "$@"
