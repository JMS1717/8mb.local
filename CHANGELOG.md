# Changelog

## [v137] - 2026-04-20

### UI

- **Encoder preset vs NVENC tuning:** Main, batch, and settings copy clarifies P1–P7 (effort toward target size/bitrate) vs **NVENC tuning** (quality vs latency); tune control is explained only for NVIDIA codecs.
- **Extra Quality:** Dropdown and contextual notes describe **constant quality (CRF/CQ)** behavior, that **output size is not guaranteed** vs target MB, and that **fixed video bitrate** mode falls back to **P6** (CQ conflicts with fixed kbps).

### Worker

- Fixed **`compress_video`** nested **`sys`** use (removed inner `import sys` that shadowed the module and caused `NameError` in nested FFmpeg helpers).

### SVT-AV1 encoder (fast CPU AV1)

- FFmpeg is now built with **`--enable-libsvtav1`** (via `libsvtav1-dev`, `libsvtav1enc-dev`) and ships **`libsvtav1enc1`** in the runtime image.
- Backend/worker stop aliasing `libsvtav1` to `libaom-av1`. `libsvtav1` is a first-class encoder and is the **preferred CPU AV1 fallback** (10×–50× faster than libaom at comparable quality for strict target-size ABR).
- `worker.tasks` has preset mappings for SVT-AV1 (numeric `-preset 0..13`) and for libaom-av1 (`-cpu-used 0..8`); no more "silent slow fallback" to libaom when the user picks SVT.
- Settings page exposes a separate **AV1 (SVT-AV1, fast)** visibility toggle alongside the existing **AV1 (libaom, slow)** toggle. New default preset profile: *AV1 8MB (SVT-AV1, CPU)*.

### Backend stability / responsiveness

- **Cleanup scheduler:** APScheduler now runs `cleanup_files` as a true coroutine; blocking `os.listdir`/`os.stat`/`os.remove` are offloaded to a thread via `asyncio.to_thread`. Duplicate `start_scheduler()` invocation (two schedulers firing every 15 min) fixed; double `import time` removed.
- **hw-info caching:** 60-second TTL + single-flight async lock. New `get_hw_info_cached_async()` / `get_hw_info_fresh_async()` wrap the blocking Celery RPC in `asyncio.to_thread`; `/api/hardware`, `/api/codecs/available`, `/api/system/capabilities`, `/api/system/encoder-tests`, `/api/diagnostics/gpu`, and the rerun endpoint no longer stall the event loop on the worker round-trip. Cache is invalidated after hardware-test reruns so operators see fresh results.

### `.env` footgun fix

- `docker-compose.yml` / `docker-compose.cpu.yml` now use the long-form bind with **`create_host_path: false`** so Docker will no longer silently create a `.env` *directory* when the file is missing on the host.
- `entrypoint.sh` detects an empty `/app/.env` directory (from prior runs) and recovers by replacing it with an empty file.
- New **`.env.example`** template committed at the repo root; copy to `.env` before first `docker compose up`.

### API routing

- SPA catch-all no longer masks 404s. Any unmatched path starting with `/api/`, `/healthz`, `/download/`, `/progress/`, `/stream/`, `/outputs/`, `/uploads/` now returns a real `404 Not Found` instead of a `200 OK` with the SPA's `index.html`.

### Logging

- New request-logging middleware emits per-request rid, method, path, status, and duration; unhandled exceptions log with full traceback and the request id is echoed back as `X-Request-ID`.
- Worker (Celery) hooks `task_prerun` / `task_postrun` / `task_failure` to log task boundaries; `compress_video` logs the full FFmpeg command, encoder choice, fallback decisions, and final size.
- `LOG_LEVEL`, `LOG_LEVEL_APP` (for `app.*` / `worker.*`), and `LOG_LEVEL_UVICORN` environment variables for fine-grained verbosity control.

### Docker

- **Docker Hub:** **`jms1717/8mblocal:latest`** and **`jms1717/8mblocal:v137`** (`BUILD_VERSION` **137**). Pull: `docker pull jms1717/8mblocal:v137` (or `:latest`).

---

## [v136] - 2026-04-17

### Portrait / display orientation

- **ffprobe** now uses **`-show_format` + `-show_streams`** (full stream JSON) so **Display Matrix** rotation is present for phone **HEVC/MP4** files where a narrow `-show_entries` query omitted it.
- **Rotation parsing** treats **`rotation` on any `side_data_list` entry** as authoritative (not only when `side_data_type` contains “display matrix”).
- When rotation is non-zero, the worker **avoids CUDA/NVDEC decode** for H.264/HEVC/AV1 paths so **decoder autorotation** runs; output dimensions match **display** size (e.g. **1080×1920**) instead of coded **1920×1080** for portrait clips stored with a −90° matrix.

### Target video bitrate

- Optional **`target_video_bitrate_kbps`**: compress to a fixed video bitrate (API, batch upload, and UI) instead of deriving rate only from target file size.

### Max frame rate (UI)

- **Max frame rate** cap control moved to **Advanced Options** on the main encode page (no longer beside Resolution).
- Cap choices extended through **120 fps** (24, 25, 30, 50, 60, 72, 90, 100, 120), with the same list on **Batch**, **Settings** (preset defaults), and shared **`$lib/fpsCap`** parsing for `localStorage`.

### Docker

- Default **`docker-compose.yml`** requests **`gpus: all`** (NVIDIA). Hosts without GPU passthrough can use **`docker-compose.cpu.yml`** (`docker compose -f docker-compose.cpu.yml up -d --build`).
- **Docker Hub:** **`jms1717/8mblocal:latest`** and **`jms1717/8mblocal:v136`** published for this release (`BUILD_VERSION` **136**). Pull: `docker pull jms1717/8mblocal:v136` (or `:latest`).

