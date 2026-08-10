"""Regression coverage for SSE reconnect state replay."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.routers import stream


class _Result:
    state = "SUCCESS"
    info = {}
    result = {"output_path": "outputs/task.mp4", "final_size_mb": 1.0}


class TestStreamReplay(unittest.TestCase):
    def test_completed_task_replays_terminal_done_event(self):
        with patch.object(stream.celery_app, "AsyncResult", return_value=_Result()):
            event = stream._terminal_or_progress_event("task-1")
        self.assertEqual(event["type"], "done")
        self.assertEqual(event["task_id"], "task-1")
        self.assertEqual(event["stats"]["output_path"], "outputs/task.mp4")


if __name__ == "__main__":
    unittest.main()
