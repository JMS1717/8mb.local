"""8mb.local API entry point.

Creates the FastAPI application, registers middleware and startup hooks,
mounts all route modules, and serves the pre-built SvelteKit SPA.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import configure_logging, settings
from .cleanup import start_scheduler
from .deps import (
    UPLOADS_DIR,
    OUTPUTS_DIR,
    get_hw_info_cached,
    redis,
    sync_codec_settings_from_tests,
)
from . import settings_manager

from .routers import upload, compress, stream, download
from .routers import settings as settings_router
from .routers import system

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(title="8mb.local API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------
app.include_router(upload.router)
app.include_router(compress.router)
app.include_router(stream.router)
app.include_router(download.router)
app.include_router(settings_router.router)
app.include_router(system.router)

# ---------------------------------------------------------------------------
# Startup hooks
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    start_scheduler()
    try:
        boot_id = str(uuid.uuid4())
        try:
            await redis.set("startup:boot_id", boot_id)
            await redis.set("startup:boot_ts", str(int(time.time())))
        except Exception as e:
            logger.warning(f"Failed to set boot_id in Redis: {e}")
        asyncio.create_task(sync_codec_settings_from_tests())
    except Exception as e:
        logger.warning(f"Startup initialization failed: {e}")


@app.on_event("startup")
async def startup_event():
    settings_manager.initialize_env_if_missing()
    # Note: start_scheduler() is already called in on_startup() above
    try:
        _ = get_hw_info_cached()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Serve pre-built frontend (unified container deployment)
# ---------------------------------------------------------------------------
frontend_build = Path("/app/frontend-build")
if frontend_build.exists():
    app.mount("/_app", StaticFiles(directory=frontend_build / "_app"), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve SPA - return index.html for all non-API routes"""
        file_path = frontend_build / full_path
        if file_path.is_file():
            media_type = None
            if full_path.endswith('.svg'):
                media_type = "image/svg+xml"
            elif full_path.endswith('.png'):
                media_type = "image/png"
            elif full_path.endswith('.ico'):
                media_type = "image/x-icon"
            elif full_path.endswith('.jpg') or full_path.endswith('.jpeg'):
                media_type = "image/jpeg"
            return FileResponse(file_path, media_type=media_type)
        return FileResponse(frontend_build / "index.html")