---

## [v135] - 2026-04-14

### Architecture: NVIDIA + CPU only

- **Removed Intel QSV, VAAPI, and AMD AMF** from the worker, API models, UI, and Docker image. In practice those paths **did not work reliably** in this app (container/driver friction, inconsistent behavior) and were **a poor fit for strict target-size / rate-control** encoding. The product now standardizes on **NVENC when available** and **CPU encoders** otherwise.
- **Worker** split into modules (`tasks.py`, `encoder.py`, `hw_detect.py`, `progress.py`, …); non-NVIDIA hardware branches deleted.
- **API** split into FastAPI routers under `backend-api/app/routers/`; shared helpers in `deps.py`.
- **Frontend** updated to match (no Intel/AMD UI); preset/codec and settings fixes as shipped in this branch.
- **Docs:** `docs/GPU_SUPPORT.md` rewritten for the NVENC+CPU model; see also `README.md`.

## [v134] - 2025-03-17

### ✨ Major Features

#### 🎯 Batch Processing Support
- **New `/api/batch/upload` endpoint**: Upload multiple video files at once with unified compression settings
- **Batch status tracking**: Monitor progress of all files in a batch with real-time updates
- **Batch UI page**: New `/batch` route with full batch management capabilities
- **Bulk ZIP download**: Download all completed outputs as a single ZIP file
- **Chain processing**: Leverages Celery chains for coordinated multi-job execution
- **Batch state management**: Tracks batch state (queued/running/completed/completed_with_errors/failed)
- **Per-item tracking**: Individual progress, error messages, and download URLs for each file

**Models Added:**
- `BatchItemStatus`: Individual file state in a batch (job_id, task_id, progress, download URL)
- `BatchCreateResponse`: Response when batch is created, includes all item details
- `BatchStatusResponse`: Full batch status with overall progress and per-item tracking

**Backend Improvements:**
- Batch enqueue rollback on Celery chain failure (cleans up files and metadata)
- Skipped item state propagation to job metadata
- Improved job metadata storage with TTL support
- Safe filename handling for batch uploads

**Frontend Improvements:**
- Batch upload UI with drag-and-drop or multi-file selection
- Real-time progress display for each batch item
- Details panel showing codec, bitrate, duration for preview
- Batch state visualization with status indicators
- ZIP download button for completed batches

### 🔧 Reliability & Compatibility Improvements

#### Intel QSV (Quick Sync Video) Robustness
- **Device-aware render node detection**: Automatically locates `/dev/dri` render nodes for QSV initialization
- **Multi-pattern QSV probing**: Tests 3 FFmpeg invocation patterns:
  1. With `-qsv_device` flag 
  2. Without hardware initialization flags
  3. With `-init_hw_device qsv=hw`
- **Startup test variants**: QSV encoders now validated across compatible FFmpeg configurations
- **QSV compatibility retry logic**: If QSV encode fails, automatically retries without strict hardware flags before CPU fallback
- **Device tracking**: Hardware detection now tracks `qsv_device` in result dictionary

**Benefits:**
- Works with different FFmpeg builds (some require `-qsv_device`, others work without flags)
- Handles cases where strict device initialization fails but fallback works
- Silent retry prevents premature CPU fallback on transient issues
- Better error messages when QSV is truly unavailable

#### Batch State Consistency
- **Rollback on enqueue failure**: If Celery chain fails to enqueue, all uploaded files are deleted and job metadata cleaned up
- **Skipped item sync**: When a batch item fails during chain execution, state is propagated to job metadata
- **Active job tracking**: Proper removal from `jobs:active` set on completion
- **TTL-based metadata cleanup**: Job metadata automatically expires after configurable period (default: 24 hours)

### 🏗️ Code Quality

- Helper functions for file handling: `_save_upload_file()`, `_is_video_upload()`, `_build_output_name()`
- Improved filename sanitization across upload and batch endpoints  
- Video extension validation for batch uploads
- Enhanced error handling for file size limits (413 status code)
- Timeout increased to 8s for QSV startup tests (was 5s)

### 📋 Configuration

**New Environment Variables:**
- `MAX_BATCH_FILES`: Maximum files per batch (default: 200)
- `BATCH_METADATA_TTL_HOURS`: Metadata retention for batch jobs (default: 24)

**Updated Constraints:**
- `MAX_UPLOAD_SIZE_MB`: Now enforced on both individual and batch uploads (default: 51200 MB)

### ✅ Tested & Validated

- ✓ Frontend production build passes (minor accessibility warnings pre-existing)
- ✓ Python syntax validation on all modified files
- ✓ FFmpeg binary confirmed: qsv/vaapi/cuda hwaccels; h264_qsv/hevc_qsv/av1_qsv encoders
- ✓ Batch chain rollback behavior
- ✓ Job metadata state consistency
- ✓ Multi-variant QSV probe logic

### 📝 Notes

- **Not Addressed**: Security hardening of cancel/clear endpoints (deferred per request; focus on reliability & compatibility)
- **Intel QSV on Windows WSL2**: QSV detection works correctly but device access requires Linux with `/dev/dri` exposed or Windows with NVIDIA CUDA
- **Batch Processing**: Batch chain failure handling ensures no orphaned uploads or metadata
- **Backward Compatibility**: All changes are additive; existing single-file upload still fully supported

### 🐛 Known Limitations

- QSV device unavailable in containerized envs without `/dev/dri` device mapping (expected behavior)
- Integration testing on actual Intel hardware recommended for QSV confirmation
- Batch UI requires JavaScript; no server-side rendering of progress

---

## Previous Releases
See git log for v133 and earlier changelogs.
