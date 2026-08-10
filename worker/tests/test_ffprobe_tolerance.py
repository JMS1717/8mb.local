"""Regression coverage for valid media with missing ffprobe bitrate fields."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from worker.app.utils import ffprobe_info


class TestWorkerFfprobeTolerance(unittest.TestCase):
    def test_na_bitrates_do_not_reject_valid_video(self):
        payload = {
            "format": {"duration": "2.0", "tags": {}},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "bit_rate": "N/A",
                    "width": "640",
                    "height": "360",
                    "avg_frame_rate": "30/1",
                    "display_aspect_ratio": "16:9",
                },
                {"codec_type": "audio", "bit_rate": "N/A"},
            ],
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch("worker.app.utils.subprocess.run", return_value=result):
            info = ffprobe_info("valid-but-vbr.mp4")

        self.assertEqual(info["duration"], 2.0)
        self.assertEqual(info["width"], 640)
        self.assertIsNone(info["video_bitrate_kbps"])
        self.assertIsNone(info["audio_bitrate_kbps"])

    def test_audio_only_media_requires_explicit_extraction_mode(self):
        payload = {
            "format": {"duration": "2.0", "tags": {}},
            "streams": [{"codec_type": "audio", "codec_name": "aac", "bit_rate": "96000"}],
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch("worker.app.utils.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "no usable video"):
                ffprobe_info("audio-only.m4a")
            info = ffprobe_info("audio-only.m4a", allow_audio_only=True)

        self.assertFalse(info["has_video"])
        self.assertTrue(info["has_audio"])


if __name__ == "__main__":
    unittest.main()
