"""Worker entry point – initialization and re-exports.

Celery discovers tasks via the module path ``worker.app.worker``.  The actual
task implementations live in :pymod:`worker.app.tasks`; this module re-exports
them so that the registered task names (``worker.worker.compress_video``, etc.)
continue to resolve correctly.
"""
from __future__ import annotations

import logging
import os
import sys
from threading import Thread

from .celery_app import celery_app  # noqa: F401 – needed by Celery autodiscovery
from .hw_detect import get_hw_info
from .startup_tests import run_startup_tests

# Re-export task functions so ``worker.worker.compress_video`` is importable.
from .tasks import (  # noqa: F401
    ENCODER_TEST_CACHE,
    compress_video,
    get_hardware_info_task,
    run_hardware_tests_task,
)

# ---------------------------------------------------------------------------
# Logging (configure before any tests run)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background startup tests
# ---------------------------------------------------------------------------
def _start_encoder_tests_async() -> None:
    def _run() -> None:
        try:
            logger.info("")
            logger.info("*" * 70)
            logger.info("  8MB.LOCAL WORKER INITIALIZATION")
            logger.info("*" * 70)
            logger.info("")
            sys.stdout.flush()
            _hw_info = get_hw_info()
            cache = run_startup_tests(_hw_info)
            ENCODER_TEST_CACHE.update(cache)
            logger.info(f"✓ Encoder cache ready: {len(ENCODER_TEST_CACHE)} encoder(s) validated")
            logger.info("✓ Worker initialization complete")
            logger.info("*" * 70)
            logger.info("")
            sys.stdout.flush()
        except Exception as e:
            logger.warning(f"Startup encoder tests failed (non-fatal): {e}")
            sys.stdout.flush()

    if os.getenv('DISABLE_STARTUP_TESTS', '').lower() in ('1', 'true', 'yes'):
        logger.info("Skipping encoder startup tests (DISABLE_STARTUP_TESTS=1)")
        return
    try:
        Thread(target=_run, daemon=True).start()
    except Exception as e:
        logger.warning(f"Failed to start background encoder tests: {e}")


_start_encoder_tests_async()
