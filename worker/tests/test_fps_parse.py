"""Tests for frame-rate parsing from ffprobe-style strings."""
import unittest

from worker.app.progress import parse_ffmpeg_out_time
from worker.app.utils import parse_fps_fraction


class TestFpsParse(unittest.TestCase):
    def test_slash_fractions(self):
        self.assertAlmostEqual(parse_fps_fraction("60/1"), 60.0)
        self.assertAlmostEqual(parse_fps_fraction("30000/1001"), 30000 / 1001)

    def test_invalid(self):
        self.assertIsNone(parse_fps_fraction(None))
        self.assertIsNone(parse_fps_fraction("0/0"))
        self.assertIsNone(parse_fps_fraction("N/A"))

    def test_ffmpeg_progress_time_is_microseconds(self):
        self.assertEqual(parse_ffmpeg_out_time("1000000"), 1.0)
        self.assertEqual(parse_ffmpeg_out_time("700000"), 0.7)
        self.assertIsNone(parse_ffmpeg_out_time("N/A"))


if __name__ == "__main__":
    unittest.main()
