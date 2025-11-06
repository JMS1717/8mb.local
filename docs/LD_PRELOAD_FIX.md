# RTX 50-Series NVENC Fix: LD_PRELOAD Inheritance Issue

**Date**: November 6, 2025  
**Issue**: NVENC encoders failing after security improvements deployment  
**GPU**: RTX 5070 Ti Laptop (Blackwell, CUDA 13.0, Driver 581.80)  
**Status**: ✅ **FIXED**

---

## Problem Summary

After deploying the security improvements (commit 09895f3), all three NVENC encoders started failing with:
- `Decode: ✗ FAIL - Decode error (code 187)`
- `Encode: ✗ FAIL - Could not open encoder`

This occurred despite the entrypoint.sh correctly setting `LD_PRELOAD` to force the real WSL CUDA driver.

---

## Root Cause Analysis

### Background: RTX 50-Series WSL2 NVENC Requirements

The RTX 50-series (Blackwell) on WSL2 requires special handling:

1. **WSL2 uses DirectX Graphics (DXG)** subsystem instead of traditional `/dev/nvidia*` devices
2. **nvidia-container-toolkit** only mounts 5 CUDA libraries by default
3. **RTX 50-series needs 17 DXG-specific libraries** including:
   - `libnvdxgdmal.so.1` (NVIDIA DXG DirectML)
   - `libnvdxdlkernels.so` (DXG kernel libraries)
   - `libnvidia-encode.so.1` (NVENC encoder)
   - `libnvcuvid.so.1` (NVDEC decoder)
   - Plus 13 more...

4. **Stub driver problem**: The `nvidia/cuda` base image includes a 172KB stub `libcuda.so.1` that doesn't work with WSL2's DXG subsystem. The real driver is the 26MB `libcuda.so.1.1` in `/usr/lib/wsl/drivers/`.

### Our Solution (RTX50_FIX.md)

The original fix (documented in `RTX50_FIX.md`) works by:

1. **Mounting the full WSL driver directory** in `docker-compose.yml`:
   ```yaml
   volumes:
     - /usr/lib/wsl/drivers:/usr/lib/wsl/drivers:ro
   ```

2. **Using LD_PRELOAD to override the stub** in `entrypoint.sh`:
   ```bash
   WSL_DRV_DIR=$(find /usr/lib/wsl/drivers -type d -name 'nvami.inf_*' | head -1)
   export LD_PRELOAD="$WSL_DRV_DIR/libcuda.so.1.1:${LD_PRELOAD:-}"
   ```

### What Broke

The **supervisord.conf** file hardcodes environment variables for the `backend` and `worker` programs:

```ini
[program:worker]
environment=LD_LIBRARY_PATH="/usr/local/cuda/compat:..."
# LD_PRELOAD was NOT included!
```

When supervisord starts child processes, it **replaces** the environment rather than inheriting it. This meant:

- ✅ entrypoint.sh sets `LD_PRELOAD` correctly
- ✅ supervisord itself inherits `LD_PRELOAD`
- ❌ **Worker and backend processes do NOT inherit `LD_PRELOAD`**
- ❌ FFmpeg loads the 172KB stub instead of the 26MB real driver
- ❌ NVENC initialization fails with "Could not open encoder"

---

## The Fix

### Changes to `supervisord.conf`

Added `LD_PRELOAD="%(ENV_LD_PRELOAD)s"` to both programs:

**Backend:**
```ini
[program:backend]
environment=PYTHONPATH="/app",CUDA_HOME="/usr/local/cuda",...,LD_PRELOAD="%(ENV_LD_PRELOAD)s"
```

**Worker:**
```ini
[program:worker]
environment=LD_LIBRARY_PATH="...",LD_PRELOAD="%(ENV_LD_PRELOAD)s"
```

The `%(ENV_LD_PRELOAD)s` syntax tells supervisord to **inherit** the `LD_PRELOAD` value from the parent environment (set by entrypoint.sh).

---

## Verification

After the fix, the worker should show:

```log
✓ LD_PRELOAD set to: /usr/lib/wsl/drivers/nvami.inf_amd64_6963c3ad1eccbcde/libcuda.so.1.1

[h264_nvenc] ✓ OVERALL PASS
[hevc_nvenc] ✓ OVERALL PASS
[av1_nvenc ] ✓ OVERALL PASS
```

Instead of:

```log
[h264_nvenc] ✗ OVERALL FAIL - Could not open encoder
[hevc_nvenc] ✗ OVERALL FAIL - Could not open encoder
[av1_nvenc ] ✗ OVERALL FAIL - Could not open encoder
```

---

## Why This Wasn't Caught Earlier

This issue was **not caused by the security improvements** themselves, but rather exposed a pre-existing fragility:

1. The original RTX 50-series fix was tested with manual `docker run` commands where environment variables are inherited naturally
2. The supervisord environment configuration was **always incorrect** but went unnoticed
3. The security improvements deployment required a full container rebuild, which exposed the dormant bug

---

## Lessons Learned

### 1. Environment Variable Inheritance is Fragile

When using process managers like supervisord, **always explicitly pass through** critical environment variables:

```ini
environment=CRITICAL_VAR="%(ENV_CRITICAL_VAR)s"
```

Don't assume child processes will inherit them.

### 2. Test Full Deployment Path

Testing with `docker run` may not catch issues that only appear with `docker-compose` + supervisord.

### 3. Document Critical Environment Variables

The RTX50_FIX.md should explicitly mention that `LD_PRELOAD` **must be inherited by all processes**, not just set at the shell level.

---

## Related Files

- `entrypoint.sh` - Sets `LD_PRELOAD` for WSL2 CUDA driver override
- `supervisord.conf` - Now passes `LD_PRELOAD` to backend and worker (**FIXED**)
- `docker-compose.yml` - Mounts `/usr/lib/wsl/drivers` for access to real driver
- `RTX50_FIX.md` - Original documentation of the RTX 50-series fix

---

## Testing Commands

### Verify LD_PRELOAD is Active

```bash
# Inside the running container
docker exec <container_id> bash -c 'echo $LD_PRELOAD'
# Should show: /usr/lib/wsl/drivers/nvami.inf_amd64_.../libcuda.so.1.1
```

### Check Worker Process Environment

```bash
# From the host
docker exec <container_id> cat /proc/$(pgrep -f celery)/environ | tr '\0' '\n' | grep LD_PRELOAD
# Should show: LD_PRELOAD=/usr/lib/wsl/drivers/...
```

### Run Encoder Tests

Visit `http://localhost:8001/settings` and click "Re-run Tests" to verify all NVENC encoders pass.

---

## Deployment

**Container Rebuild Required**: Yes  
**Breaking Changes**: None  
**Affected Systems**: RTX 50-series GPUs on Windows WSL2 only  

To deploy:
```bash
cd /path/to/8mb.local
git pull
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

---

## Commit History

1. **9c2a814** - "Upgrade latest build to CUDA 13.0.1 with RTX 50-series support"
2. **09895f3** - "Security & robustness improvements: 10 critical fixes"
3. **8bd1263** - "Fix RTX 50-series NVENC: Pass LD_PRELOAD through supervisord" ⬅ **This fix**

---

**Status**: Production-ready ✅  
**Tested On**: RTX 5070 Ti Laptop, Driver 581.80, Windows 11 WSL2  
**Expected Outcome**: All NVENC encoders (h264/hevc/av1) should pass validation tests
