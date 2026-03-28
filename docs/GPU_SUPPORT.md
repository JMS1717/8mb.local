# Multi-Vendor GPU Support

## Overview

8mb.local supports hardware-accelerated video encoding across multiple GPU vendors and automatically detects the best available option at runtime:

1. **NVIDIA NVENC** (CUDA-based)
2. **Intel Quick Sync Video (QSV)**
3. **AMD AMF** (Windows) / **VAAPI** (Linux)
4. **Apple VideoToolbox** (macOS/Apple Silicon)
5. **CPU fallback** (software encoding)

## Hardware Detection

The worker automatically detects available hardware acceleration when it starts processing a video. The detection logic:

1. Checks for NVIDIA GPU via `nvidia-smi` and CUDA availability
2. Checks for Intel QSV via FFmpeg `hwaccels` and encoder availability
3. Checks for AMD AMF (Windows) or VAAPI (Linux) support
4. Checks for Apple VideoToolbox on macOS (requires native worker)
5. Falls back to CPU software encoding if no GPU is detected

Detection results are logged at the start of each compression job: "Hardware: NVIDIA acceleration detected" (or INTEL/AMD/CPU).

## Encoder Mapping

Based on detected hardware, user codec requests are automatically mapped:

| User Request | NVIDIA | Intel QSV | AMD AMF/VAAPI | VideoToolbox | CPU Fallback |
|--------------|--------|-----------|---------------|--------------|--------------|
| H.264 | h264_nvenc | h264_qsv | h264_amf/h264_vaapi | h264_videotoolbox | libx264 |
| HEVC (H.265) | hevc_nvenc | hevc_qsv | hevc_amf/hevc_vaapi | hevc_videotoolbox | libx265 |
| AV1 | av1_nvenc | av1_qsv | av1_amf/av1_vaapi | N/A* | libsvtav1 |

*VideoToolbox does not support AV1 encoding. AV1 requests on macOS fall back to CPU encoding.

### AV1 Support Notes

- **NVIDIA**: RTX 40-series and newer (Ada Lovelace architecture)
- **Intel**: Arc GPUs (Alchemist and newer)
- **AMD**: RDNA 3 architecture and newer (RX 7000 series)
- **CPU**: Always available via libsvtav1 or libaom-av1

## Preset/Tune Mapping

Different encoders support different preset and tune parameters. The worker automatically translates them:

### NVIDIA NVENC
- Presets: p1-p7 (native support)
- Tune: hq, ll, ull, lossless (native support)

### Intel QSV
- Presets: Mapped from p1-p7 → veryfast/faster/fast/medium/slow/slower/veryslow
- Tune: Not supported (ignored)

### AMD AMF
- Presets: Mapped from p1-p7 → speed/balanced/quality
- Tune: Not supported (ignored)

### VAAPI (Linux AMD/Intel)
- Presets: Fixed compression_level=7
- Tune: Not supported (ignored)

### CPU (libx264/libx265)
- Presets: Mapped from p1-p7 → ultrafast/superfast/veryfast/faster/fast/medium/slow
- Tune: film (libx264), grain (libx265)

## Docker Configuration

### NVIDIA GPUs

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
environment:
  - NVIDIA_VISIBLE_DEVICES=all
  - NVIDIA_DRIVER_CAPABILITIES=all
```

**Requirements:**
- NVIDIA drivers installed on host
- NVIDIA Container Toolkit installed
- Docker configured with GPU support

### Intel GPUs

```yaml
devices:
  - /dev/dri:/dev/dri
```

**Requirements:**
- Intel GPU with QSV support (6th gen Core or newer, Arc GPUs)
- Intel compute runtime / media drivers installed
- `/dev/dri` device accessible

### AMD GPUs

#### Linux (VAAPI)
```yaml
devices:
  - /dev/dri:/dev/dri
```

**Requirements:**
- AMD GPU with VAAPI support
- Mesa drivers with VAAPI support
- `/dev/dri` device accessible

#### Windows (AMF)
- AMD drivers installed
- Docker Desktop with GPU support
- No special configuration needed

### CPU-Only Systems

No special configuration needed. Works out of the box with software encoders.

### macOS with Apple Silicon (VideoToolbox)

Apple Silicon Macs (M1/M2/M3/M4) require a hybrid setup because Docker Desktop on macOS runs in a Linux VM without GPU access:

```
┌─────────────────────────────────────────────────────┐
│              Docker (Linux containers)               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │   Redis     │  │  Frontend   │  │ Backend API │ │
│  │   :6379     │  │   :5173     │  │   :8001     │ │
│  └──────┬──────┘  └─────────────┘  └──────┬──────┘ │
└─────────┼──────────────────────────────────┼───────┘
          │ REDIS_URL                        │
          ▼                                  ▼
