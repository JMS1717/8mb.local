"""Cancellation must leave worker cleanup code in control of FFmpeg."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

os.environ["AUTH_ENABLED"] = "false"

from app.routers import compress


class _FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    async def publish(self, *_args, **_kwargs):
        return 1


class TestCooperativeCancellation(unittest.TestCase):
    def test_single_job_cancel_uses_non_terminating_revoke(self):
        fake_redis = _FakeRedis()
        calls = []
        with (
            patch.object(compress, "redis", fake_redis),
            patch.object(
                compress.celery_app.control,
                "revoke",
                side_effect=lambda task_id, **kwargs: calls.append((task_id, kwargs)),
            ),
        ):
            result = asyncio.run(compress.cancel_job("task-123"))

        self.assertEqual(result["status"], "cancellation_requested")
        self.assertEqual(fake_redis.values["cancel:task-123"], "1")
        self.assertEqual(calls, [("task-123", {"terminate": False})])


if __name__ == "__main__":
    unittest.main()
