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


if __name__ == "__main__":
    unittest.main()
