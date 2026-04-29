# Bugfix Summary — PR #36 Follow-up Fixes

> Branch: `feature/jellyfin-ffmpeg-71`  
> Base: `JMS1717/8mb.local:main`  
> Tested on: Intel UHD 630 (iGPU) via VAAPI/QSV, Jellyfin FFmpeg 7.1

---

## Critical Bug Fixes

### 1. `libsvtav1` crashes with string presets (e.g. `"fast"`)

**File:** `worker/app/tasks.py`  
**Error:** FFmpeg exit code 1 — SVT-AV1 encoder rejected non-integer preset strings like `"fast"` or `"medium"`, which are valid for `libx264`/`libx265` but illegal for `libsvtav1`.

**Fix:** Split `libsvtav1` into its own `elif` branch. Presets are now mapped through `SVTAV1_PRESET_MAP` (already defined in `constants.py`) to their integer equivalents before being passed to FFmpeg.

```python
# Before (crash):
elif actual_encoder in ("libx264", "libx265", "libsvtav1"):
    preset_flags = ["-preset", preset_val]  # "fast" → crash for libsvtav1

# After:
elif actual_encoder == "libsvtav1":
    preset_int = SVTAV1_PRESET_MAP.get(preset_val, 6)
    preset_flags = ["-preset", str(preset_int)]
```

---

### 2. `libsvtav1` crashes with `-maxrate`/`-bufsize` (VBR mode)

**File:** `worker/app/tasks.py`  
**Error:** FFmpeg exit code 234 — `"Svt[error]: Max Bitrate only supported with CRF mode"`. SVT-AV1 does not allow `-maxrate`/`-bufsize` in bitrate-target mode.

**Fix:** `libsvtav1` gets only `-b:v` (no `-maxrate`/`-bufsize`), both in the primary command and in the CPU `cmd2` fallback.

---

### 3. VAAPI/QSV filter chain crash — `format=nv12|vaapi,hwupload` missing

**File:** `worker/app/tasks.py`  
**Error:** FFmpeg exit code 218 — `"Impossible to convert between the formats supported by the filter"`. The `vf_filters` list in `tasks.py` was built with only `scale=` entries; the pixel format conversion and `hwupload` required by VAAPI/QSV were never appended.

**Note:** The correct logic existed in `encoder.py`'s `_build_video_filters()` but that function was never called from `tasks.py`.

**Fix:** After any scale filter, append the appropriate hwupload for the active encoder:

```python
if actual_encoder in VAAPI_ENCODERS:
    vf_filters.append("format=nv12|vaapi,hwupload")
elif actual_encoder in QSV_ENCODERS:
    vf_filters.append("format=nv12,hwupload=extra_hw_frames=64")
```

Also: `VAAPI_ENCODERS`, `QSV_ENCODERS`, and `HW_ENCODERS` are now imported from `constants.py`.

---

### 4. Runtime hardware encoder failure only retried for NVENC

**File:** `worker/app/tasks.py`  
**Problem:** The runtime CPU fallback (`cmd2`) was guarded by `actual_encoder.endswith("_nvenc")`. VAAPI and QSV encoders that fail at runtime (e.g. unsupported profile, missing driver) had no fallback — the job simply failed with an error.

**Fix:**
```python
# Before:
if rc != 0 and actual_encoder.endswith("_nvenc"):

# After:
if rc != 0 and actual_encoder in HW_ENCODERS:
```

All hardware encoders (NVENC, VAAPI, QSV) now fall back to a CPU encoder on runtime failure. The CPU fallback `cmd2` also strips `format=nv12|vaapi,hwupload` and `hwupload` from `vf_filters` so no VAAPI-specific filters leak into the CPU pipeline.

---

### 5. AV1 CPU fallback used `libaom-av1` (not available in Jellyfin FFmpeg)

**File:** `worker/app/tasks.py`  
**Error:** FFmpeg exit code 8 — `"Unknown encoder 'libaom-av1'"`. Jellyfin FFmpeg does not ship `libaom-av1`. Two hardcoded fallback paths used it:
- Pre-flight fallback (startup test cache hit)
- Runtime fallback (cmd2 AV1 branch)

**Fix:** Both paths now use `libsvtav1` (available in Jellyfin FFmpeg) for AV1 CPU fallback. The HW_ENCODERS membership check (`actual_encoder in HW_ENCODERS`) replaces the fragile `not in ("libx264", "libx265", "libaom-av1")` guard.

---

