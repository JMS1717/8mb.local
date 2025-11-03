from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    redis_url: str = Field(
        default="redis://redis-broker:6379/0",
        description="Redis connection string used for Celery broker and metadata storage.",
    )
    celery_broker_url: Optional[str] = Field(
        default=None,
        description="Override for Celery broker URL. Defaults to redis_url when unset.",
    )
    celery_result_backend: Optional[str] = Field(
        default=None,
        description="Override for Celery result backend. Defaults to redis_url when unset.",
    )
    uploads_dir: Path = Field(
        default=Path(os.getenv("EIGHTMBLOCAL_UPLOADS_DIR", "/app/uploads")),
        description="Directory where uploaded source files are stored.",
    )
    outputs_dir: Path = Field(
        default=Path(os.getenv("EIGHTMBLOCAL_OUTPUTS_DIR", "/app/outputs")),
        description="Directory where transcoded files are written.",
    )
    cleanup_interval_seconds: int = Field(
        default=600,
        description="Frequency in seconds for scheduled filesystem cleanup.",
    )
    file_ttl_seconds: int = Field(
        default=3600,
        description="Time-to-live in seconds for uploads and outputs before cleanup.",
    )
    basic_auth_username: Optional[str] = Field(
        default=None,
        description="Username required for HTTP Basic auth. Disabled when unset.",
    )
    basic_auth_password: Optional[str] = Field(
        default=None,
        description="Password required for HTTP Basic auth. Disabled when unset.",
    )

    class Config:
        env_prefix = "EIGHTMBLOCAL_"
        env_file = os.getenv("EIGHTMBLOCAL_ENV_FILE")
        case_sensitive = False

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache()
def get_settings() -> Settings:
    return Settings()
