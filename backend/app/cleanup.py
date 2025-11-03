from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import get_settings

settings = get_settings()


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    for path in root.iterdir():
        if path.is_file():
            yield path


def remove_stale_files() -> None:
    cutoff = datetime.utcnow() - timedelta(seconds=settings.file_ttl_seconds)
    for directory in (settings.uploads_dir, settings.outputs_dir):
        for file in _iter_files(directory):
            mtime = datetime.utcfromtimestamp(file.stat().st_mtime)
            if mtime < cutoff:
                try:
                    file.unlink()
                except OSError:
                    continue


def setup_cleanup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(remove_stale_files, "interval", seconds=settings.cleanup_interval_seconds)
    scheduler.start()
    return scheduler
