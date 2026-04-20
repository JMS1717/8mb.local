# Core Data Flow / API Reference

8mb.local operates as a completely headless-capable backend schema, driven dynamically by FastAPI and mapped explicitly out of the box via an interactive OpenAPI (Swagger) payload. No UI is technically required to leverage the encoding cluster workflow.

## The Interactive Sandbox (`/docs`)

FastAPI automatically generates an interactive sandbox for exploring, authenticating, and structurally hitting backend routes. Assuming you are currently executing 8mb.local via Docker standard ports:
1. Navigate to **http://localhost:8001/docs**
2. Expand the `POST /api/upload` route, click **"Try it out"**, attach a video via the UI payload drop field, and execute to see exactly how responses are handled.
> **Note**: The `/docs` portal inherently respects system light/dark mode queries automatically.

---

## Core Headless Routing Implementation

### 1. Simple Single Video Upload (`/api/upload`)

Triggers a single synchronous hardware evaluation and task dispatch.

```bash
curl -X POST "http://localhost:8001/api/upload" \
  -H "Accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@your_video.mp4" \
  -F "target_mb=25" \
  -F "preset=p6" \
  -F "video_codec=libsvtav1"
```
**Response Details:**
Returns JSON identifying a `task_id` necessary to fetch progress logs.
```json
{
  "task_id": "bfd91d1e-8e8f-4...",
  "original_width": 1920,
  ...
}
```

### 2. Multi-file Batch Payload (`/api/batch/upload`)

Constructs a Celery multi-job queue execution.

```bash
curl -X POST "http://localhost:8001/api/batch/upload" \
  -H "Accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "files=@video_1.mkv" \
  -F "files=@video_2.mov" \
  -F "target_mb=50"
```

### 3. Server-Sent Events Logs (`/api/stream/{task_id}`)

Binds native connection headers expecting raw text streams detailing encoder metrics (requires a parser if doing this via raw CLI, otherwise natively handled by `EventSource` in JS).

```bash
curl -N -H "Accept: text/event-stream" \
  "http://localhost:8001/api/stream/bfd91d1e-8e8f-4..."
```

### 4. Fetch Download Artifact (`/api/jobs/{task_id}/download`)

```bash
curl -OJ "http://localhost:8001/api/jobs/bfd91d1e-8e8f-4.../download"
```
