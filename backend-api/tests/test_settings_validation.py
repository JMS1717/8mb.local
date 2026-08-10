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


if __name__ == "__main__":
    unittest.main()
