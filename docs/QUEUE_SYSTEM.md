# Multi-Job Queue System

## Overview

The 8mb.local queue system allows multiple users to submit compression jobs simultaneously, with live progress tracking and parallel processing.

## Features

### Multi-User Support
- Multiple users can upload and compress videos at the same time
- Each user can submit multiple jobs
- Jobs are tracked independently with unique task IDs

### Parallel Processing
- Worker configured with concurrency of 4 (adjustable in `supervisord.conf`)
- Jobs run in parallel up to the concurrency limit
- Additional jobs are automatically queued

### Live Progress Tracking
- Real-time job status updates via `/queue` page
- Individual SSE streams per job for live logs
- Queue automatically updates every 2 seconds

### Job States
- **queued**: Job submitted, waiting for available worker
- **running**: Currently being processed
- **completed**: Finished successfully, file ready for download
- **failed**: Error occurred during processing
- **canceled**: User canceled the job

## API Endpoints

### Queue Status
```
GET /api/queue/status
```
Returns current queue state with all active jobs.

**Response:**
```json
{
  "active_jobs": [
    {
      "task_id": "abc123...",
      "job_id": "def456...",
      "filename": "video.mp4",
      "target_size_mb": 25.0,
      "video_codec": "h264_nvenc",
      "state": "running",
      "progress": 45.5,
      "created_at": 1730000000,
      "started_at": 1730000010,
      "completed_at": null,
      "error": null,
      "output_path": null,
      "final_size_mb": null
    }
  ],
  "queued_count": 2,
  "running_count": 3,
  "completed_count": 5
}
```

### Job Progress Stream (unchanged)
```
GET /api/stream/{task_id}
```
Server-Sent Events stream for real-time job progress and logs.

### Submit Compression (unchanged)
```
POST /api/compress
```
Submits a new compression job. Job is automatically queued.

## User Interface

### Main Page (/)
- Upload and analyze videos as before
- Click "Compress" to add job to queue
- Automatically redirects to `/queue` page after submission

### Queue Page (/queue)
- View all active jobs across all users
- Real-time progress updates
- Expandable live logs for running jobs
- Download completed files
- Queue summary stats at top

### Features per Job Card
- State indicator with icon and color
- Progress bar for running/queued jobs
- Job metadata (target size, codec, timestamps)
- Duration tracking for running jobs
- Download button for completed jobs
- Expandable live log viewer for running jobs

## Architecture

### Backend
- **Redis**: Job metadata storage and SSE pub/sub
- **Celery**: Distributed task queue with Redis backend
- **FastAPI**: REST API and SSE streaming

### Job Lifecycle
1. User submits compression via `/api/compress`
2. Backend creates job metadata in Redis (`job:{task_id}`)
3. Job added to Redis sorted set (`jobs:active`)
4. Celery worker picks up job when available
5. Worker publishes progress via Redis pub/sub channel
6. Frontend polls `/api/queue/status` and subscribes to SSE
7. On completion, job remains in queue for 1 hour

### Storage
- Job metadata: Redis with 24h TTL
- Active jobs index: Redis sorted set by creation time
- Completed jobs: Automatically cleaned after 1 hour

## Configuration

### Worker Concurrency
Edit `supervisord.conf`:
```ini
[program:worker]
command=celery -A worker.celery_app worker --loglevel=info -n 8mblocal@%%h --concurrency=4
```

Change `--concurrency=4` to desired parallel job count.

**Note**: Higher concurrency requires more CPU/GPU resources. For NVENC, most GPUs can handle 2-4 concurrent encodes efficiently.

### Queue Polling Interval
Edit `frontend/src/routes/queue/+page.svelte`:
```typescript
pollInterval = setInterval(fetchQueueStatus, 2000); // 2 seconds
```

## Multi-User Scenarios

### Scenario 1: Two Users, Four Jobs
- User A submits 2 jobs
- User B submits 2 jobs
- With concurrency=4: All 4 jobs run in parallel

### Scenario 2: Queue Overflow
- 6 jobs submitted with concurrency=4
- First 4 jobs run immediately
- Remaining 2 jobs wait in queue (state: "queued")
- As jobs complete, queued jobs start automatically

### Scenario 3: Mixed Progress
- Users see all jobs in the queue
- Each job shows independent progress
- Completed jobs remain visible for 1 hour
- Download links work for any user (if auth is disabled)

## Limitations

- No per-user authentication/isolation (all users see all jobs)
- No priority system (FIFO queue)
- No job cancellation from queue page (yet)
- Completed jobs auto-expire after 1 hour

## Future Enhancements

- [ ] Per-user job filtering
- [ ] Job priority levels
- [ ] Cancel button in queue UI
- [ ] Job retry on failure
- [ ] Persistent queue across restarts
- [ ] Email/webhook notifications on completion
