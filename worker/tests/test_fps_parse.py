"""Tests for frame-rate parsing from ffprobe-style strings."""
import unittest

from worker.app.utils import parse_fps_fraction


class TestFpsParse(unittest.TestCase):
    def test_slash_fractions(self):
        self.assertAlmostEqual(parse_fps_fraction("60/1"), 60.0)
        self.assertAlmostEqual(parse_fps_fraction("30000/1001"), 30000 / 1001)

    def test_invalid(self):
        self.assertIsNone(parse_fps_fraction(None))
        self.assertIsNone(parse_fps_fraction("0/0"))
        self.assertIsNone(parse_fps_fraction("N/A"))


if __name__ == "__main__":
    unittest.main()
