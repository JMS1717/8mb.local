"""Regression tests for hardware-aware default profile selection."""
from __future__ import annotations

import copy
import unittest

from app.deps import _ensure_default_preset_matches_hardware


class _SettingsStub:
    def __init__(self, data: dict):
        self.data = copy.deepcopy(data)
        self.writes = 0

    def _read_settings(self) -> dict:
        return copy.deepcopy(self.data)

    def _write_settings(self, data: dict) -> None:
        self.data = copy.deepcopy(data)
        self.writes += 1


def _stock_settings(*, managed: bool = True) -> dict:
    return {
        "default_preset": "AV1 9.7MB (SVT-AV1, CPU)",
        "default_preset_managed": managed,
        "preset_profiles": [
            {
                "name": "AV1 9.7MB (NVENC)",
                "target_mb": 9.7,
                "video_codec": "av1_nvenc",
                "audio_codec": "libopus",
                "preset": "p6",
                "audio_kbps": 128,
                "container": "mp4",
                "tune": "hq",
            },
            {
                "name": "AV1 9.7MB (SVT-AV1, CPU)",
                "target_mb": 9.7,
                "video_codec": "libsvtav1",
                "audio_codec": "libopus",
                "preset": "p6",
                "audio_kbps": 128,
                "container": "mkv",
                "tune": "hq",
            },
        ],
    }


class TestHardwareDefaultPreset(unittest.TestCase):
    def test_qsv_av1_creates_managed_hardware_profile_instead_of_cpu_fallback(self):
        settings = _SettingsStub(_stock_settings())

        _ensure_default_preset_matches_hardware(
            settings,
            {"av1_qsv": True, "h264_qsv": True, "libsvtav1": True},
        )

        self.assertEqual(settings.data["default_preset"], "AV1 9.7MB (Intel Quick Sync)")
        selected = next(
            profile for profile in settings.data["preset_profiles"]
            if profile["name"] == settings.data["default_preset"]
        )
        self.assertEqual(selected["video_codec"], "av1_qsv")
        self.assertTrue(selected["_auto_hardware_profile"])

    def test_codec_family_priority_prefers_av1_qsv_over_h264_nvenc(self):
        settings = _SettingsStub(_stock_settings())

        _ensure_default_preset_matches_hardware(
            settings,
            {"av1_qsv": True, "h264_nvenc": True, "libsvtav1": True},
        )

        selected = next(
            profile for profile in settings.data["preset_profiles"]
            if profile["name"] == settings.data["default_preset"]
        )
        self.assertEqual(selected["video_codec"], "av1_qsv")

    def test_explicit_user_default_is_not_overridden_while_available(self):
        settings = _SettingsStub(_stock_settings(managed=False))

        _ensure_default_preset_matches_hardware(
            settings,
            {"av1_qsv": True, "libsvtav1": True},
        )

        self.assertEqual(settings.writes, 0)
        self.assertEqual(settings.data["default_preset"], "AV1 9.7MB (SVT-AV1, CPU)")


if __name__ == "__main__":
    unittest.main()
