from __future__ import annotations

import uuid
from datetime import datetime

import aiofiles
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from .config import get_settings
from .jobs import (
    JOB_KEY_PREFIX,
    JobMetadata,
    channel_name,
    get_job,
    job_key,
    publish,
    store_job,
)
from .schemas import JobCreateResponse, JobRequest, JobStatusResponse
from .security import basic_auth, enforce_websocket_auth
from .tasks import compress_video
from .cleanup import setup_cleanup_scheduler
from .utils import estimate_bitrates, run_ffprobe

settings = get_settings()
app = FastAPI(title="8mb.local API")

cleanup_scheduler = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/outputs", StaticFiles(directory=settings.outputs_dir), name="outputs")


async def redis_client() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


@app.on_event("startup")
async def startup_event() -> None:
    global cleanup_scheduler
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    cleanup_scheduler = setup_cleanup_scheduler()


async def save_upload(file: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(destination, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            await buffer.write(chunk)


@app.post("/api/jobs", response_model=JobCreateResponse)
async def create_job(
    request: JobRequest = Depends(JobRequest.as_form),
    file: UploadFile = File(...),
    _credentials=Depends(basic_auth),
):
    uploads_dir = settings.uploads_dir
    job_id = str(uuid.uuid4())
    input_path = uploads_dir / f"{job_id}_{file.filename}"
    await save_upload(file, input_path)

    probe = await run_ffprobe(input_path)
    estimate, bitrate_payload = estimate_bitrates(probe, request)

    extension = "mkv" if request.audio_codec == "libopus" else "mp4"
    output_path = settings.outputs_dir / f"{job_id}.{extension}"

    metadata = JobMetadata(
        job_id=job_id,
        status="queued",
        input_path=str(input_path),
        output_path=str(output_path),
        payload={
            "filename": file.filename,
            "target_size_mb": request.target_size_mb,
            "video_codec": request.video_codec,
            "audio_codec": request.audio_codec,
            "preset": request.preset,
            "audio_bitrate_kbps": request.audio_bitrate_kbps,
            **bitrate_payload,
            "duration": probe.duration,
            "original_bitrate_kbps": probe.bitrate_kbps,
            "original_audio_bitrate_kbps": probe.audio_bitrate_kbps,
            "original_size_mb": probe.size_mb,
        },
    )

    redis = await redis_client()
    await store_job(redis, metadata)
    await redis.hset(
        f"{job_key(job_id)}:state",
        mapping={
            "job_id": job_id,
            "status": "queued",
            "progress": 0.0,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "extension": extension,
            "created_at": metadata.created_at.isoformat(),
            "updated_at": metadata.updated_at.isoformat(),
        },
    )

    payload = {
        "job_id": job_id,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "video_codec": request.video_codec,
        "extension": extension,
        "audio_codec": request.audio_codec,
        "preset": request.preset,
        "audio_bitrate_kbps": request.audio_bitrate_kbps,
        **bitrate_payload,
        "duration": probe.duration,
    }

    await file.close()

    compress_video.delay(job_id, payload)

    await publish(
        redis,
        job_id,
        {
            "type": "status",
            "payload": {
                "status": "queued",
                "estimate": estimate.model_dump(),
                "probe": probe.model_dump(),
            },
        },
    )

    await redis.aclose()
    return JobCreateResponse(
        job_id=job_id,
        probe=probe,
        estimate=estimate,
        status="queued",
    )


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, _credentials=Depends(basic_auth)):
    redis = await redis_client()
    state = await redis.hgetall(f"{job_key(job_id)}:state")
    if not state:
        raise HTTPException(status_code=404, detail="Job not found")
    payload = await get_job(redis, job_id)
    await redis.aclose()
    return JobStatusResponse(
        job_id=job_id,
        status=state.get("status", "unknown"),
        progress=float(state.get("progress", 0.0)),
        output_url=state.get("output_path"),
        created_at=datetime.fromisoformat(state.get("created_at")),
        updated_at=datetime.fromisoformat(state.get("updated_at")),
        payload=payload.payload if payload else {},
    )


@app.websocket("/ws/jobs/{job_id}")
async def job_updates(websocket: WebSocket, job_id: str):
    authorized = await enforce_websocket_auth(websocket)
    if not authorized:
        return
    await websocket.accept()
    redis = await redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_name(job_id))
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        await pubsub.unsubscribe(channel_name(job_id))
    finally:
        await pubsub.close()
        await redis.aclose()


@app.get("/api/jobs/{job_id}/log")
async def job_log(job_id: str, limit: int = 200, _credentials=Depends(basic_auth)):
    redis = await redis_client()
    key = f"{JOB_KEY_PREFIX}:{job_id}:log"
    entries = await redis.lrange(key, -limit, -1)
    await redis.aclose()
    return {"log": entries}


@app.get("/healthz")
async def healthcheck_endpoint():
    return {"status": "ok"}


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global cleanup_scheduler
    if cleanup_scheduler:
        cleanup_scheduler.shutdown(wait=False)
