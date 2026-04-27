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
    APP_VERSION: str = Field(default="136")

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
    """
    level_name = settings.LOG_LEVEL.upper()
    numeric_level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )
