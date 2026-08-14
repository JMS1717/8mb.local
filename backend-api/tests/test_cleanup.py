"""Retention cleanup removes stale media and matching history metadata."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import cleanup, history_manager, settings_manager


class RetentionCleanupTests(unittest.TestCase):
    def test_expired_output_and_history_entry_are_removed_together(self):
        with tempfile.TemporaryDirectory(prefix="8mb-cleanup-test-") as temp_dir:
            root = Path(temp_dir)
            uploads = root / "uploads"
            outputs = root / "outputs"
            state = root / "state"
            uploads.mkdir()
            outputs.mkdir()
            state.mkdir()
            old_output = outputs / "old_8mblocal_old-task.mp4"
            fresh_output = outputs / "fresh_8mblocal_new-task.mp4"
            old_output.write_bytes(b"old")
            fresh_output.write_bytes(b"fresh")
            old_time = time.time() - 2 * 3600
            fresh_time = time.time()
            os.utime(old_output, (old_time, old_time))
            os.utime(fresh_output, (fresh_time, fresh_time))
            history_file = state / "history.json"
            lock_file = state / ".history.json.lock"
            history_file.write_text(json.dumps([
                {
                    "timestamp": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                    "filename": "old.mp4",
                    "task_id": "old-task",
                    "output_filename": old_output.name,
                },
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "filename": "fresh.mp4",
                    "task_id": "new-task",
                    "output_filename": fresh_output.name,
                },
            ]), encoding="utf-8")

            with (
                patch.object(cleanup, "UPLOADS_DIR", uploads),
                patch.object(cleanup, "OUTPUTS_DIR", outputs),
                patch.object(settings_manager, "get_retention_hours", return_value=1),
                patch.object(history_manager, "HISTORY_FILE", history_file),
                patch.object(history_manager, "HISTORY_LOCK_FILE", lock_file),
            ):
                cleanup._cleanup_files_sync()
                remaining = history_manager.get_history()

            self.assertFalse(old_output.exists())
            self.assertTrue(fresh_output.exists())
            self.assertEqual([entry["task_id"] for entry in remaining], ["new-task"])


if __name__ == "__main__":
    unittest.main()
