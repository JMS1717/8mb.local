import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.app.tasks import _cleanup_transient_input_after_task, cleanup_transient_input


class TransientCleanupTests(unittest.TestCase):
    def test_successful_task_removes_transient_input_after_return(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")

            @_cleanup_transient_input_after_task
            def task(_self, _job_id, input_path, transient_input=False):
                self.assertTrue(Path(input_path).exists())
                return "success"

            with patch.dict(os.environ, {"MEDIA_STORAGE": "auto"}, clear=False):
                self.assertEqual(task(None, "job", str(source), transient_input=True), "success")
            self.assertFalse(source.exists())

    def test_failed_task_and_retry_exhaustion_remove_transient_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")

            @_cleanup_transient_input_after_task
            def task(_self, _job_id, input_path, transient_input=False):
                self.assertTrue(Path(input_path).exists())
                raise RuntimeError("encoder failed after fallback retries")

            with patch.dict(os.environ, {"MEDIA_STORAGE": "memory"}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "fallback retries"):
                    task(None, "job", str(source), transient_input=True)
            self.assertFalse(source.exists())

    def test_disk_mode_also_removes_api_staged_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")
            with patch.dict(os.environ, {"MEDIA_STORAGE": "disk"}, clear=False):
                cleanup_transient_input(source, transient_input=True)
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
