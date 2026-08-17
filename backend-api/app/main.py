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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import configure_logging, settings
from .cleanup import start_scheduler
from .auth import basic_auth
from .deps import (
    UPLOADS_DIR,
    OUTPUTS_DIR,
    redis,
    sync_codec_settings_from_tests,
)
from . import settings_manager
from .folder_watch import folder_watch_service

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


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Per-request structured logging.

    At DEBUG level emits method/path/query on entry and status/duration on
    exit; at INFO level emits a single combined line on exit so production
    logs stay compact. Unhandled exceptions are logged with full traceback
    before being re-raised so FastAPI can still translate them to 500s.
    """
    req_logger = logging.getLogger("app.request")
    start = time.perf_counter()
    rid = uuid.uuid4().hex[:8]
    request.state.request_id = rid

    try:
        if req_logger.isEnabledFor(logging.DEBUG):
            req_logger.debug(
                "-> rid=%s %s %s%s",
                rid, request.method, request.url.path,
                f"?{request.url.query}" if request.url.query else "",
            )
        response = await call_next(request)
    except Exception:
        dur_ms = (time.perf_counter() - start) * 1000
        req_logger.exception(
            "!! rid=%s %s %s raised after %.1fms",
            rid, request.method, request.url.path, dur_ms,
        )
        raise

    dur_ms = (time.perf_counter() - start) * 1000
    # Skip noisy high-frequency polls on INFO; keep them on DEBUG.
    noisy_prefixes = ("/healthz", "/api/stream/", "/api/queue/status")
    if request.url.path.startswith(noisy_prefixes):
        req_logger.debug(
            "<- rid=%s %s %s %s %.1fms",
            rid, request.method, request.url.path, response.status_code, dur_ms,
        )
    else:
        req_logger.info(
            "%s %s -> %s in %.1fms [rid=%s]",
            request.method, request.url.path, response.status_code, dur_ms, rid,
        )
    try:
        response.headers["X-Request-ID"] = rid
    except Exception:
        pass
    return response

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
    """Single startup hook — previously split into two handlers that both
    called ``start_scheduler()`` (producing two schedulers running the cleanup
    job on independent 15-minute cycles)."""
    logger.info(
        "startup: app_version=%s redis=%s uploads=%s outputs=%s auth_enabled=%s retention_hours=%s",
        settings.APP_VERSION, settings.REDIS_URL,
        UPLOADS_DIR, OUTPUTS_DIR,
        settings.AUTH_ENABLED, settings.FILE_RETENTION_HOURS,
    )
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        settings_manager.initialize_env_if_missing()
        logger.debug("startup: .env initialization OK")
    except Exception as e:
        logger.warning(f"initialize_env_if_missing failed: {e}")

    # start_scheduler() is idempotent; called once here by design.
    start_scheduler()
    logger.debug("startup: cleanup scheduler started")
    await folder_watch_service.start()

    try:
        boot_id = str(uuid.uuid4())
        try:
            await redis.set("startup:boot_id", boot_id)
            await redis.set("startup:boot_ts", str(int(time.time())))
            logger.debug("startup: boot_id=%s written to redis", boot_id)
        except Exception as e:
            logger.warning(f"Failed to set boot_id in Redis: {e}")
        asyncio.create_task(sync_codec_settings_from_tests())
        logger.debug("startup: codec-sync background task scheduled")
    except Exception as e:
        logger.warning(f"Startup initialization failed: {e}")

    logger.info("startup: complete")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("shutdown: FastAPI stopping")
    await folder_watch_service.stop()
    # The Docker API has a normal Celery client and no local executor. The
    # desktop runtime exposes shutdown() so closing the native window also
    # cancels active FFmpeg work and joins its worker threads.
    from .celery_app import celery_app

    shutdown = getattr(celery_app, "shutdown", None)
    if callable(shutdown):
        await asyncio.to_thread(shutdown)
        logger.info("shutdown: local worker stopped")


# ---------------------------------------------------------------------------
# Serve pre-built frontend (unified container deployment)
# ---------------------------------------------------------------------------
frontend_build = Path(settings.FRONTEND_BUILD_DIR)


def _safe_frontend_path(full_path: str) -> Path | None:
    """Return a frontend file only when it remains under the build root."""
    try:
        root = frontend_build.resolve()
        candidate = (root / full_path).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate if candidate.is_file() else None


if frontend_build.exists():
    app.mount("/_app", StaticFiles(directory=frontend_build / "_app"), name="static-assets")

    # Paths that must never be swallowed by the SPA fallback. Any unmatched
    # URL starting with these prefixes should yield a real 404 from FastAPI so
    # bad client calls surface in logs/monitoring instead of returning a
    # 200-OK index.html that looks like success to callers.
    _SPA_FALLTHROUGH_EXCLUDES = (
        "api/", "healthz", "download/", "progress/",
        "stream/", "outputs/", "uploads/",
    )

    @app.get("/{full_path:path}", dependencies=[Depends(basic_auth)])
    async def serve_spa(full_path: str):
        """Serve SPA - return index.html for all non-API routes.

        Guards against the catch-all masking legitimate 404s on API endpoints
        (e.g. a typo in ``/api/setings/codecs`` would previously respond with
        ``index.html`` + 200 OK, leading to confusing "HTML returned from a
        JSON endpoint" errors in the browser).
        """
        logger.debug("serve_spa: path=%r", full_path)
        if full_path.startswith(_SPA_FALLTHROUGH_EXCLUDES):
            logger.debug("serve_spa: refusing to mask non-SPA path %r -> 404", full_path)
            raise HTTPException(status_code=404, detail="Not Found")

        file_path = _safe_frontend_path(full_path)
        if file_path is not None:
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
