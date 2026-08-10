# GPU support

8mb.local validates the encoder against the device passed into the container
and falls back to a CPU encoder whenever the runtime probe fails. The shipped
image supports:

1. NVIDIA NVENC (and optional CUDA decode)
2. Intel Quick Sync on Linux through the VAAPI render node
3. Linux VAAPI for Intel and AMD GPUs
4. Windows AMD AMF (`h264_amf`, `hevc_amf`, `av1_amf`, when the GPU/driver exposes them)
5. CPU software encoders (`libx264`, `libx265`, and SVT-AV1 via FFmpeg's
   `libsvtav1` token)

AMD acceleration on Linux is VAAPI. AMD AMF is probed only by the native
Windows runtime; the Docker path remains Linux-native.

## Encoder mapping

| Codec family | NVIDIA | Intel | AMD Windows | AMD / generic Linux | CPU fallback |
|---|---|---|---|---|---|
| H.264 | `h264_nvenc` | `h264_qsv` | `h264_amf` | `h264_vaapi` | `libx264` |
| HEVC | `hevc_nvenc` | `hevc_qsv` | `hevc_amf` | `hevc_vaapi` | `libx265` |
| AV1 | `av1_nvenc` | `av1_qsv` (if supported) | `av1_amf` (if supported) | `av1_vaapi` (if supported) | SVT-AV1 (`libsvtav1`) |

`libsvtav1` is the canonical FFmpeg encoder name for the SVT-AV1 project.
The `lib` prefix is FFmpeg's external-library wrapper name; it does not mean
the slower libaom encoder. Legacy `libaom-av1` settings are retained only for
backward-compatible migration and are not offered or selected automatically.

The preferred codec is selected in AV1 → HEVC → H.264 order, with NVIDIA →
QSV → AMF → VAAPI → CPU priority within a family. A device is not considered
available just because FFmpeg lists its encoder.

## Intel Quick Sync and VAAPI

Use the dedicated compose profile:

```sh
cp .env.example .env
docker compose -f docker-compose.vaapi.yml up -d --build
```

The profile passes `/dev/dri` into the container and adds the common `video`
and `render` group IDs. If the host has multiple GPUs, set `VAAPI_DEVICE` to a
specific render node (for example `/dev/dri/renderD129`). The worker discovers
render nodes when the variable is empty and checks the sysfs vendor before it
attempts QSV, so an AMD node is never misidentified as Intel QSV.

The encoder path uses software decode and scaling followed by an explicit
hardware upload: `format=nv12,hwupload` for QSV and
`format=nv12|vaapi,hwupload` for VAAPI. This is intentional: it preserves
rotation metadata and makes scaling/fps filters consistent across Intel and
AMD driver versions.
The startup test and the job command use the same VAAPI/QSV initialization
sequence.

Useful checks inside the running container:

```sh
ls -l /dev/dri
vainfo --display drm --device /dev/dri/renderD128
ffmpeg -hide_banner -encoders | grep -E 'qsv|vaapi'
```

The Settings page's hardware test runs one-frame QSV and VAAPI smoke tests and
records the result in Redis. The `/api/diagnostics/gpu` endpoint reports the
same checks without changing files. On Windows it also runs an AMF smoke test;
the desktop runtime stores the result in its local state store.

## NVIDIA

The default `docker-compose.yml` uses NVIDIA Container Toolkit and `gpus: all`:

```sh
docker compose up -d --build
```

The host must provide a working `nvidia-smi`; the container also needs the
video capability. The worker probes NVENC at startup and uses CPU fallback if
the driver, device node, or encoder API is unavailable.

## CPU-only

Use the CPU profile on hosts without NVIDIA or `/dev/dri` passthrough:

```sh
docker compose -f docker-compose.cpu.yml up -d --build
```

No GPU options are required. CPU encoders remain available even if a hardware
probe fails, so a driver issue does not make a queued job unrecoverable.

## Rate control and limitations

The product targets a total output size. Hardware encoders have vendor-specific
rate-control behavior, so the worker verifies the output and can perform up to
two bitrate adjustments. If a hardware encode fails at runtime, the last
working command is converted to a CPU-safe filter chain and retried; hardware
filters such as `hwdownload` and `hwupload` are never reused on the CPU path.

Hardware support still depends on the GPU generation, driver, FFmpeg build, and
codec support. For example, many Intel/AMD devices do not expose AV1 encode;
the detector will keep H.264/HEVC or CPU AV1 instead of presenting a false
option.
