import os
from celery import Celery
from .config import settings

REDIS_URL = settings.REDIS_URL

celery_app = Celery(
    "8mblocal",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    worker_send_task_events=True,
    task_send_sent_event=True,
)
