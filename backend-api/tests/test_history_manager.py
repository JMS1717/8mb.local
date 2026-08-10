"""Regression coverage for durable, concurrent history writes."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import history_manager


class TestHistoryManager(unittest.TestCase):
    def test_concurrent_adds_remain_valid_and_unique(self):
        with tempfile.TemporaryDirectory(prefix="8mb-history-test-") as temp_dir:
            history_file = Path(temp_dir) / "history.json"
            lock_file = Path(temp_dir) / ".history.json.lock"
            errors: list[Exception] = []

            with patch.object(history_manager, "HISTORY_FILE", history_file), patch.object(
                history_manager, "HISTORY_LOCK_FILE", lock_file
            ):
                history_manager.clear_history()

                def add_entry(index: int) -> None:
                    try:
                        history_manager.add_history_entry(
                            filename=f"file {index}.mp4",
                            original_size_mb=1,
                            compressed_size_mb=0.5,
                            video_codec="libx264",
                            audio_codec="aac",
                            target_mb=1,
                            preset="p6",
                            duration=1,
                            task_id=f"task-{index}",
                            output_filename=f"output-{index}.mp4",
                        )
                    except Exception as exc:  # pragma: no cover - assertion below reports it
                        errors.append(exc)

                threads = [threading.Thread(target=add_entry, args=(i,)) for i in range(16)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                entries = history_manager.get_history()
                self.assertEqual(errors, [])
                self.assertEqual(len(entries), 16)
                self.assertEqual(
                    {entry["task_id"] for entry in entries},
                    {f"task-{i}" for i in range(16)},
                )
                self.assertTrue(history_file.read_text(encoding="utf-8").lstrip().startswith("["))


if __name__ == "__main__":
    unittest.main()
