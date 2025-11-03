import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis-broker:6379/0")

celery_app = Celery(
    "smartdrop",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.worker"],  # Ensure task module is imported so tasks register
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
)
