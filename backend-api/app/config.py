"""Centralized configuration for the 8mb.local backend.

All environment variables are declared here with their defaults.  Other
modules should ``from .config import settings`` instead of calling
``os.getenv()`` directly.

**Constraint:** Default values are identical to the historical defaults
scattered across main.py / settings_manager.py / cleanup.py.  No
environment variable has been renamed.
"""
from __future__ import annotations

import logging
import os

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Core ---
    REDIS_URL: str = Field(default="redis://127.0.0.1:6379/0")
    BACKEND_HOST: str = Field(default="0.0.0.0")
    BACKEND_PORT: int = Field(default=8001)

    # --- Authentication ---
    AUTH_ENABLED: bool = Field(default=True)
    AUTH_USER: str = Field(default="admin")
    AUTH_PASS: str = Field(default="changeme")

    # --- File management ---
    FILE_RETENTION_HOURS: int = Field(default=1)
    MAX_UPLOAD_SIZE_MB: int = Field(default=51200)
    MAX_BATCH_FILES: int = Field(default=200)
    BATCH_METADATA_TTL_HOURS: int = Field(default=24)

    # --- Worker ---
    WORKER_CONCURRENCY: int = Field(default=4)

    # --- History ---
    HISTORY_ENABLED: bool = Field(default=True)

    # --- Version (baked at build time) ---
    APP_VERSION: str = Field(default="137")

    # --- Logging ---
    LOG_LEVEL: str = Field(default="INFO")

    # --- Frontend ---
    PUBLIC_BACKEND_URL: str = Field(default="")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def configure_logging() -> None:
    """Configure the root logger from the ``LOG_LEVEL`` environment variable.

    Call this once at application startup (before importing route modules) so
    that all ``logging.getLogger(__name__)`` calls use the desired level.

    Recognised environment variables:
      * ``LOG_LEVEL``         — root level (default ``INFO``).
      * ``LOG_LEVEL_APP``     — override for the ``app.*`` / ``worker.*``
                                namespace; use ``DEBUG`` to get per-request,
                                per-job, and per-ffmpeg tracing without
                                flooding noisy 3rd-party loggers.
      * ``LOG_LEVEL_UVICORN`` — override for ``uvicorn.access`` (access log).
    """
    level_name = settings.LOG_LEVEL.upper()
    numeric_level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        # %(name)s includes the dotted logger path (e.g. app.routers.system)
        # which makes grepping per-subsystem trivial.
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )

    app_level = os.getenv("LOG_LEVEL_APP", level_name).upper()
    app_numeric = getattr(logging, app_level, numeric_level)
    for ns in ("app", "worker"):
        logging.getLogger(ns).setLevel(app_numeric)

    uvicorn_level = os.getenv("LOG_LEVEL_UVICORN", "WARNING").upper()
    uvicorn_numeric = getattr(logging, uvicorn_level, logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(uvicorn_numeric)

    # Very chatty 3rd-party loggers — quiet unless operator opts in.
    for noisy in ("urllib3", "asyncio", "celery.redirected"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "logging configured: root=%s app=%s uvicorn.access=%s",
        level_name, app_level, uvicorn_level,
    )
