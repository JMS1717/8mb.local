"""SSE streaming route handler."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import AsyncGenerator

import orjson
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..deps import redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])


async def _sse_event_generator(task_id: str) -> AsyncGenerator[bytes, None]:
    """SSE stream combining Redis pubsub messages with periodic heartbeats.

    Heartbeats help keep connections alive across proxies that drop idle SSE.
    """
    channel = f"progress:{task_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    queue: asyncio.Queue[str] = asyncio.Queue()
    
    await queue.put(orjson.dumps({"type": "connected", "task_id": task_id, "ts": time.time()}).decode())

    async def reader():
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                logger.debug("[SSE %s] msg", task_id[:8])
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
        logger.debug("[SSE %s] stream started", task_id[:8])
        while True:
            data = await queue.get()
            yield f"data: {data}\n\n".encode()
    finally:
        logger.debug("[SSE %s] stream closing", task_id[:8])
        reader_task.cancel()
        hb_task.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.close()


@router.get("/api/stream/{task_id}")
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