┌─────────────────────────────────────────────────────┐
│            Native macOS (Apple Silicon)              │
│  ┌──────────────────────────────────────────────┐   │
│  │           Celery Worker (native)             │   │
│  │  • FFmpeg with VideoToolbox                  │   │
│  │  • h264_videotoolbox, hevc_videotoolbox      │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

**Setup:**

1. Run the Docker containers using the macOS-specific compose file:
   ```bash
   docker-compose -f docker-compose.macos.yml up -d
   ```

2. Run the setup script to install dependencies and configure the native worker:
   ```bash
   ./scripts/macos-setup.sh
   ```

3. Start the native worker:
   ```bash
   source venv/bin/activate
   export REDIS_URL=redis://localhost:6380/0
   export UPLOAD_DIR=./uploads
   export OUTPUT_DIR=./outputs
   export PYTHONPATH=.:./backend-api:./worker
   celery -A worker.celery_app worker --loglevel=info
   ```

**Requirements:**
- macOS 11+ (Big Sur or newer)
- Apple Silicon (M1/M2/M3/M4) or Intel Mac with T2 chip
- Homebrew
- FFmpeg with VideoToolbox support (`brew install ffmpeg`)
- Python 3.10+

**Notes:**
- VideoToolbox supports H.264 and HEVC encoding only
- AV1 encoding falls back to CPU (libaom-av1)
- Redis is exposed on port 6380 to avoid conflicts with native Redis

## Performance Comparison

Approximate encoding speeds (1080p video, medium quality):

| Hardware | H.264 | HEVC | AV1 |
|----------|-------|------|-----|
| NVIDIA RTX 4090 | ~500 fps | ~400 fps | ~300 fps |
| NVIDIA RTX 3080 | ~450 fps | ~350 fps | N/A |
| Intel Arc A770 | ~250 fps | ~200 fps | ~150 fps |
| AMD RX 7900 XT | ~200 fps | ~150 fps | ~100 fps |
| CPU (Ryzen 9 5900X) | ~50 fps | ~20 fps | ~5 fps |

*Note: Actual performance varies based on video resolution, complexity, and preset settings.*

## Quality Comparison

Hardware encoders typically produce slightly lower quality than software encoders at the same bitrate, but the difference is minimal for most use cases:

- **NVIDIA NVENC**: Excellent quality, especially with HQ tune
- **Intel QSV**: Good quality, comparable to NVENC
- **AMD AMF/VAAPI**: Good quality, slightly behind NVENC/QSV
- **CPU (libx264/libx265)**: Best quality, but much slower

For maximum quality at the expense of speed, use CPU encoding with slow presets.

## Troubleshooting

### NVIDIA Issues

**Problem**: "Hardware: CPU acceleration detected" but NVIDIA GPU is present

**Solutions**:
1. Check NVIDIA drivers: `nvidia-smi` should show your GPU
2. Verify Container Toolkit: `docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi`
3. Restart Docker daemon: `sudo systemctl restart docker`
4. Check docker-compose GPU configuration

### Intel QSV Issues

**Problem**: QSV not detected on Intel system

**Solutions**:
1. Verify QSV support: `docker exec 8mblocal-worker ffmpeg -hide_banner -hwaccels | grep qsv`
2. Check `/dev/dri` permissions: `ls -la /dev/dri`
3. Install Intel media drivers: `sudo apt-get install intel-media-va-driver-non-free`
4. Ensure device is mounted in container

### AMD Issues

**Problem**: AMD GPU not being used

**Solutions**:
1. **Linux**: Check VAAPI: `docker exec 8mblocal-worker ffmpeg -hide_banner -hwaccels | grep vaapi`
2. **Linux**: Install Mesa drivers: `sudo apt-get install mesa-va-drivers`
3. **Windows**: Update AMD drivers to latest version
4. Check `/dev/dri` mount (Linux) or GPU access (Windows)

### Performance Issues

**Problem**: Encoding is slower than expected

**Solutions**:
1. Check which encoder is being used in logs
2. Verify GPU is actually being used: `nvidia-smi` / `intel_gpu_top` / `radeontop`
3. Try faster preset (P1-P4)
4. Reduce resolution if possible
5. Check for CPU/GPU thermal throttling

## FFmpeg Build

The unified Dockerfile builds FFmpeg 7.0 with support for all hardware acceleration methods:

```bash
./configure \
  --enable-nonfree --enable-gpl \
  --enable-cuda-nvcc --enable-libnpp --enable-nvenc \
  --enable-libmfx --enable-vaapi \
  --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus
```

This ensures maximum compatibility across different hardware configurations.

## Future Enhancements

Potential improvements for hardware acceleration support:

- [x] Apple VideoToolbox support (macOS) - Added in v1.x
- [ ] Vulkan video encoding (cross-platform)
- [ ] Raspberry Pi V4L2 M2M support
- [ ] Dynamic hardware switching based on load
- [ ] Per-codec quality/speed profiles
