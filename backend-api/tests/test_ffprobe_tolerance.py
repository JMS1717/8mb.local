"""Regression coverage for valid media with missing ffprobe bitrate fields."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["AUTH_ENABLED"] = "false"

from app.deps import ffprobe


class TestBackendFfprobeTolerance(unittest.TestCase):
    def test_na_bitrates_do_not_reject_valid_video(self):
        payload = {
            "format": {"duration": "2.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "bit_rate": "N/A",
                    "width": "640",
                    "height": "360",
                    "avg_frame_rate": "30/1",
                },
                {"codec_type": "audio", "bit_rate": "N/A"},
            ],
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch("app.deps.subprocess.run", return_value=result):
            info = ffprobe(Path("valid-but-vbr.mp4"))

        self.assertEqual(info["duration"], 2.0)
        self.assertEqual(info["width"], 640)
        self.assertIsNone(info["video_bitrate_kbps"])
        self.assertIsNone(info["audio_bitrate_kbps"])

    def test_audio_only_media_is_accepted_for_extraction(self):
        payload = {
            "format": {"duration": "2.0"},
            "streams": [{"codec_type": "audio", "codec_name": "aac", "bit_rate": "96000"}],
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch("app.deps.subprocess.run", return_value=result):
            info = ffprobe(Path("audio-only.m4a"))

        self.assertEqual(info["duration"], 2.0)
        self.assertFalse(info["has_video"])
        self.assertTrue(info["has_audio"])
        self.assertIsNone(info["width"])


if __name__ == "__main__":
    unittest.main()
