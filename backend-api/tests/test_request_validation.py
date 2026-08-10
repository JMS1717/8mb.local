"""Regression coverage for request bounds shared by direct and batch jobs."""
from __future__ import annotations

import os
import unittest

os.environ["AUTH_ENABLED"] = "false"

from pydantic import ValidationError

from app.models import CompressRequest
from app.routers.upload import _validate_batch_options


class TestRequestValidation(unittest.TestCase):
    def test_direct_compress_rejects_non_positive_target(self):
        with self.assertRaises(ValidationError):
            CompressRequest(
                job_id="job",
                filename="input.mp4",
                target_size_mb=0,
                video_codec="libx264",
            )

    def test_direct_compress_accepts_qsv_codec(self):
        request = CompressRequest(
            job_id="job",
            filename="input.mp4",
            target_size_mb=1,
            video_codec="h264_qsv",
        )
        self.assertEqual(request.video_codec, "h264_qsv")

    def test_direct_compress_accepts_windows_amf_codec(self):
        request = CompressRequest(
            job_id="job",
            filename="input.mp4",
            target_size_mb=1,
            video_codec="h264_amf",
        )
        self.assertEqual(request.video_codec, "h264_amf")

    def test_batch_rejects_non_finite_or_negative_options(self):
        with self.assertRaisesRegex(Exception, "target_size_mb"):
            _validate_batch_options("libx264", "aac", "p6", "mp4", "hq", float("nan"), 128, None, None, 240, None, None, None)
        with self.assertRaisesRegex(Exception, "max_width"):
            _validate_batch_options("libx264", "aac", "p6", "mp4", "hq", 1, 128, 0, None, 240, None, None, None)
        with self.assertRaisesRegex(Exception, "target_video_bitrate_kbps"):
            _validate_batch_options("libx264", "aac", "p6", "mp4", "hq", 1, 128, None, None, 240, None, -1, None)

    def test_batch_rejects_unknown_codec(self):
        with self.assertRaisesRegex(Exception, "video_codec"):
            _validate_batch_options("not-an-encoder", "aac", "p6", "mp4", "hq", 1, 128, None, None, 240, None, None, None)


if __name__ == "__main__":
    unittest.main()
