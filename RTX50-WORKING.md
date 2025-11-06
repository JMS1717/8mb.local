# RTX 50-Series (Blackwell) Support - Working Version

## ✅ Verified Working Configuration

**Branch:** `rtx50-e711a8c-working`  
**Commit:** `e711a8c` (Nov 6, 2025 @ 2:20am)  
**Docker Image:** `jms1717/8mblocal:rtx50-working`  
**Tested On:** RTX 5070 Ti, Driver 581.80, Windows 11 WSL2

### Test Results
All 6 encoders passing:
- ✅ h264_nvenc (NVIDIA H.264 hardware encoder)
- ✅ hevc_nvenc (NVIDIA H.265/HEVC hardware encoder)
- ✅ av1_nvenc (NVIDIA AV1 hardware encoder)
- ✅ libx264 (CPU H.264 encoder)
- ✅ libx265 (CPU H.265/HEVC encoder)
- ✅ libaom-av1 (CPU AV1 encoder)

## Requirements

### System Requirements
- **GPU:** NVIDIA RTX 50-series (Blackwell architecture, SM_100)
- **Driver:** NVIDIA 550.x or newer (tested with 581.80)
- **CUDA:** 13.0.1+
- **OS:** Windows 11 WSL2 or Linux with CUDA 13 support

### Software Requirements
- Docker Desktop (Windows) or Docker with nvidia-container-toolkit (Linux)
- NVIDIA Container Runtime

## Installation

### Option 1: Docker Hub (Recommended)

Pull the pre-built image:

```bash
docker pull jms1717/8mblocal:rtx50-working
```

### Option 2: Build from Source

Clone and build from the working branch:

```bash
git clone https://github.com/JMS1717/8mb.local.git
cd 8mb.local
git checkout rtx50-e711a8c-working

docker build \
  --build-arg BUILD_VERSION=rtx50-working \
  --build-arg BUILD_FLAVOR=latest \
  --build-arg CUDA_VERSION=13.0.1 \
  --build-arg UBUNTU_FLAVOR=ubuntu22.04 \
  --build-arg FFMPEG_VERSION=8.0 \
  --build-arg NV_CODEC_HEADERS_REF=sdk/12.2 \
  --build-arg NVCC_ARCHS="100 90 89 86 80 75" \
  --build-arg DRIVER_MIN=550.00 \
  --build-arg ENABLE_LIBNPP=true \
  -t 8mblocal:rtx50-working .
```

## Usage

### ⚠️ CRITICAL: WSL Driver Mount Required

For RTX 50-series NVENC to work, you **MUST** mount the WSL driver directory. Without this mount, NVENC will fail with error code 187.

### Using Docker Compose (Recommended)

The included `docker-compose.yml` already has the correct configuration:

```bash
docker-compose up -d
```

### Using Docker Run

If using `docker run`, include the WSL driver mount:

```bash
docker run -d \
  --name 8mblocal \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,video,utility \
  -p 8001:8001 \
  -v ./uploads:/app/uploads \
  -v ./outputs:/app/outputs \
  -v /usr/lib/wsl/drivers:/usr/lib/wsl/drivers:ro \
  jms1717/8mblocal:rtx50-working
```

**Note the critical volume mount:** `-v /usr/lib/wsl/drivers:/usr/lib/wsl/drivers:ro`

## Verification

Check that all encoders are working:

```bash
docker logs 8mblocal 2>&1 | grep "TEST SUMMARY" -A 10
```

You should see:
```
TEST SUMMARY
──────────────────────────────────────────────────────────────────────
Total Encoders Tested: 6
✓ Passed:  6
✗ Failed:  0
```

## Technical Details

### What Makes This Version Work

1. **CUDA 13.0.1 Support:** Full Blackwell (SM_100) architecture support
2. **FFmpeg 8.0:** Built with nv-codec-headers sdk/12.2 for RTX 50 NVENC
3. **WSL2 Driver Access:** Mounts `/usr/lib/wsl/drivers` to access DXG libraries
4. **LD_PRELOAD Override:** Uses real WSL driver instead of stub `libcuda.so.1`
5. **Proper NVENC Detection:** Worker correctly detects and enables NVENC encoders

### Build Configuration

```dockerfile
CUDA_VERSION=13.0.1
FFMPEG_VERSION=8.0
NV_CODEC_HEADERS_REF=sdk/12.2
NVCC_ARCHS="100 90 89 86 80 75"
DRIVER_MIN=550.00
```

### Key Files

- **entrypoint.sh:** WSL2 driver detection and LD_PRELOAD setup
- **worker/app/hw_detect.py:** NVIDIA hardware detection
- **worker/app/startup_tests.py:** Encoder validation tests
- **docker-compose.yml:** Pre-configured with WSL driver mount

## Troubleshooting

### NVENC Fails with "Decode error (code 187)"

**Cause:** Missing WSL driver directory mount

**Solution:** Ensure you're mounting `/usr/lib/wsl/drivers:/usr/lib/wsl/drivers:ro`

### "Could not open encoder"

**Cause:** Either missing WSL driver mount or incompatible driver version

**Solution:**
1. Check driver version: `nvidia-smi` (needs 550.x+)
2. Verify WSL driver mount is present in container:
   ```bash
   docker exec 8mblocal ls -la /usr/lib/wsl/drivers
   ```

### Container Exits Immediately

**Cause:** Possible entrypoint failure

**Solution:** Check logs:
```bash
docker logs 8mblocal
```

## Version History

- **e711a8c (Nov 6, 2025):** ✅ Working RTX 50-series NVENC support
  - All 6 encoders pass
  - Requires WSL driver mount
  - CUDA 13.0.1 + FFmpeg 8.0
  - Tested and verified on RTX 5070 Ti

## Related Documentation

- [RTX50_FIX.md](RTX50_FIX.md) - Detailed technical explanation of the fix
- [GPU_SUPPORT.md](docs/GPU_SUPPORT.md) - General GPU support documentation
- [docker-compose.yml](docker-compose.yml) - Reference configuration

## Support

If you encounter issues:
1. Verify all requirements are met
2. Check that WSL driver mount is present
3. Review container logs: `docker logs 8mblocal`
4. Open an issue with:
   - GPU model and driver version (`nvidia-smi`)
   - Container logs
   - Your `docker run` command or `docker-compose.yml`

## License

See [LICENSE](LICENSE) file in the repository root.
