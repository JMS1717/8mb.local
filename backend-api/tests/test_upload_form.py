"""HTTP-level regression tests for multipart upload handling."""
from __future__ import annotations

import os
import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile

os.environ["AUTH_ENABLED"] = "false"

from fastapi.testclient import TestClient

from app import main
from app import deps
from app.routers import upload


class TestUploadMultipartForm(unittest.TestCase):
    def test_oversized_upload_closes_partial_file_before_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="8mb-upload-size-test-") as temp_dir:
            destination = Path(temp_dir) / "partial.mp4"
            upload_file = UploadFile(file=io.BytesIO(b"01234567890"), filename="partial.mp4")
            with patch.object(deps, "MAX_UPLOAD_SIZE_BYTES", 10):
                with self.assertRaisesRegex(Exception, "File too large") as raised:
                    asyncio.run(deps.save_upload_file(upload_file, destination))
            self.assertEqual(raised.exception.status_code, 413)
            self.assertFalse(destination.exists())

    def test_target_and_audio_values_are_read_from_multipart_body(self):
        with tempfile.TemporaryDirectory(prefix="8mb-upload-test-") as temp_dir:
            upload_dir = Path(temp_dir)

            async def fake_save_upload(_file, destination, **_kwargs):
                destination.write_bytes(b"test video placeholder")
                return destination

            probe_info = {
                "duration": 10.0,
                "video_bitrate_kbps": 4000.0,
                "audio_bitrate_kbps": 128.0,
                "width": 1920,
                "height": 1080,
                "video_fps": 30.0,
            }

            with (
                patch.object(upload, "UPLOADS_DIR", upload_dir),
                patch.object(upload, "save_upload_file", new=fake_save_upload),
                patch.object(upload, "ffprobe", return_value=probe_info),
                patch.object(upload, "calc_bitrates", return_value=(1234.0, 1186.0, False)) as calc,
            ):
                response = TestClient(main.app).post(
                    "/api/upload",
                    files={"file": ("input.mp4", b"video", "video/mp4")},
                    data={"target_size_mb": "0.5", "audio_bitrate_kbps": "48"},
                )

            self.assertEqual(response.status_code, 200, response.text)
            calc.assert_called_once_with(0.5, 10.0, 48)
            self.assertEqual(response.json()["estimate_total_kbps"], 1234.0)


if __name__ == "__main__":
    unittest.main()
