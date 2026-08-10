"""Regression tests for hardware codec visibility."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["AUTH_ENABLED"] = "false"

from app.routers import system
from app.routers.system import _working_hardware_encoders


class TestCodecAvailability(unittest.TestCase):
    def test_failed_runtime_probe_is_not_reported_as_available(self):
        working = _working_hardware_encoders({
            "available_encoders": {"h264": "h264_nvenc"},
            "tested_encoders": {"h264_nvenc": False},
        })
        self.assertEqual(working, set())

    def test_passed_runtime_probe_is_reported(self):
        working = _working_hardware_encoders({
            "available_encoders": {"h264": "h264_nvenc"},
            "tested_encoders": {"h264_nvenc": True, "hevc_qsv": False},
        })
        self.assertEqual(working, {"h264_nvenc"})

    def test_legacy_worker_uses_detected_encoder_map(self):
        working = _working_hardware_encoders({
            "available_encoders": {"h264": "h264_vaapi", "av1": "libsvtav1"},
        })
        self.assertEqual(working, {"h264_vaapi"})

    def test_windows_amf_is_exposed_when_probe_passes(self):
        with patch.object(
            system,
            "get_hw_info_cached_async",
            new=AsyncMock(return_value={
                "type": "amd_amf",
                "available_encoders": {"h264": "h264_amf"},
                "tested_encoders": {"h264_amf": True},
            }),
        ), patch.object(
            system.settings_manager,
            "get_codec_visibility_settings",
            return_value={"h264_amf": True},
        ):
            response = asyncio.run(system.get_available_codecs())

        self.assertIn("h264_amf", response.enabled_codecs)

    def test_cpu_options_follow_the_worker_ffmpeg_listing(self):
        with patch.object(
            system,
            "get_hw_info_cached_async",
            new=AsyncMock(return_value={
                "type": "cpu",
                "available_encoders": {"h264": "libx264"},
                "available_cpu_encoders": ["libx264", "libx265"],
            }),
        ), patch.object(
            system.settings_manager,
            "get_codec_visibility_settings",
            return_value={"libx264": True, "libx265": True, "libsvtav1": True, "libaom_av1": True},
        ):
            response = asyncio.run(system.get_available_codecs())

        self.assertIn("libx264", response.enabled_codecs)
        self.assertIn("libx265", response.enabled_codecs)
        self.assertNotIn("libsvtav1", response.enabled_codecs)
        self.assertNotIn("libaom-av1", response.enabled_codecs)

    def test_missing_cpu_listing_uses_conservative_baseline(self):
        self.assertEqual(
            system._working_cpu_encoders({"available_encoders": {}}),
            {"libx264", "libx265"},
        )


if __name__ == "__main__":
    unittest.main()
