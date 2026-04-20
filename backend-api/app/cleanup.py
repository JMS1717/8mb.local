from __future__ import annotations

import os
import time
from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from . import settings_manager

UPLOADS_DIR = "/app/uploads"
OUTPUTS_DIR = "/app/outputs"


def cleanup_files() -> None:
    """Delete upload and output files older than the configured retention period."""
    # Use dynamic retention from settings.json if present
    try:
        retention = settings_manager.get_retention_hours()
    except Exception:
        retention = settings.FILE_RETENTION_HOURS
    cutoff_ts = time.time() - (retention * 3600)
    for base in (UPLOADS_DIR, OUTPUTS_DIR):
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
                if st.st_mtime < cutoff_ts:
                    os.remove(path)
            except Exception:
                continue


def start_scheduler() -> None:
    """Run the periodic cleanup job on a fixed interval."""
    scheduler = BackgroundScheduler()
    # run every 15 minutes
    scheduler.add_job(cleanup_files, 'interval', minutes=15)
    scheduler.start()
