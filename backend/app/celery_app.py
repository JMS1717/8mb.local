from __future__ import annotations

import os

from celery import Celery

from .config import get_settings

settings = get_settings()

celery = Celery(
    "8mblocal",
    broker=settings.broker_url,
    backend=settings.result_backend,
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    broker_url=settings.broker_url,
    result_backend=settings.result_backend,
)


@celery.task(name="8mblocal.healthcheck")
def healthcheck() -> str:
    return os.getenv("HOSTNAME", "worker")
