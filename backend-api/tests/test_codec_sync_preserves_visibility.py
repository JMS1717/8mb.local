"""Regression coverage for hardware probing and saved codec visibility."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import deps, settings_manager


class _RedisStub:
    set = AsyncMock()


class TestCodecSyncPreservesVisibility(unittest.TestCase):
    def test_startup_probe_does_not_overwrite_saved_codec_choices(self):
        hardware_info = {
            "available_encoders": {
                "h264": "h264_nvenc",
                "hevc": "hevc_nvenc",
            },
            "available_cpu_encoders": ["libx264", "libx265", "libsvtav1"],
            "tested_encoders": {
                "h264_nvenc": True,
                "hevc_nvenc": True,
            },
        }
        saved_visibility = {
            "h264_nvenc": False,
            "hevc_nvenc": True,
            "av1_nvenc": True,
            "libx264": True,
            "libx265": False,
            "libsvtav1": True,
        }
        captured = {}

        def capture_default(_settings_manager, visibility):
            captured.update(visibility)

        with patch.object(
            deps,
            "get_hw_info_fresh_async",
            new=AsyncMock(return_value=hardware_info),
        ), patch.object(
            deps,
            "redis",
            _RedisStub(),
        ), patch.object(
            settings_manager,
            "get_codec_visibility_settings",
            return_value=saved_visibility,
        ), patch.object(
            settings_manager,
            "update_codec_visibility_settings",
        ) as update_visibility, patch.object(
            deps,
            "_ensure_default_preset_matches_hardware",
            side_effect=capture_default,
        ):
            asyncio.run(deps.sync_codec_settings_from_tests(timeout_s=5))

        update_visibility.assert_not_called()
        self.assertFalse(captured["h264_nvenc"])
        self.assertTrue(captured["hevc_nvenc"])
        self.assertFalse(captured["av1_nvenc"])
        self.assertTrue(captured["libx264"])
        self.assertFalse(captured["libx265"])
        self.assertTrue(captured["libsvtav1"])


if __name__ == "__main__":
    unittest.main()
