from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from . import settings_manager

logger = logging.getLogger(__name__)

UPLOADS_DIR = "/app/uploads"
OUTPUTS_DIR = "/app/outputs"

# Module-level handle so the scheduler object is not garbage collected after
# start_scheduler() returns (APScheduler keeps internal refs via the event
# loop, but holding it explicitly makes shutdown/introspection possible too).
_scheduler: AsyncIOScheduler | None = None


def _cleanup_files_sync() -> None:
    """Blocking worker: delete files older than the configured retention window.

    Kept sync deliberately — all operations are blocking filesystem syscalls
    (os.listdir / os.stat / os.remove). The async wrapper offloads this to a
    thread so it cannot stall the FastAPI event loop.
    """
    try:
        retention = settings_manager.get_retention_hours()
    except Exception as e:
        logger.debug("cleanup: get_retention_hours failed (%s); using default", e)
        retention = settings.FILE_RETENTION_HOURS

    cutoff_ts = (datetime.utcnow() - timedelta(hours=retention)).timestamp()
    scanned = 0
    removed = 0
    total_bytes = 0

    for base in (UPLOADS_DIR, OUTPUTS_DIR):
        if not os.path.isdir(base):
            logger.debug("cleanup: skipping missing dir %s", base)
            continue
        try:
            entries = os.listdir(base)
        except OSError as e:
            logger.warning("cleanup: failed to list %s: %s", base, e)
            continue
        for name in entries:
            scanned += 1
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime >= cutoff_ts:
                continue
            try:
                sz = st.st_size
                os.remove(path)
                removed += 1
                total_bytes += sz
                logger.debug("cleanup: removed %s (%d bytes, age=%.1fh)",
                             path, sz, (cutoff_ts - st.st_mtime) / 3600 + retention)
            except OSError as e:
                logger.debug("cleanup: failed to remove %s: %s", path, e)

    if removed:
        logger.info(
            "cleanup: removed %d of %d file(s), freed %.1f MB (retention=%sh)",
            removed, scanned, total_bytes / (1024 * 1024), retention,
        )
    else:
        logger.debug(
            "cleanup: scanned %d file(s), nothing expired (retention=%sh)",
            scanned, retention,
        )


async def cleanup_files() -> None:
    """Async entrypoint used by APScheduler; offloads blocking IO to a thread."""
    await asyncio.to_thread(_cleanup_files_sync)


def start_scheduler() -> None:
    """Start the periodic cleanup job on a fixed interval.

    Idempotent: repeat calls are no-ops. The scheduled job is registered as a
    coroutine function so AsyncIOScheduler's AsyncIOExecutor runs it on the
    event loop via ``ensure_future`` (not in a thread where ``asyncio`` APIs
    would have no running loop).
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.debug("start_scheduler: already running — no-op")
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(cleanup_files, "interval", minutes=15, id="cleanup_files")
    _scheduler.start()
    logger.info("cleanup scheduler started (15-minute interval)")
