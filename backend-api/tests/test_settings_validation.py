import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings_manager


class TestSettingsValidation(unittest.TestCase):
    def test_size_buttons_reject_empty_non_finite_and_out_of_range_values(self):
        with self.assertRaises(ValueError):
            settings_manager.update_size_buttons([])
        with self.assertRaises(ValueError):
            settings_manager.update_size_buttons([math.nan])
        with self.assertRaises(ValueError):
            settings_manager.update_size_buttons([-1, 4])
        with self.assertRaises(ValueError):
            settings_manager.update_size_buttons([51200.01])

    def test_settings_write_is_atomic_and_invalid_json_resets_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                path.write_text("[]", encoding="utf-8")
                self.assertEqual(settings_manager._read_settings(), {})
                settings_manager.update_size_buttons([8, 4, 8])
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["size_buttons"], [4.0, 8.0])
                self.assertFalse(list(path.parent.glob(".settings.json.*.tmp")))

    def test_explicit_default_preset_disables_hardware_auto_management(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                data = settings_manager._ensure_defaults()
                selected = data["preset_profiles"][-1]["name"]
                settings_manager.set_default_preset(selected)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["default_preset"], selected)
                self.assertFalse(saved["default_preset_managed"])

    def test_legacy_custom_default_is_not_marked_as_managed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "default_preset": "My CPU Choice",
                "preset_profiles": [{
                    "name": "My CPU Choice",
                    "video_codec": "libsvtav1",
                }],
            }), encoding="utf-8")
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                data = settings_manager._ensure_defaults()
            self.assertFalse(data["default_preset_managed"])

    def test_legacy_stock_default_remains_hardware_managed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "default_preset": "AV1 9.7MB (SVT-AV1, CPU)",
                "preset_profiles": [{
                    "name": "AV1 9.7MB (SVT-AV1, CPU)",
                    "video_codec": "libsvtav1",
                }],
            }), encoding="utf-8")
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                data = settings_manager._ensure_defaults()
            self.assertTrue(data["default_preset_managed"])

    def test_codec_visibility_round_trips_without_being_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                settings_manager.update_codec_visibility_settings({
                    "h264_nvenc": False,
                    "hevc_nvenc": True,
                    "av1_nvenc": False,
                    "libx264": False,
                    "libx265": True,
                    "libsvtav1": True,
                })
                saved = settings_manager.get_codec_visibility_settings()

            self.assertFalse(saved["h264_nvenc"])
            self.assertTrue(saved["hevc_nvenc"])
            self.assertFalse(saved["av1_nvenc"])
            self.assertFalse(saved["libx264"])
            self.assertTrue(saved["libx265"])
            self.assertTrue(saved["libsvtav1"])

    def test_codec_visibility_requires_a_cpu_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                with self.assertRaisesRegex(ValueError, "CPU codec"):
                    settings_manager.update_codec_visibility_settings({
                        "libx264": False,
                        "libx265": False,
                        "libsvtav1": False,
                    })

    def test_corrupt_codec_visibility_recovers_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"codec_visibility": ["not", "a", "mapping"]}), encoding="utf-8")
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                visible = settings_manager.get_codec_visibility_settings()

        self.assertTrue(visible["libx264"])
        self.assertTrue(visible["libx265"])
        self.assertTrue(visible["libsvtav1"])

    def test_corrupt_codec_values_are_coerced_and_cpu_fallback_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"codec_visibility": {
                "libx264": "false",
                "libx265": "0",
                "libsvtav1": False,
                "h264_nvenc": "true",
            }}), encoding="utf-8")
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                visible = settings_manager.get_codec_visibility_settings()

        self.assertTrue(visible["libx264"])
        self.assertFalse(visible["libx265"])
        self.assertFalse(visible["libsvtav1"])
        self.assertTrue(visible["h264_nvenc"])

    def test_saving_defaults_preserves_profile_frame_rate_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                data = settings_manager._ensure_defaults()
                profile = dict(data["preset_profiles"][0])
                profile["name"] = "Capped default"
                profile["max_output_fps"] = 30.0
                data["preset_profiles"] = [profile]
                data["default_preset"] = profile["name"]
                settings_manager._write_settings(data)

                settings_manager.update_default_presets(
                    target_mb=12,
                    video_codec=profile["video_codec"],
                    audio_codec=profile["audio_codec"],
                    preset=profile["preset"],
                    audio_kbps=profile["audio_kbps"],
                    container=profile["container"],
                    tune=profile["tune"],
                )
                saved = settings_manager._read_settings()

            self.assertEqual(saved["preset_profiles"][0]["max_output_fps"], 30.0)

    def test_deleting_profiles_keeps_default_selection_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(settings_manager, "SETTINGS_FILE", path):
                data = settings_manager._ensure_defaults()
                first, second = data["preset_profiles"][:2]
                data["preset_profiles"] = [first, second]
                data["default_preset"] = first["name"]
                settings_manager._write_settings(data)

                settings_manager.delete_preset_profile(second["name"])
                self.assertEqual(settings_manager._read_settings()["default_preset"], first["name"])

                settings_manager.add_preset_profile(second)
                settings_manager.delete_preset_profile(first["name"])
                self.assertEqual(settings_manager._read_settings()["default_preset"], second["name"])

                settings_manager.delete_preset_profile(second["name"])
                saved = settings_manager._read_settings()

            self.assertEqual(saved["preset_profiles"], [])
            self.assertIsNone(saved["default_preset"])


if __name__ == "__main__":
    unittest.main()
