"""In-process Redis/Celery-compatible state for the native desktop build.

The desktop application deliberately reuses the production FastAPI routes and
worker task functions.  Docker still uses real Redis and Celery; this module
only supplies the narrow interfaces those routes need when the application is
running locally on Windows.

It is intentionally small rather than a second queue implementation: tasks
run in a bounded ``ThreadPoolExecutor`` owned by ``backend.celery_app`` and
the store below provides process-local state, cancellation, and pub/sub.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable


def enabled() -> bool:
    return os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class _TaskRecord:
    state: str = "PENDING"
    info: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class _Subscription:
    def __init__(self, store: "_LocalStore") -> None:
        self.store = store
        self.channels: set[str] = set()
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.closed = False


class _LocalStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.values: dict[str, tuple[str, float | None]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.subscribers: dict[str, set[_Subscription]] = {}
        self.tasks: dict[str, _TaskRecord] = {}

    def _purge_expired(self, key: str) -> None:
        entry = self.values.get(key)
        if entry and entry[1] is not None and entry[1] <= time.time():
            self.values.pop(key, None)

    def get(self, key: str) -> str | None:
        with self.lock:
            self._purge_expired(key)
            entry = self.values.get(key)
            return entry[0] if entry else None

    def set(self, key: str, value: Any, ex: int | float | None = None) -> bool:
        expiry = time.time() + float(ex) if ex is not None else None
        with self.lock:
            self.values[str(key)] = (str(value), expiry)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        with self.lock:
            for key in keys:
                if key in self.values:
                    removed += 1
                    self.values.pop(key, None)
        return removed

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        with self.lock:
            target = self.sorted_sets.setdefault(str(key), {})
            added = 0
            for member, score in mapping.items():
                if str(member) not in target:
                    added += 1
                target[str(member)] = float(score)
            return added

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        with self.lock:
            members = sorted(
                self.sorted_sets.get(str(key), {}).items(),
                key=lambda item: (item[1], item[0]),
            )
            if end == -1:
                end = len(members) - 1
            if start < 0:
                start = max(len(members) + start, 0)
            if end < 0:
                end = len(members) + end
            return [member for member, _ in members[start : end + 1]]

    def zrem(self, key: str, *members: str) -> int:
        with self.lock:
            target = self.sorted_sets.get(str(key), {})
            removed = 0
            for member in members:
                if str(member) in target:
                    target.pop(str(member), None)
                    removed += 1
            return removed

    def subscribe(self, subscription: _Subscription, channels: Iterable[str]) -> None:
        with self.lock:
            for channel in channels:
                channel = str(channel)
                subscription.channels.add(channel)
                self.subscribers.setdefault(channel, set()).add(subscription)

    def unsubscribe(self, subscription: _Subscription, channels: Iterable[str] | None = None) -> None:
        with self.lock:
            selected = set(channels) if channels is not None else set(subscription.channels)
            for channel in selected:
                subscribers = self.subscribers.get(channel)
                if subscribers:
                    subscribers.discard(subscription)
                    if not subscribers:
                        self.subscribers.pop(channel, None)
                subscription.channels.discard(channel)

    def publish(self, channel: str, value: Any) -> int:
        message = {
            "type": "message",
            "pattern": None,
            "channel": str(channel),
            "data": str(value),
        }
        with self.lock:
            subscribers = list(self.subscribers.get(str(channel), ()))
        for subscriber in subscribers:
            if not subscriber.closed:
                subscriber.messages.put(message)
        return len(subscribers)

    def ensure_task(self, task_id: str) -> _TaskRecord:
        with self.lock:
            return self.tasks.setdefault(str(task_id), _TaskRecord())

    def update_task(
        self,
        task_id: str,
        *,
        state: str | None = None,
        info: dict[str, Any] | None = None,
        result: Any = None,
        error: str | None = None,
    ) -> _TaskRecord:
        with self.lock:
            record = self.tasks.setdefault(str(task_id), _TaskRecord())
            if state:
                record.state = state
            if info:
                record.info.update(info)
            if result is not None:
                record.result = result
            if error is not None:
                record.error = error
                record.info.setdefault("detail", error)
            if record.state in {"SUCCESS", "FAILURE", "REVOKED", "CANCELED"}:
                record.finished_at = record.finished_at or time.time()
            return record

    def task_snapshot(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            record = self.tasks.get(str(task_id))
            if record is None:
                return None
            return {
                "state": record.state,
                "info": dict(record.info),
                "result": record.result,
                "error": record.error,
                "created_at": record.created_at,
                "finished_at": record.finished_at,
            }

    def reset(self) -> None:
        with self.lock:
            for subscriber in self.subscribers.values():
                for item in subscriber:
                    item.closed = True
            self.values.clear()
            self.sorted_sets.clear()
            self.subscribers.clear()
            self.tasks.clear()


_STORE = _LocalStore()


class LocalRedis:
    """Synchronous subset used by worker tasks and startup probes."""

    def get(self, key: str) -> str | None:
        return _STORE.get(key)

    def set(self, key: str, value: Any, ex: int | float | None = None, **_: Any) -> bool:
        return _STORE.set(key, value, ex=ex)

    def setex(self, key: str, seconds: int | float, value: Any) -> bool:
        return _STORE.set(key, value, ex=seconds)

    def delete(self, *keys: str) -> int:
        return _STORE.delete(*keys)

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        return _STORE.zadd(key, mapping)

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        return _STORE.zrange(key, start, end)

    def zrem(self, key: str, *members: str) -> int:
        return _STORE.zrem(key, *members)

    def publish(self, channel: str, value: Any) -> int:
        return _STORE.publish(channel, value)

    def close(self) -> None:
        return None


class LocalPubSub:
    """Async pub/sub facade with Redis-py-compatible method names."""

    def __init__(self) -> None:
        self._subscription = _Subscription(_STORE)

    async def subscribe(self, *channels: str) -> None:
        _STORE.subscribe(self._subscription, channels)

    async def unsubscribe(self, *channels: str) -> None:
        _STORE.unsubscribe(self._subscription, channels or None)

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while not self._subscription.closed:
            try:
                message = await asyncio.to_thread(self._subscription.messages.get, True, 0.25)
            except queue.Empty:
                continue
            if message is None:
                return
            yield message

    async def close(self) -> None:
        self._subscription.closed = True
        _STORE.unsubscribe(self._subscription)
        self._subscription.messages.put(None)


class AsyncLocalRedis:
    """Async Redis facade used by FastAPI routes."""

    async def get(self, key: str) -> str | None:
        return _STORE.get(key)

    async def set(self, key: str, value: Any, ex: int | float | None = None, **_: Any) -> bool:
        return _STORE.set(key, value, ex=ex)

    async def setex(self, key: str, seconds: int | float, value: Any) -> bool:
        return _STORE.set(key, value, ex=seconds)

    async def delete(self, *keys: str) -> int:
        return _STORE.delete(*keys)

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        return _STORE.zadd(key, mapping)

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        return _STORE.zrange(key, start, end)

    async def zrem(self, key: str, *members: str) -> int:
        return _STORE.zrem(key, *members)

    async def publish(self, channel: str, value: Any) -> int:
        return _STORE.publish(channel, value)

    def pubsub(self) -> LocalPubSub:
        return LocalPubSub()

    async def close(self) -> None:
        return None


_SYNC_REDIS = LocalRedis()
_ASYNC_REDIS = AsyncLocalRedis()


def get_sync_redis() -> LocalRedis:
    return _SYNC_REDIS


def get_async_redis() -> AsyncLocalRedis:
    return _ASYNC_REDIS


def ensure_task(task_id: str) -> None:
    _STORE.ensure_task(task_id)


def update_task(
    task_id: str,
    *,
    state: str | None = None,
    info: dict[str, Any] | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    _STORE.update_task(task_id, state=state, info=info, result=result, error=error)


def task_snapshot(task_id: str) -> dict[str, Any] | None:
    return _STORE.task_snapshot(task_id)


def cancel_task(task_id: str) -> None:
    """Record a cancellation request without racing the worker's cleanup.

    An active worker owns the terminal transition: it must stop FFmpeg and
    remove any partial output before the task becomes CANCELED.  The local
    Celery control path marks a task revoked immediately only when its future
    was still queued and could be canceled without running.
    """
    _STORE.set(f"cancel:{task_id}", "1", ex=3600)


def record_worker_event(task_id: str, event: dict[str, Any]) -> None:
    """Mirror worker progress into local result and queue metadata."""
    telemetry_keys = (
        "requested_encoder", "resolved_encoder", "actual_encoder",
        "hardware_used", "hardware_type", "hardware_device", "render_device",
        "fallback_occurred", "fallback_stage", "fallback_reason", "decoder",
    )
    kind = str(event.get("type") or "log")
    task_state = {
        "progress": "PROGRESS",
        "log": "STARTED",
        "done": "SUCCESS",
        "error": "FAILURE",
        "canceled": "CANCELED",
    }.get(kind)
    info = dict(event)
    if kind == "done":
        # Keep the fields consumed by the download route at the top level of
        # the local AsyncResult metadata.  The worker publishes them inside
        # ``done.stats`` because that is the Docker/SSE contract; flattening
        # here makes the desktop runtime behave the same way.
        stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
        for key in (
            "output_path", "final_size_mb", "duration_s", "target_size_mb", "encoder",
            "requested_encoder", "resolved_encoder", "actual_encoder", "hardware_used",
            "hardware_device", "render_device", "fallback_occurred", "fallback_stage",
            "fallback_reason", "hardware_type", "decoder",
        ):
            if stats.get(key) is not None:
                info[key] = stats[key]
        info.setdefault("progress", 100.0)
        info.setdefault("detail", "done")
    elif kind == "telemetry":
        telemetry = event.get("telemetry") if isinstance(event.get("telemetry"), dict) else {}
        for key in telemetry_keys:
            if telemetry.get(key) is not None:
                info[key] = telemetry[key]
    if kind == "error":
        update_task(task_id, state=task_state, info=info, error=str(event.get("message") or "Compression failed"))
    elif kind == "done":
        update_task(task_id, state=task_state, info=info)
    else:
        update_task(task_id, state=task_state, info=info)

    raw = _STORE.get(f"job:{task_id}")
    if not raw:
        return
    try:
        job = json.loads(raw)
    except (TypeError, ValueError):
        return
    now = time.time()
    if kind in {"log", "progress", "retry", "telemetry"}:
        job["state"] = "running"
        job.setdefault("started_at", now)
        if kind == "progress":
            job["progress"] = max(0.0, min(100.0, float(event.get("progress") or 0.0)))
            job["phase"] = event.get("phase") or job.get("phase") or "encoding"
    telemetry = event.get("telemetry") if isinstance(event.get("telemetry"), dict) else {}
    if kind == "done" and isinstance(event.get("stats"), dict):
        telemetry = event["stats"]
    for key in telemetry_keys:
        if telemetry.get(key) is not None:
            job[key] = telemetry[key]
    if kind == "done":
        job.update({"state": "completed", "phase": "done", "progress": 100.0, "completed_at": now})
        stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
        if stats.get("output_path"):
            job["output_path"] = stats["output_path"]
        if stats.get("final_size_mb") is not None:
            job["final_size_mb"] = stats["final_size_mb"]
    elif kind == "canceled":
        job.update({"state": "canceled", "phase": "canceled", "progress": 100.0, "completed_at": now})
    elif kind == "error":
        job.update({
            "state": "failed", "phase": "done", "progress": 100.0,
            "completed_at": now, "error": event.get("message") or "Compression failed",
        })
    _STORE.set(f"job:{task_id}", json.dumps(job), ex=86400)


def reset_for_tests() -> None:
    """Clear process-local state; intended for tests and launcher restarts."""
    _STORE.reset()
