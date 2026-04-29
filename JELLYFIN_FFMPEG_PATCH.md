# Jellyfin FFmpeg 7.1 Integration (Intel QSV/VAAPI + NVIDIA NVENC) for 8mb.local

**Author:** Patch by OneCreek (https://github.com/OneCreek), based on [JMS1717/8mb.local](https://github.com/JMS1717/8mb.local)  
**Date:** April 2026  
**Goal:** Add Intel VAAPI (+ QSV) hardware encoding alongside existing NVIDIA NVENC support.

---

## Summary

This patch adds **Intel VAAPI** and **Intel QSV** (Quick Sync Video) hardware encoding to 8mb.local. VAAPI works with FFmpeg 6.1+, QSV requires FFmpeg 7.0+ (via `libvpl`). The VAAPI-only variant described here uses FFmpeg 6.1.1 and `ubuntu:22.04` as base image instead of `nvidia/cuda`, saving ~3 GB image size.

All changes are **additive** — existing NVIDIA NVENC support is fully preserved. The encoder priority is: **NVENC → QSV → VAAPI → CPU** (auto-detected at startup).

---

## Changed Files (13 files)

### Layer 1: Worker (FFmpeg integration)

#### 1. `worker/app/constants.py`
- **Added** QSV encoder constants: `H264_QSV`, `HEVC_QSV`, `AV1_QSV`
- **Added** VAAPI encoder constants: `H264_VAAPI`, `HEVC_VAAPI`, `AV1_VAAPI`
- **Changed** priority lists from `[NVENC, CPU]` → `[NVENC, QSV, VAAPI, CPU]`
- **Added** `CPU_FALLBACK` entries for all new encoders
- **Added** `QSV_ENCODERS` and `VAAPI_ENCODERS` frozensets
- **Added** `QSV_PRESET_MAP` (maps p1–p7 / ultrafast–veryslow to QSV presets)

#### 2. `worker/app/hw_detect.py`
- **Added** `VAAPI_DEVICE` env var support (default: `/dev/dri/renderD128`)
- **Added** `NO_QSV` / `NO_VAAPI` env vars to disable specific backends
- **Added** `_detect_vaapi_driver()` — auto-detects best driver (iHD → i965 → system-default)
- **Added** `_test_qsv()` — tests QSV via VAAPI backend (`-init_hw_device vaapi=va:DEV -init_hw_device qsv=qs@va`)
- **Added** `_test_vaapi()` — tests VAAPI with proper hwaccel flags and filter chain
- **Changed** `detect_hw_accel()` to probe QSV/VAAPI after NVENC
- **Changed** `map_codec_to_hw()` to return QSV/VAAPI encoders when detected

#### 3. `worker/app/encoder.py`
- **Changed** `_build_video_filters()`:
  - QSV: Uses `scale_qsv` (HW scaling) + `hwmap=derive_device=qsv,format=qsv`
  - VAAPI: Uses CPU `scale` + `format=nv12|vaapi,hwupload`
  - NVENC/CPU: Unchanged (standard `scale` filter)
- **Changed** `_encoder_quality_flags()`:
  - QSV: `-global_quality` (ICQ mode) + `-look_ahead 1`
  - VAAPI: `-rc_mode CQP` + `-qp` (or VBR with quality)
  - NVENC/CPU: Unchanged

#### 4. `worker/app/startup_tests.py`
- **Changed** `test_encoder_init()` — critical fix:
  - VAAPI encoders now tested with `-hwaccel vaapi -hwaccel_device DEV -vf format=nv12|vaapi,hwupload`
  - QSV encoders tested with `-init_hw_device vaapi=va:DEV -init_hw_device qsv=qs@va -vf hwmap=derive_device=qsv,format=qsv`
  - Without these flags, VAAPI/QSV tests fail with "Error reinitializing filters!"
- **Added** VAAPI/QSV test entries in `run_startup_tests()`
- **Added** logging for `VAAPI_DEVICE` and `LIBVA_DRIVER_NAME` env vars
- **Increased** encoder test timeout from 8s → 15s (VAAPI init can be slow)

### Layer 2: Backend API (FastAPI)

#### 5. `backend-api/app/models.py`
- **Expanded** all `Literal` type unions with QSV/VAAPI codecs:
  - `CompressRequest.video_codec`
  - `DefaultPresets.video_codec`
  - `PresetProfile.video_codec`
- **Added** QSV/VAAPI fields to `CodecVisibilitySettings` (h264_qsv, hevc_qsv, av1_qsv, h264_vaapi, hevc_vaapi, av1_vaapi)
- **Changed** `hardware_type` comment: `nvidia, cpu` → `nvidia, qsv, vaapi, cpu`

#### 6. `backend-api/app/routers/system.py`
- **Added** QSV/VAAPI codecs to `test_codecs` list and codec_map
- **Changed** `is_hardware` check: was `endswith("_nvenc")` → now also checks `_qsv` and `_vaapi`
- **Added** `_matches_hw()` logic for `qsv` and `vaapi` hardware types
- **Added** VAAPI diagnostics: `vainfo`, `/dev/dri` listing, VAAPI smoke test
- **Added** summary flags: `ffmpeg_sees_vaapi`, `ffmpeg_has_qsv`, `ffmpeg_has_vaapi`, `vaapi_encode_ok`

#### 7. `backend-api/app/settings_manager.py`
- **Added** QSV/VAAPI to default `codec_visibility` settings
- **Added** `CODEC_*` env var overrides (e.g., `CODEC_H264_QSV=true` in docker-compose)
- **Added** QSV/VAAPI to `valid_keys` in `update_codec_visibility_settings()`

### Layer 3: Frontend (SvelteKit)

#### 8. `frontend/src/routes/+page.svelte`
- **Added** QSV/VAAPI entries to `buildCodecList` (dropdown options)
- **Fixed** encoder badge: was only checking `_nvenc$` → now also shows "Intel QSV" (blue) and "VAAPI" (amber) badges
- **Added** cancel button timeout: 3s force-reset if server doesn't send 'canceled' SSE event

#### 9. `frontend/src/routes/settings/+page.svelte`
- **Added** QSV/VAAPI to `CodecVisibilitySettings` type and initial state
- **Added** QSV/VAAPI checkboxes in codec visibility section (QSV = blue, VAAPI = amber)
- **Updated** description text to mention QSV and VAAPI

#### 10. `frontend/src/routes/gpu-support/+page.svelte`
- **Rewritten** to include Intel QSV and VAAPI support tables alongside NVIDIA
- **Added** QSV support matrix (Skylake through Arrow Lake + NAS platforms)
- **Added** VAAPI section with driver info (iHD vs i965)

### Layer 4: Docker

#### 11. `Dockerfile`
- **Changed** build stage base: `nvidia/cuda:12.2.0-devel-ubuntu22.04` → `ubuntu:22.04` (saves ~3 GB)
- **Changed** runtime stage base: `nvidia/cuda:12.2.0-runtime-ubuntu22.04` → `ubuntu:22.04`
- **Removed** NVIDIA NVENC headers clone (`nv-codec-headers`)
- **Removed** CUDA-specific configure flags (`--enable-cuda-nvcc`, `--enable-libnpp`, `--enable-nvenc`, `--extra-cflags`, `--extra-ldflags`)
- **Added** build deps: `libva-dev`, `libdrm-dev`, `nasm`
- **Added** FFmpeg configure: `--enable-vaapi`, `--enable-libdrm`
- **Added** runtime packages: `libva-drm2`, `libva-x11-2`, `libdrm2`, `intel-media-va-driver-non-free`, `i965-va-driver`, `vainfo`, `intel-gpu-tools`
- **Removed** `NVIDIA_DRIVER_CAPABILITIES` and `NVIDIA_VISIBLE_DEVICES` env vars

#### 12. `docker-compose.yml`
- **Added** `/dev/dri:/dev/dri` device mount (Intel iGPU access)
- **Added** environment variables: `VAAPI_DEVICE=/dev/dri/renderD128`, `LIBVA_DRIVER_NAME=iHD`

---

## Host Requirements

```bash
# Intel iGPU must be accessible
ls -la /dev/dri/renderD128

# VAAPI driver must be installed on the HOST (for passthrough)
sudo apt install intel-media-va-driver-non-free vainfo

# Verify VAAPI works on host
vainfo --display drm --device /dev/dri/renderD128

# Container user needs render group access
# Check your render group GID:
stat -c '%g' /dev/dri/renderD128
```

## Docker Compose (minimal example)

```yaml
services:
  8mblocal:
    image: 8mb-local-custom:latest
    ports:
      - "8001:8001"
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "105"  # render group GID on your host
    environment:
      - VAAPI_DEVICE=/dev/dri/renderD128
      - LIBVA_DRIVER_NAME=iHD  # or i965 for pre-Broadwell
    volumes:
      - ./uploads:/app/uploads
      - ./outputs:/app/outputs
```

## QSV Support (requires FFmpeg 7.0+)

For QSV support, upgrade FFmpeg to 7.1 in the Dockerfile:
- Change `ffmpeg-6.1.1.tar.xz` → `ffmpeg-7.1.tar.xz`
- Add `libvpl-dev` to build deps
- Add `--enable-libvpl` to configure
- Add `libvpl2` to runtime packages

QSV initializes via VAAPI backend:
```
-init_hw_device vaapi=va:/dev/dri/renderD128 -init_hw_device qsv=qs@va
```

## Key Technical Decisions

1. **VAAPI filter chain**: `format=nv12|vaapi,hwupload` — the `nv12|vaapi` passthrough format avoids unnecessary pixel format conversion when input is already in VAAPI surfaces.

2. **QSV via VAAPI backend** (not libmfx): Modern approach that works without the deprecated MediaSDK. Uses `-init_hw_device vaapi=va:DEV -init_hw_device qsv=qs@va`.

3. **Driver auto-detection**: `hw_detect.py` tries iHD first (modern Intel, Gen8+), then i965 (older), then system default. Can be overridden with `LIBVA_DRIVER_NAME` env var.

4. **Startup encoder tests**: Each encoder is tested by actually encoding 1 frame with the correct hwaccel flags — not just checking `ffmpeg -encoders`. This prevents false positives.

5. **Graceful fallback**: If QSV/VAAPI fails, falls back to CPU automatically. Priority chain: NVENC → QSV → VAAPI → CPU.

---

## Development Notes

This patch was developed with assistance from **Claude (Anthropic)** — an AI coding assistant used for code generation, testing, and documentation.