### 6. `CPU_FALLBACK` mapping was correct but never applied for pre-flight fallback

**File:** `worker/app/tasks.py`  
**Problem:** `_cpu_fallback_for()` correctly reads `CPU_FALLBACK` from `constants.py` (`AV1_VAAPI → libsvtav1`), but the pre-flight fallback block in `compress_video` used its own inline `if/elif/else` chain with hardcoded encoder names instead of calling it.

**Fix:** Inline chain updated to use `libsvtav1` for AV1 (matching `CPU_FALLBACK`). A future refactor could unify both paths through `_cpu_fallback_for()`.

---

## Reliability / Stability Fixes

### 7. SVT-AV1 `lp=6` can crash VMs (parallelism limit)

**File:** `worker/app/tasks.py`  
**Problem:** Hardcoded `SVTAV1_PARAMS_MAX_LP = ["-svtav1-params", "lp=6"]` maximises SVT-AV1 parallelism. On 4K input this saturates all CPU cores and can OOM-kill or freeze VMs.

**Fix:** Default changed to `lp=0` (library auto-selects a safe level). Configurable via env var:

```yaml
# docker-compose.yml
- SVTAV1_LP=0   # 0=auto (safe), 1–6=explicit level
```

### 8. CPU encoder thread limits

**File:** `worker/app/tasks.py`  
**Problem:** `libx264`, `libx265` use all available cores by default — same VM-crash risk.

**Fix:** New env var `CPU_ENCODER_THREADS` (default `0` = FFmpeg auto):

```yaml
- CPU_ENCODER_THREADS=2   # limit to 2 threads; 0 = no limit
```

Applied to `libx264`, `libx265` in both primary command and `cmd2` fallback.

---

## Startup Tests / Hardware Detection Fixes

### 9. `tested.get(encoder, False)` false-negative on empty cache

**File:** `worker/app/hw_detect.py`  
**Problem:** `tested.get(encoder, False)` returns `False` when the encoder is not in the cache (not yet tested), incorrectly treating it as "failed" and preventing hardware from being used on startup.

**Fix:**
```python
# Before (wrong): marks untested encoders as failed
if tested.get(encoder, False):

# After (correct): only skip if explicitly tested and marked False
if encoder in tested and not tested[encoder]:
```

### 10. Stale in-memory `tested_encoders` not cleared on rerun

**File:** `worker/app/startup_tests.py`  
**Problem:** After flushing the Redis encoder test cache (`_flush_encoder_test_cache()`), the in-memory `hw_info` dict still held the old `tested_encoders` dict. The rerun used stale data.

**Fix:** Added `hw_info.pop("tested_encoders", None)` directly after the flush.

---

## Infrastructure Fix

### 11. Silent `dpkg` repair failure

**File:** `backend-api/app/routers/system.py`  
**Problem:** If `apt-get install -f` (dpkg repair) failed, the error was silently ignored and the Jellyfin FFmpeg installation continued as if the system were healthy.

**Fix:** Check `repair.returncode` and raise `HTTPException(500)` if the repair step fails.

---

## UI Improvements

**File:** `frontend/src/routes/+page.svelte`

| Change | Detail |
|--------|--------|
| **Codec group colors** | NVIDIA=green `#22c55e`, QSV=blue `#60a5fa`, VAAPI=amber `#fbbf24`, CPU=brown `#92400e` |
| **Codec group icons** | 🟢 NVIDIA, 🔵 QSV, 🟡 VAAPI, ⚪ CPU — shown in dropdown options |
| **Failed codec warning** | Red box directly under the codec dropdown when the selected codec failed startup tests |
| **Removed duplicate warning** | The `"hat den Encoder-Test nicht bestanden"` message in the pipeline log area was removed — it was already shown under the codec dropdown |
| **"Hardware encoder unavailable" false positive** | Warning now only shown when `encodeMethod.startsWith('lib') && hardwareType !== 'cpu' && selectedCodec.group !== 'cpu'` — previously triggered incorrectly for CPU-group codecs |

---

## Files Changed

| File | Changes |
|------|---------|
| `worker/app/tasks.py` | All encoding fixes, fallback logic, env vars, hwupload filter |
| `worker/app/hw_detect.py` | Fix 9: `tested.get()` false-negative |
| `worker/app/startup_tests.py` | Fix 10: stale `tested_encoders` on rerun |
| `backend-api/app/routers/system.py` | Fix 11: dpkg repair error handling |
| `frontend/src/routes/+page.svelte` | Codec colors, icons, duplicate warning removal |
