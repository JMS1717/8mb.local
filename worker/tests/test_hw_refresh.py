"""Regression tests for forced hardware rediscovery and cache replacement."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import worker.app.hw_detect as hw_detect
from worker.app.tasks import (
    encoder_test_cache_snapshot,
    replace_encoder_test_cache,
)


class TestHardwareRefresh(unittest.TestCase):
    def tearDown(self):
        hw_detect.invalidate_hw_cache()
        replace_encoder_test_cache({})

    def test_force_refresh_replaces_the_worker_snapshot(self):
        first = {"type": "cpu", "probe_generation": "old"}
        second = {"type": "intel_qsv", "probe_generation": "new"}
        with patch.object(hw_detect, "detect_hw_accel", side_effect=[first, second]):
            self.assertIs(hw_detect.get_hw_info(), first)
            self.assertIs(hw_detect.get_hw_info(), first)
            self.assertIs(hw_detect.get_hw_info(force_refresh=True), second)
            self.assertEqual(hw_detect.get_hw_info()["probe_generation"], "new")

    def test_encoder_cache_replacement_removes_stale_results(self):
        replace_encoder_test_cache({"h264_qsv:old": True, "hevc_qsv:old": True})
        replace_encoder_test_cache({"h264_qsv:new": False})
        self.assertEqual(encoder_test_cache_snapshot(), {"h264_qsv:new": False})


if __name__ == "__main__":
    unittest.main()
