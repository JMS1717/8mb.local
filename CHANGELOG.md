# Changelog

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
