from __future__ import annotations

import unittest

from worker.app.tasks import mp4_video_tag_args


class TestMp4MobileCompatibility(unittest.TestCase):
    def test_hevc_mp4_uses_apple_compatible_hvc1_tag(self):
        self.assertEqual(mp4_video_tag_args("output.mp4", "hevc_nvenc"), ["-tag:v", "hvc1"])
        self.assertEqual(mp4_video_tag_args("output.mp4", "libx265"), ["-tag:v", "hvc1"])

    def test_non_hevc_or_non_mp4_keeps_default_tag(self):
        self.assertEqual(mp4_video_tag_args("output.mp4", "h264_nvenc"), [])
        self.assertEqual(mp4_video_tag_args("output.mkv", "hevc_nvenc"), [])


if __name__ == "__main__":
    unittest.main()
