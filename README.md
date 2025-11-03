# 8mb.local

8mb.local is a self-hosted, GPU-accelerated alternative to 8mb.video. Drop a video, choose the destination size, and let NVIDIA NVENC shrink the file with modern codecs such as AV1, HEVC, or H.264. The stack is designed for fire-and-forget operation with real-time status updates.

## Features

- **FastAPI backend** with async uploads, ffprobe analysis, and a Celery job queue.
- **Celery + Redis** worker pipeline that streams FFmpeg progress and logs in real time.
- **Next.js + Tailwind** single-page interface with drag-and-drop uploads, Discord-friendly presets, and an advanced options drawer.
- **NVIDIA NVENC**-enabled FFmpeg build (AV1/HEVC/H.264) packaged in a dedicated worker container.
- **Automatic cleanup** of stale uploads/outputs and optional HTTP basic authentication for simple protection.

## Getting Started

### Prerequisites

- Docker and Docker Compose v2
- An NVIDIA GPU with recent drivers and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

### Environment

Basic authentication can be enabled by exporting credentials before running Compose:

```bash
export EIGHTMBLOCAL_BASIC_AUTH_USERNAME=admin
export EIGHTMBLOCAL_BASIC_AUTH_PASSWORD=changeme
```

Use the **Access** panel on the homepage to store credentials in the browser so API calls include the proper Authorization header.

The frontend reads `NEXT_PUBLIC_API_BASE_URL` from Compose and supports the same origin setup out-of-the-box.

### Launch the stack

```bash
docker compose up --build
```

Services:

- `frontend` – Next.js UI served at http://localhost:3000
- `backend-api` – FastAPI server with WebSocket progress at http://localhost:8000
- `worker` – Celery worker with GPU-enabled FFmpeg
- `redis-broker` – Redis instance used as broker/result backend and for pub/sub updates

Uploads are stored under `./uploads` and results under `./outputs`. Both directories are purged on a schedule (default 1 hour TTL).

## Development Notes

- FFmpeg is compiled in the worker image with `--enable-nonfree`, `--enable-libnpp`, `--enable-nvenc`, `--enable-cuda-nvcc`, and `--enable-libopus` to unlock NVENC encoders.
- The backend exposes `/ws/jobs/<id>` for live updates and `/outputs/*` for static downloads.
- Advanced codec presets map directly to NVENC speed-quality settings (`p1`, `p5`, `p7`).
- Adjust cleanup cadence via `EIGHTMBLOCAL_CLEANUP_INTERVAL_SECONDS` and retention with `EIGHTMBLOCAL_FILE_TTL_SECONDS`.

## Roadmap

- Multi-user sessions and shared history
- OAuth and API token auth options
- Automated integration tests for worker/encoder flows

Enjoy high-quality compression without upload limits.
