"""Celery wiring for Docker and the local desktop runtime.

Docker uses the normal Redis-backed Celery application.  The native desktop
build imports the same routes and worker task functions but replaces only the
transport with a bounded in-process executor.  Keeping this seam here avoids
duplicating compression logic or maintaining a second API for Windows.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .config import settings
from shared.concurrency import resolve_worker_concurrency

logger = logging.getLogger(__name__)


def _local_enabled() -> bool:
    return os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"}


if _local_enabled():
    from shared.local_runtime import (
        cancel_task as _cancel_task,
        ensure_task as _ensure_task,
        task_snapshot as _task_snapshot,
        update_task as _update_task,
    )

    class LocalTaskResult:
        """Small AsyncResult-compatible view over process-local task state."""

        def __init__(self, task_id: str, future: Future[Any] | None = None) -> None:
            self.id = str(task_id)
            self._future = future

        @property
        def state(self) -> str:
            snapshot = _task_snapshot(self.id)
            return str(snapshot.get("state") if snapshot else "PENDING")

        @property
        def info(self) -> dict[str, Any]:
            snapshot = _task_snapshot(self.id)
            return dict(snapshot.get("info") or {}) if snapshot else {}

        @property
        def result(self) -> Any:
            snapshot = _task_snapshot(self.id)
            if not snapshot:
                return None
            if snapshot.get("state") == "FAILURE":
                return snapshot.get("error") or snapshot.get("info", {}).get("detail")
            return snapshot.get("result")

        def ready(self) -> bool:
            return self.state in {"SUCCESS", "FAILURE", "REVOKED"}

        def successful(self) -> bool:
            return self.state == "SUCCESS"

        def get(self, timeout: float | None = None, propagate: bool = True, **_: Any) -> Any:
            if self._future is not None:
                try:
                    return self._future.result(timeout=timeout)
                except Exception:
                    if propagate:
                        raise
                    return self.result

            deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
            while not self.ready():
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for task {self.id}")
                time.sleep(0.02)
            if self.state == "FAILURE" and propagate:
                raise RuntimeError(str(self.result or "Local task failed"))
            return self.result


    class LocalSignature:
        def __init__(
            self,
            app: "LocalCelery",
            name: str,
            *,
            args: tuple[Any, ...] = (),
            kwargs: dict[str, Any] | None = None,
            options: dict[str, Any] | None = None,
        ) -> None:
            self.app = app
            self.name = name
            self.args = tuple(args)
            self.kwargs = dict(kwargs or {})
            self.options = dict(options or {})

        def set(self, **options: Any) -> "LocalSignature":
            self.options.update(options)
            return self

        def apply_async(self, **options: Any) -> LocalTaskResult:
            merged = {**self.options, **options}
            return self.app.send_task(
                self.name,
                args=self.args,
                kwargs=self.kwargs,
                task_id=merged.pop("task_id", None),
                **merged,
            )


    class LocalGroup:
        def __init__(self, signatures: tuple[LocalSignature, ...]) -> None:
            self.signatures = signatures

        def apply_async(self, **options: Any) -> list[LocalTaskResult]:
            return [signature.apply_async(**options) for signature in self.signatures]


    class LocalCelery:
        """Run the existing worker task entry points in bounded local threads."""

        def __init__(self) -> None:
            workers = resolve_worker_concurrency(
                os.getenv("WORKER_CONCURRENCY", settings.WORKER_CONCURRENCY)
            )
            self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="8mblocal")
            self._futures: dict[str, Future[Any]] = {}
            self._lock = threading.RLock()
            self._shutting_down = False
            self.control = _LocalControl(self)

        def signature(
            self,
            name: str,
            args: tuple[Any, ...] | list[Any] | None = None,
            kwargs: dict[str, Any] | None = None,
            immutable: bool = False,
            **_: Any,
        ) -> LocalSignature:
            return LocalSignature(self, name, args=tuple(args or ()), kwargs=kwargs)

        def group(self, *signatures: LocalSignature) -> LocalGroup:
            return LocalGroup(tuple(signatures))

        def send_task(
            self,
            name: str,
            args: tuple[Any, ...] | list[Any] | None = None,
            kwargs: dict[str, Any] | None = None,
            task_id: str | None = None,
            **_: Any,
        ) -> LocalTaskResult:
            task_id = str(task_id or uuid.uuid4())
            with self._lock:
                if self._shutting_down:
                    raise RuntimeError("Local worker is shutting down")
            _ensure_task(task_id)
            future = self._executor.submit(
                self._execute, task_id, name, tuple(args or ()), dict(kwargs or {})
            )
            with self._lock:
                self._futures[task_id] = future
            return LocalTaskResult(task_id, future)

        def AsyncResult(self, task_id: str) -> LocalTaskResult:  # noqa: N802 - Celery API
            with self._lock:
                future = self._futures.get(str(task_id))
            return LocalTaskResult(str(task_id), future)

        def _execute(
            self,
            task_id: str,
            name: str,
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> Any:
            snapshot = _task_snapshot(task_id)
            if (snapshot and snapshot.get("state") == "REVOKED") or _is_cancel_requested(task_id):
                _update_task(task_id, state="REVOKED", info={"detail": "Cancellation requested"})
                return None
            _update_task(task_id, state="STARTED", info={"progress": 0.0, "phase": "queued"})
            try:
                # Dynamic import keeps the desktop launcher light and avoids a
                # backend↔worker import cycle during normal Docker startup.
                from worker.app import tasks as worker_tasks

                task_name = name.rsplit(".", 1)[-1]
                task_name = {
                    "get_hardware_info": "get_hardware_info_task",
                    "run_hardware_tests": "run_hardware_tests_task",
                }.get(task_name, task_name)
                task = getattr(worker_tasks, task_name)
                # Use Celery's eager task boundary instead of manually
                # pushing a request onto the Task object.  The latter works
                # in an unfrozen interpreter but can resolve to a task proxy
                # with no request stack in a PyInstaller process, causing
                # even the hardware-info task to fail with ``None.push``.
                # ``apply`` also supplies the correct bound ``self`` for
                # compression tasks while keeping execution in this bounded
                # local thread.
                eager_result = task.apply(
                    args=args,
                    kwargs=kwargs,
                    task_id=task_id,
                    throw=True,
                )
                value = eager_result.get(propagate=True)
                snapshot = _task_snapshot(task_id) or {}
                _update_task(
                    task_id,
                    state="SUCCESS",
                    info=snapshot.get("info") or {"detail": "done"},
                    result=value,
                )
                return value
            except Exception as exc:
                snapshot = _task_snapshot(task_id) or {}
                canceled = snapshot.get("state") == "REVOKED" or _is_cancel_requested(task_id)
                if canceled:
                    _update_task(task_id, state="REVOKED", info=snapshot.get("info") or {}, error=str(exc))
                    logger.info("local task canceled id=%s name=%s", task_id, name)
                else:
                    _update_task(task_id, state="FAILURE", info=snapshot.get("info") or {}, error=str(exc))
                    logger.exception("local task failed id=%s name=%s", task_id, name)
                raise

        def _revoke(self, task_id: str) -> None:
            _cancel_task(str(task_id))
            with self._lock:
                future = self._futures.get(str(task_id))
            if future is not None and future.cancel():
                _update_task(str(task_id), state="REVOKED", info={"detail": "Cancellation requested"})
            elif future is None:
                # Preserve Celery's behavior for an unknown task ID without
                # overwriting a result that may have completed already.
                _update_task(str(task_id), state="REVOKED", info={"detail": "Cancellation requested"})

        def shutdown(self) -> None:
            """Cancel local jobs and wait for FFmpeg workers to stop.

            Active encodes poll the shared cancellation flag every 250 ms and
            terminate their FFmpeg process. Waiting here prevents a windowed
            desktop process from surviving invisibly after its UI closes.
            """
            with self._lock:
                if self._shutting_down:
                    return
                self._shutting_down = True
                active = [
                    (task_id, future)
                    for task_id, future in self._futures.items()
                    if not future.done()
                ]
            for task_id, future in active:
                _cancel_task(task_id)
                future.cancel()
            self._executor.shutdown(wait=True, cancel_futures=True)


    def _is_cancel_requested(task_id: str) -> bool:
        from shared.local_runtime import get_sync_redis

        return str(get_sync_redis().get(f"cancel:{task_id}")) == "1"


    class _LocalControl:
        def __init__(self, app: LocalCelery) -> None:
            self.app = app

        def revoke(self, task_id: str, terminate: bool = False, **_: Any) -> None:
            self.app._revoke(task_id)


    celery_app = LocalCelery()
    group = celery_app.group

else:
    from celery import Celery, group as celery_group

    REDIS_URL = settings.REDIS_URL

    celery_app = Celery(
        "8mblocal",
        broker=REDIS_URL,
        backend=REDIS_URL,
    )

    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        worker_send_task_events=True,
        task_send_sent_event=True,
    )
    group = celery_group
