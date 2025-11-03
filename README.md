# 8mb.local – Self-Hosted GPU Video Compressor

8mb.local is a self-hosted, "fire-and-forget" web app to compress videos to a target size using NVIDIA NVENC with modern codecs (AV1/HEVC/H.264). It features a minimal SPA UI, async FastAPI backend, Celery worker, Redis broker, and real-time progress via SSE.

## Features
- Drop a file, pick a target size (8MB, 25MB, 50MB, 100MB, custom)
- Codecs: AV1 (av1_nvenc, default), HEVC (hevc_nvenc), H.264 (h264_nvenc)
- Audio: Opus (libopus, default), AAC
- Speed/Quality presets mapped to NVENC `-preset` (P1 fast … P7 best)
- ffprobe on upload for instant estimates and quality warnings
- Real-time progress + live FFmpeg log (SSE)
- GPU-accelerated FFmpeg 6.x compiled with NVENC/NPP/Opus
- Auto-cleanup of files after retention window
- Optional Basic Auth (env-controlled)

## Requirements
- Docker and Docker Compose
- NVIDIA GPU with NVIDIA drivers
- NVIDIA Container Toolkit installed and configured

## Installation
You can run 8mb.local using prebuilt Docker images (recommended) or build locally.

### Quick Start (Docker Hub images)
### Pull-and-run (Docker Hub images)
If you just want to run the stack without building images locally:

```bash
docker compose -f docker-compose.hub.yml up -d
```

Frontend: http://localhost:5173  •  Backend: http://localhost:8000

Requires NVIDIA GPU runtime for the worker (Compose uses `gpus: all`).

### Build locally
1. Copy `.env.example` to `.env` and adjust values.
2. Create folders: `uploads/`, `outputs/` (already present).
3. Start services:
   - The worker image compiles FFmpeg (first build can take a while).

```bash
# Optional: Windows PowerShell
# Ensure Docker Desktop is running and NVIDIA toolkit is installed
# Run from repo root
docker compose up --build
```

Visit:
- Frontend: http://localhost:5173
- Backend: http://localhost:8000/docs (requires AUTH if enabled)

## Environment Variables
- AUTH_ENABLED=true|false
- AUTH_USER, AUTH_PASS
- FILE_RETENTION_HOURS=1
- REDIS_URL=redis://redis-broker:6379/0
- BACKEND_URL=http://localhost:8000 (frontend uses this)

## How it Works
- Upload triggers ffprobe to extract duration and bitrates.
- Target total bitrate (kbps) = (target_MB * 8192) / duration_seconds
- Target video bitrate (kbps) = total_kbps - audio_kbps (e.g., 128)
- Worker executes FFmpeg with NVENC and streams progress to Redis Pub/Sub; backend relays via SSE.

## Security
Enable Basic Auth by setting `AUTH_ENABLED=true` and specifying `AUTH_USER`/`AUTH_PASS`.

## Notes
- AV1 (av1_nvenc) requires recent RTX GPUs (30/40 series) and recent drivers.
- First worker build compiles FFmpeg; subsequent builds are faster.
- For production, consider HTTPS termination and hardened auth.
