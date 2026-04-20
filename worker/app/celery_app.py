from __future__ import annotations

import logging
import os
from celery import Celery
from celery.signals import after_setup_logger, task_prerun, task_postrun, task_failure
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

celery_app = Celery(
    "8mblocal",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["worker.worker"],  # Ensure task module is imported so tasks register
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
)


# ---------------------------------------------------------------------------
# Logging — honor LOG_LEVEL_APP/LOG_LEVEL so we match the API's verbosity
# ---------------------------------------------------------------------------
@after_setup_logger.connect
def _configure_worker_logging(logger, *args, **kwargs):
    """Raise the app.* / worker.* namespace to DEBUG when the env requests it.

    Celery's default setup hides our own loggers unless we re-apply the level
    after it has set up its handlers.
    """
    level_name = (os.getenv("LOG_LEVEL_APP") or os.getenv("LOG_LEVEL") or "INFO").upper()
    numeric = getattr(logging, level_name, logging.INFO)
    for ns in ("app", "worker"):
        logging.getLogger(ns).setLevel(numeric)
    logger.info("worker logging configured: app/worker -> %s", level_name)


@task_prerun.connect
def _task_prerun_log(task_id=None, task=None, *args, **kwargs):
    logging.getLogger("worker.celery").info(
        "task START id=%s name=%s", task_id, getattr(task, "name", "?"),
    )


@task_postrun.connect
def _task_postrun_log(task_id=None, task=None, state=None, retval=None, *args, **kwargs):
    logging.getLogger("worker.celery").info(
        "task END   id=%s name=%s state=%s", task_id, getattr(task, "name", "?"), state,
    )


@task_failure.connect
def _task_failure_log(task_id=None, exception=None, traceback=None, *args, **kwargs):
    logging.getLogger("worker.celery").error(
        "task FAIL  id=%s exception=%r", task_id, exception,
    )
