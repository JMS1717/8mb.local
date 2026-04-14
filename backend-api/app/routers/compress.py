"""Compress and queue management route handlers."""
from __future__ import annotations

import logging
import time
import uuid

import orjson
from fastapi import APIRouter, Depends, HTTPException

from ..auth import basic_auth
from ..celery_app import celery_app
from ..deps import (
    OUTPUTS_DIR,
    UPLOADS_DIR,
    build_output_name,
    redis,
    safe_filename,
    store_job_metadata,
)
from ..models import CompressRequest, JobMetadata, QueueStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["compress"])


@router.post("/api/compress", dependencies=[Depends(basic_auth)])
async def compress(req: CompressRequest):
    input_path = UPLOADS_DIR / safe_filename(req.filename)
    if not input_path.exists():
        raise HTTPException(status_code=404, detail="Input not found")

    task_id = str(uuid.uuid4())

    output_name = build_output_name(input_path, task_id, req.container, bool(req.audio_only or False))
    output_path = OUTPUTS_DIR / output_name
    
    task = celery_app.send_task(
        "worker.worker.compress_video",
        task_id=task_id,
        kwargs=dict(
            job_id=req.job_id,
            input_path=str(input_path),
            output_path=str(output_path),
            target_size_mb=req.target_size_mb,
            video_codec=req.video_codec,
            audio_codec=req.audio_codec,
            audio_bitrate_kbps=req.audio_bitrate_kbps,
            preset=req.preset,
            tune=req.tune,
            max_width=req.max_width,
            max_height=req.max_height,
            start_time=req.start_time,
            end_time=req.end_time,
            force_hw_decode=bool(req.force_hw_decode or False),
            fast_mp4_finalize=bool(req.fast_mp4_finalize or False),
            auto_resolution=bool(req.auto_resolution or False),
            min_auto_resolution=req.min_auto_resolution,
            target_resolution=req.target_resolution,
            audio_only=bool(req.audio_only or False),
        ),
    )
    try:
        await redis.publish(f"progress:{task.id}", orjson.dumps({"type":"log","message":"Job queued – waiting for worker…"}).decode())
    except Exception:
        pass
    
    await store_job_metadata(task.id, req.job_id, req.filename, req.target_size_mb, req.video_codec)
    
    return {"task_id": task.id}


@router.post("/api/jobs/{task_id}/cancel")
async def cancel_job(task_id: str):
    """Signal a running job to cancel and attempt to stop ffmpeg."""
    try:
        await redis.set(f"cancel:{task_id}", "1", ex=3600)
        await redis.publish(f"progress:{task_id}", orjson.dumps({"type":"log","message":"Cancellation requested"}).decode())
        try:
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            pass
        return {"status": "cancellation_requested"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/queue/clear")
async def clear_queue():
    """Clear all jobs from the queue (cancel running, remove pending/completed)."""
    try:
        job_ids = await redis.zrange("jobs:active", 0, -1)
        
        cancelled_count = 0
        removed_count = 0
        
        for task_id in job_ids:
            try:
                job_data = await redis.get(f"job:{task_id}")
                if job_data:
                    job_meta = JobMetadata(**orjson.loads(job_data))
                    
                    if job_meta.state in ('queued', 'running'):
                        await redis.set(f"cancel:{task_id}", "1", ex=3600)
                        await redis.publish(
                            f"progress:{task_id}", 
                            orjson.dumps({"type": "log", "message": "Queue cleared - job cancelled"}).decode()
                        )
                        try:
                            celery_app.control.revoke(task_id, terminate=True)
                        except Exception:
                            pass
                        cancelled_count += 1
                    
                    await redis.zrem("jobs:active", task_id)
                    await redis.delete(f"job:{task_id}")
                    removed_count += 1
            except Exception as e:
                logger.warning(f"Failed to clear job {task_id}: {e}")
                continue
        
        return {
            "status": "cleared",
            "cancelled": cancelled_count,
            "removed": removed_count,
            "total": len(job_ids)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/queue/status", response_model=QueueStatusResponse, dependencies=[Depends(basic_auth)])
async def queue_status():
    """Get current queue status showing all active, queued, and recently completed jobs."""
    try:
        job_ids = await redis.zrange("jobs:active", 0, -1)
        
        jobs = []
        for task_id in job_ids:
            try:
                job_data = await redis.get(f"job:{task_id}")
                if job_data:
                    job_meta = JobMetadata(**orjson.loads(job_data))
                    if job_meta.state in ('queued', 'running'):
                        try:
                            res = celery_app.AsyncResult(task_id)
                            celery_state = res.state
                            meta = res.info if isinstance(res.info, dict) else {}
                            
                            if celery_state == 'PENDING':
                                job_meta.state = 'queued'
                                job_meta.phase = 'queued'
                            elif celery_state in ('STARTED', 'PROGRESS'):
                                job_meta.state = 'running'
                                old_progress = job_meta.progress
                                job_meta.progress = meta.get('progress', job_meta.progress)
                                
                                if 'phase' in meta:
                                    job_meta.phase = meta['phase']
                                elif job_meta.progress < 95:
                                    job_meta.phase = 'encoding'
                                elif job_meta.progress < 100:
                                    job_meta.phase = 'finalizing'
                                else:
                                    job_meta.phase = 'done'
                                
                                if not job_meta.started_at:
                                    job_meta.started_at = time.time()
                                
                                now_ts = time.time()
                                if job_meta.progress > old_progress and job_meta.progress > 0:
                                    job_meta.last_progress_update = now_ts
                                    elapsed = now_ts - job_meta.started_at
                                    if job_meta.progress < 100:
                                        estimated_total_time = elapsed / (job_meta.progress / 100.0)
                                        job_meta.estimated_completion_time = job_meta.started_at + estimated_total_time
                                
                            elif celery_state == 'SUCCESS':
                                job_meta.state = 'completed'
                                job_meta.progress = 100.0
                                job_meta.phase = 'done'
                                if not job_meta.completed_at:
                                    job_meta.completed_at = time.time()
                                job_meta.output_path = meta.get('output_path')
                                if 'final_size_mb' in meta:
                                    job_meta.final_size_mb = meta.get('final_size_mb')
                            elif celery_state == 'FAILURE':
                                job_meta.state = 'failed'
                                job_meta.phase = 'done'
                                if not job_meta.completed_at:
                                    job_meta.completed_at = time.time()
                                job_meta.error = str(meta) if meta else 'Unknown error'
                            
                            await redis.setex(f"job:{task_id}", 86400, orjson.dumps(job_meta.dict()).decode())
                        except Exception:
                            pass
                    
                    jobs.append(job_meta)
            except Exception as e:
                logger.warning(f"Failed to load job {task_id}: {e}")
                continue
        
        now = time.time()
        for job in jobs:
            if job.state in ('completed', 'failed', 'canceled') and job.completed_at:
                if now - job.completed_at > 3600:
                    try:
                        await redis.zrem("jobs:active", job.task_id)
                    except Exception:
                        pass
        
        queued = sum(1 for j in jobs if j.state == 'queued')
        running = sum(1 for j in jobs if j.state == 'running')
        completed = sum(1 for j in jobs if j.state == 'completed' and j.completed_at and (now - j.completed_at) < 3600)
        
        return QueueStatusResponse(
            active_jobs=jobs,
            queued_count=queued,
            running_count=running,
            completed_count=completed
        )
    except Exception as e:
        logger.error(f"Queue status error: {e}")
        return QueueStatusResponse(active_jobs=[], queued_count=0, running_count=0, completed_count=0)
