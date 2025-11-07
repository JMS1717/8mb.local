import asyncio
import os
import time
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from . import settings_manager

UPLOADS_DIR = "/app/uploads"
OUTPUTS_DIR = "/app/outputs"

async def cleanup_files():
    # Use dynamic retention from settings.json if present
    try:
        retention = settings_manager.get_retention_hours()
    except Exception:
        retention = settings.FILE_RETENTION_HOURS
    cutoff = datetime.utcnow() - timedelta(hours=retention)
    cutoff_ts = cutoff.timestamp()
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


def start_scheduler():
    scheduler = AsyncIOScheduler()
    # run every 15 minutes
    scheduler.add_job(lambda: asyncio.create_task(cleanup_files()), 'interval', minutes=15)
    scheduler.start()
