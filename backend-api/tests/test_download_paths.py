"""Regression coverage for output-path containment checks."""
from __future__ import annotations

import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ["AUTH_ENABLED"] = "false"

from app.routers.download import _media_type_for_path, _safe_output_path, _write_batch_zip, job_status


class _CompletedResult:
    state = "SUCCESS"
    info = {
        "progress": 100.0,
        "detail": "done",
        "encoder": "hevc_qsv",
        "requested_encoder": "hevc_qsv",
        "resolved_encoder": "hevc_qsv",
        "actual_encoder": "hevc_qsv",
        "hardware_used": True,
        "hardware_device": "/dev/dri/renderD128",
        "fallback_occurred": False,
        "fallback_reason": None,
    }


class _UndecodableCanceledResult:
    @property
    def state(self):
        raise ValueError("Exception information must include the exception type")


class _RunningResult:
    state = "PROGRESS"
    info = {"progress": 42.0, "phase": "encoding"}


class TestDownloadPaths(unittest.TestCase):
    def test_job_status_reports_final_encoder(self):
        with patch("app.routers.download.celery_app.AsyncResult", return_value=_CompletedResult()):
            status = asyncio.run(job_status("task-1"))
        self.assertEqual(status.state, "SUCCESS")
        self.assertEqual(status.encoder, "hevc_qsv")
        self.assertEqual(status.requested_encoder, "hevc_qsv")
        self.assertEqual(status.actual_encoder, "hevc_qsv")
        self.assertTrue(status.hardware_used)
        self.assertEqual(status.hardware_device, "/dev/dri/renderD128")

    def test_corrupt_canceled_result_is_reported_as_terminal_canceled(self):
        with patch("app.routers.download.celery_app.AsyncResult", return_value=_UndecodableCanceledResult()), \
             patch("app.routers.download.redis.get", new=AsyncMock(return_value="1")):
            status = asyncio.run(job_status("task-canceled"))
        self.assertEqual(status.state, "CANCELED")
        self.assertEqual(status.phase, None)

    def test_running_status_merges_durable_encoder_telemetry(self):
        durable = {
            "requested_encoder": "hevc_qsv",
            "resolved_encoder": "hevc_qsv",
            "hardware_type": "intel_qsv",
            "hardware_device": "/dev/dri/renderD128",
            "decoder": {"name": "software", "hardware_used": False},
        }
        with patch("app.routers.download.celery_app.AsyncResult", return_value=_RunningResult()), \
             patch("app.routers.download.redis.get", new=AsyncMock(return_value=__import__("orjson").dumps(durable))):
            status = asyncio.run(job_status("task-running"))
        self.assertEqual(status.resolved_encoder, "hevc_qsv")
        self.assertEqual(status.hardware_type, "intel_qsv")
        self.assertEqual(status.hardware_device, "/dev/dri/renderD128")
        self.assertEqual(status.decoder["name"], "software")

    def test_output_media_types_match_containers(self):
        self.assertEqual(_media_type_for_path(Path("output.mp4")), "video/mp4")
        self.assertEqual(_media_type_for_path(Path("audio.m4a")), "audio/mp4")
        self.assertEqual(_media_type_for_path(Path("output.mkv")), "video/x-matroska")

    def test_output_must_be_inside_output_directory(self):
        with tempfile.TemporaryDirectory(prefix="8mb-output-test-") as temp_dir:
            root = Path(temp_dir)
            output = root / "encoded.mp4"
            output.write_bytes(b"ok")
            outside = root.parent / f"{root.name}-outside.mp4"
            outside.write_bytes(b"no")
            try:
                with patch("app.routers.download.OUTPUTS_DIR", root):
                    self.assertEqual(_safe_output_path(output), output.resolve())
                    self.assertIsNone(_safe_output_path(outside))
                    self.assertIsNone(_safe_output_path(root / "missing.mp4"))
            finally:
                outside.unlink(missing_ok=True)

    def test_frontend_fallback_rejects_paths_outside_build_root(self):
        from app import main

        with tempfile.TemporaryDirectory(prefix="8mb-frontend-test-") as temp_dir:
            root = Path(temp_dir) / "frontend"
            root.mkdir()
            (root / "index.html").write_text("ok", encoding="utf-8")
            outside = root.parent / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            try:
                with patch.object(main, "frontend_build", root):
                    self.assertIsNone(main._safe_frontend_path("../secret.txt"))
                    self.assertEqual(main._safe_frontend_path("index.html"), (root / "index.html").resolve())
            finally:
                outside.unlink(missing_ok=True)

    def test_batch_zip_is_published_complete_and_handles_duplicate_names(self):
        import zipfile

        with tempfile.TemporaryDirectory(prefix="8mb-batch-zip-test-") as temp_dir:
            root = Path(temp_dir)
            first = root / "one" / "same.mp4"
            second = root / "two" / "same.mp4"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            temporary = root / ".batch.tmp"
            final = root / "batch.zip"

            _write_batch_zip([first, second], temporary, final)

            self.assertFalse(temporary.exists())
            with zipfile.ZipFile(final) as archive:
                self.assertEqual(set(archive.namelist()), {"same.mp4", "same_2.mp4"})
                self.assertEqual(archive.read("same.mp4"), b"one")
                self.assertEqual(archive.read("same_2.mp4"), b"two")


if __name__ == "__main__":
    unittest.main()
