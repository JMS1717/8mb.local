# RTX 50-Series NVENC Fix for Docker on WSL2

## Problem
RTX 50-series GPUs (e.g., RTX 5070 Ti) use **CUDA 13.0** with new **DXG (DirectX Graphics)** libraries that nvidia-container-toolkit doesn't automatically mount. This causes `cuInit(0)` to fail with error 500 (CUDA_ERROR_NOT_FOUND).

## Root Cause
1. **CUDA 13.0** (Blackwell architecture) requires additional WSL2-specific libraries:
   - `libnvdxgdmal.so.1` (NVIDIA DXG DirectML)
   - `libnvdxdlkernels.so` (DXG kernel libraries)
   - `libnvwgf2umx.so` (Windows Graphics Foundation)
   - `libnvidia-encode.so.1` (NVENC encoder)
   - `libnvcuvid.so.1` (NVDEC decoder)
   - Plus 8 other DXG-related libraries

2. **nvidia-container-toolkit** only mounts 5 libraries:
   - libcuda.so.1.1
   - libcuda_loader.so
   - libnvidia-ml.so.1
   - libnvidia-ml_loader.so
   - libnvidia-ptxjitcompiler.so.1

3. **Stub library conflict**: The nvidia/cuda base image includes a 172KB stub `libcuda.so.1` that takes precedence over the real 26MB WSL2 driver.

## Solution
Two changes were required:

### 1. Mount Full WSL Driver Directory
Add this volume mount to `docker-compose.yml`:
```yaml
volumes:
  - /usr/lib/wsl/drivers:/usr/lib/wsl/drivers:ro
```

This makes ALL WSL driver libraries available, including the missing DXG libraries.

### 2. Use LD_PRELOAD to Override Stub
In `entrypoint.sh`, force loading the real WSL driver:
```bash
export LD_PRELOAD="$WSL_DRV_DIR/libcuda.so.1.1:${LD_PRELOAD:-}"
```

This ensures the real 26MB WSL2 CUDA driver is loaded instead of the 172KB stub.

## Verification
All NVENC encoders now work:
- ✅ H.264 NVENC (h264_nvenc)
- ✅ HEVC NVENC (hevc_nvenc)
- ✅ AV1 NVENC (av1_nvenc)

Test command:
```bash
docker run --rm --gpus all -v /usr/lib/wsl/drivers:/usr/lib/wsl/drivers:ro \\
  8mblocal:latest ffmpeg -f lavfi -i nullsrc=s=1920x1080:d=1 \\
  -c:v h264_nvenc -f null -
```

## Technical Details
- **Hardware**: RTX 5070 Ti Laptop GPU (Blackwell architecture)
- **Driver**: 581.80
- **CUDA Version**: 13.0 (Host), 13.0.1 (Container for latest builds)
- **OS**: Windows 11 with WSL2
- **Container Base**: nvidia/cuda:13.0.1-runtime-ubuntu22.04 (latest), nvidia/cuda:12.2.0-runtime-ubuntu22.04 (legacy)
- **FFmpeg**: 8.0 with NVENC support

## Files Modified
1. `docker-compose.yml` - Added WSL drivers volume mount
2. `entrypoint.sh` - Added LD_PRELOAD logic to override stub
3. `Dockerfile` - Removed unnecessary cuda-fix.sh wrapper

## Why This Works
WSL2's CUDA 13.0 implementation uses Microsoft's DXG (DirectX Graphics) subsystem instead of traditional CUDA device nodes (`/dev/nvidia*`). The DXG libraries dynamically load at runtime when `cuInit()` is called. Without these libraries mounted, cuInit fails with error 500, even though the base libcuda.so.1.1 is present.

By mounting the full `/usr/lib/wsl/drivers` directory and using LD_PRELOAD to force loading the real driver, we provide everything CUDA 13.0 needs to initialize successfully on RTX 50-series GPUs.

## November 6, 2025
Fixed after extensive debugging that identified:
1. nvidia-container-toolkit incomplete library mounting
2. Stub libcuda.so.1 blocking real driver
3. Missing DXG libraries causing cuInit failures

The fix enables **full NVENC/NVDEC support** for RTX 50-series GPUs in Docker containers on Windows WSL2.

---

## Building Locally for RTX 50-Series (Optional)

The Docker Hub **latest** image now includes sm_100 (Blackwell) architecture support using CUDA 13.0.1:

```bash
# The latest build from Docker Hub already has RTX 50-series support!
docker pull jms1717/8mblocal:latest
```

If you want to build locally:

```bash
# Build with full RTX 50-series support (CUDA 13.0.1)
docker build -t 8mblocal:rtx50 \
  --build-arg CUDA_VERSION="13.0.1" \
  --build-arg NVCC_ARCHS="100 90 89 86" \
  --build-arg FFMPEG_VERSION="8.0" \
  .
```

**Key Points**:
- ✅ CUDA 13.0.1 supports sm_100 (Blackwell/RTX 50-series) compilation
- ✅ Latest Docker Hub image includes sm_100 support by default
- ✅ WSL2 driver mount + LD_PRELOAD makes NVENC work at runtime
- ✅ Works with Driver 550+ (581.80 tested and working)
