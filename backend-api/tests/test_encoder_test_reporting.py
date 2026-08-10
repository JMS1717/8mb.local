"""Regression tests for truthful encoder diagnostics in local mode."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["AUTH_ENABLED"] = "false"

from app.routers import system


class TestEncoderTestReporting(unittest.TestCase):
    def test_local_probe_results_are_used_when_redis_cache_is_empty(self):
        hw_info = {
            "type": "nvidia",
            "available_encoders": {
                "h264": "h264_nvenc",
                "hevc": "hevc_nvenc",
                "av1": "av1_nvenc",
            },
            "tested_encoders": {
                "h264_nvenc": True,
                "hevc_nvenc": True,
                "av1_nvenc": True,
                "h264_qsv": True,
                "hevc_qsv": True,
                "av1_qsv": True,
                "h264_amf": False,
            },
            "available_cpu_encoders": ["libx264", "libx265"],
        }
        with patch.object(
            system,
            "get_hw_info_cached_async",
            new=AsyncMock(return_value=hw_info),
        ), patch.object(system.redis, "get", new=AsyncMock(return_value=None)):
            response = asyncio.run(system.system_encoder_tests())

        results = {item["codec"]: item for item in response["results"]}
        self.assertTrue(response["any_hardware_passed"])
        self.assertTrue(results["av1_nvenc"]["passed"])
        self.assertEqual(results["av1_nvenc"]["encode_message"], "OK (runtime probe)")
        self.assertTrue(results["h264_qsv"]["passed"])
        self.assertFalse(results["h264_amf"]["passed"])
        self.assertIsNone(results["h264_vaapi"]["passed"])
        self.assertTrue(results["libx264"]["passed"])
        self.assertIsNone(results["libsvtav1"]["passed"])

    def test_current_probe_map_overrides_stale_hardware_redis_result(self):
        hw_info = {
            "type": "nvidia",
            "available_encoders": {"h264": "h264_nvenc"},
            "tested_encoders": {"h264_nvenc": False},
            "available_cpu_encoders": ["libx264"],
        }

        async def stale_get(key):
            if key == "encoder_test_json:h264_nvenc":
                return json.dumps({"passed": True, "message": "stale pass"})
            if key == "encoder_test_decode_json:h264_nvenc":
                return json.dumps({"passed": True, "message": "stale decode"})
            return None

        with patch.object(
            system,
            "get_hw_info_cached_async",
            new=AsyncMock(return_value=hw_info),
        ), patch.object(system.redis, "get", side_effect=stale_get):
            response = asyncio.run(system.system_encoder_tests())

        result = next(item for item in response["results"] if item["codec"] == "h264_nvenc")
        self.assertFalse(result["passed"])
        self.assertEqual(result["encode_message"], "Hardware initialization failed")


if __name__ == "__main__":
    unittest.main()
