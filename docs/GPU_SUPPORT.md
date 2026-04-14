# GPU support (NVIDIA + CPU)

## Why only NVIDIA and CPU?

8mb.local targets **strict output sizes** (approximate total bitrate / MB budget). In real testing, **Intel QSV, VAAPI, and AMD AMF** paths did not behave reliably in that pipeline: encoders often failed to honor the constraints we need, broke in common container/driver setups, or simply **did not work well enough** to ship. Rather than maintain half-working vendor branches, the codebase was simplified to:

1. **NVIDIA NVENC** (when an NVIDIA GPU is available and passes startup checks)
2. **CPU** (libx264, libx265, libaom-av1 / SVT-AV1 as configured)

Legacy multi-vendor documentation and compose overrides were removed. **Use an NVIDIA GPU with the official container toolkit, or CPU encoding.**

## Hardware detection

The worker detects NVIDIA via `nvidia-smi` / CUDA and validates NVENC with startup tests. If hardware encoding is unavailable or fails, it falls back to CPU encoders. Logs show which path is in use for each job.

## Encoder mapping (simplified)

| User-facing choice | Typical encoder |
|--------------------|-----------------|
| H.264 | `h264_nvenc` or `libx264` |
| HEVC (H.265) | `hevc_nvenc` or `libx265` |
| AV1 | `av1_nvenc` or CPU AV1 (e.g. libaom-av1) |

**AV1 input note:** When decoding AV1 before NVENC encode, the worker probes `av1_cuvid`. If the GPU/driver cannot decode AV1, encoding uses **software decode (`libdav1d`)** so the job still completes.

## Preset / tune (NVENC)

- Presets: **p1–p7** (mapped for CPU encoders when on software fallback)
- Tune: **hq**, **ll**, **ull**, **lossless** (NVENC-oriented; CPU paths use sensible defaults)

## Docker (NVIDIA)

See `docker-compose.yml` and `README.md`. Typical requirements:

- NVIDIA drivers on the host
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- `gpus: all` (or equivalent) and `NVIDIA_VISIBLE_DEVICES` / `NVIDIA_DRIVER_CAPABILITIES` as in the compose file

### CPU-only

No GPU flags required. The same image runs; encoding uses CPU codecs (slower).

## Performance (rough reference)

Approximate relative speeds depend on resolution, preset, and content. NVIDIA NVENC is far faster than CPU for comparable presets; see `README.md` for updated hardware guidance (e.g. RTX 40/50 series).

## Troubleshooting (NVIDIA)

**“CPU” or software encoder when you expect NVENC**

1. `nvidia-smi` on the host shows the GPU.
2. Container was started with GPU access (`docker run --gpus all` or compose `gpus: all`).
3. Check worker logs for startup encoder tests and any NVENC initialization errors.
4. From inside the container: `ffmpeg -hide_banner -encoders | grep nvenc`

**AV1 decode errors (`av1_cuvid` not supported)**

The worker should fall back to `libdav1d` automatically after a failed probe. If a job still fails, capture full FFmpeg stderr from the job log.

## FFmpeg in the image

The unified `Dockerfile` builds FFmpeg with **CUDA/NVENC/NPP** and CPU libraries (x264, x265, dav1d, aom, etc.). Intel/AMD-specific FFmpeg configure flags and runtime packages are **not** included.

## Historical note

Older changelogs and branches may mention QSV/VAAPI/AMF. That support was **removed intentionally** after it proved **unsuitable for this product’s rate-control model and did not work reliably** in practice—not because NVIDIA is the only theoretically viable vendor.
