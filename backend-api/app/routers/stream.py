"""SSE streaming route handler."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import AsyncGenerator

import orjson
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..auth import basic_auth
from ..celery_app import celery_app
from ..deps import redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])


def _terminal_or_progress_event(task_id: str) -> dict | None:
    """Replay durable task state so reconnects cannot miss the terminal event."""
    result = celery_app.AsyncResult(task_id)
    state = str(result.state or "PENDING").upper()
    info = result.info if isinstance(result.info, dict) else {}

    if state in {"STARTED", "PROGRESS"}:
        return {
            "type": "progress",
            "task_id": task_id,
            "progress": info.get("progress", 0.0),
            "phase": info.get("phase") or "encoding",
        }
    if state == "SUCCESS":
        stats = info.get("stats") if isinstance(info.get("stats"), dict) else {}
        if not stats and isinstance(result.result, dict):
            stats = result.result
        return {"type": "done", "task_id": task_id, "stats": stats}
    if state == "FAILURE":
        detail = info.get("detail") or info.get("message")
        if not detail:
            try:
                detail = str(result.result or "Compression failed")
            except Exception:
                detail = "Compression failed"
        return {"type": "error", "task_id": task_id, "message": str(detail)}
    if state in {"REVOKED", "CANCELED"}:
        return {"type": "canceled", "task_id": task_id, "message": "Job canceled by user"}
    return None


async def _sse_event_generator(task_id: str) -> AsyncGenerator[bytes, None]:
    """SSE stream combining Redis pubsub messages with periodic heartbeats.

    Heartbeats help keep connections alive across proxies that drop idle SSE.
    """
    channel = f"progress:{task_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    queue: asyncio.Queue[str] = asyncio.Queue()

    await queue.put(orjson.dumps({"type": "connected", "task_id": task_id, "ts": time.time()}).decode())
    try:
        replay = await asyncio.to_thread(_terminal_or_progress_event, task_id)
        if replay is not None:
            await queue.put(orjson.dumps(replay).decode())
    except Exception as exc:
        logger.debug("[SSE %s] status replay unavailable: %s", task_id[:8], exc)

    async def reader():
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                # Per-message trace is extremely noisy (ffmpeg progress lines every ~100ms).
                if logger.isEnabledFor(logging.DEBUG):
                    preview = data[:120] if isinstance(data, str) else data
                    logger.debug("[SSE %s] redis: %s", task_id[:8], preview)
                await queue.put(str(data))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("[SSE %s] pubsub error: %s", task_id[:8], e)
            try:
                await queue.put(orjson.dumps({"type": "error", "message": f"[SSE] pubsub error: {e}"}).decode())
            except Exception:
                pass

    async def heartbeater():
        try:
            while True:
                await asyncio.sleep(20)
                try:
                    await queue.put(orjson.dumps({"type": "ping", "ts": time.time()}).decode())
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    reader_task = asyncio.create_task(reader())
    hb_task = asyncio.create_task(heartbeater())
    try:
        logger.info("[SSE %s] stream opened", task_id[:8])
        while True:
            data = await queue.get()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "[SSE %s] yield %s",
                    task_id[:8],
                    data[:120] if len(data) > 120 else data,
                )
            yield f"data: {data}\n\n".encode()
            try:
                event_type = orjson.loads(data).get("type")
            except Exception:
                event_type = None
            if event_type in {"done", "error", "canceled"}:
                return
    finally:
        logger.info("[SSE %s] stream closed", task_id[:8])
        reader_task.cancel()
        hb_task.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.close()


@router.get("/api/stream/{task_id}", dependencies=[Depends(basic_auth)])
async def stream(task_id: str):
    return StreamingResponse(
        _sse_event_generator(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )
