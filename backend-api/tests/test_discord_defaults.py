import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings_manager


class DiscordDefaultsTests(unittest.TestCase):
    def _settings_path(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name) / 'settings.json'

    def test_fresh_defaults_use_discord_headroom(self):
        path = self._settings_path()
        with patch.object(settings_manager, 'SETTINGS_FILE', path):
            data = settings_manager._ensure_defaults()
            self.assertEqual(data['size_buttons'], [4, 5, 8, 9.7, 19.7, 50, 100])
            self.assertEqual(data['default_preset'], 'Discord 19.7 MB')
            self.assertEqual(data['preset_profiles'][0]['target_mb'], 19.7)
            self.assertEqual(data['preset_profiles'][0]['preset'], 'p4')
            self.assertEqual(settings_manager.get_default_presets()['target_mb'], 19.7)
            self.assertEqual(settings_manager.get_default_presets()['preset'], 'p4')
            self.assertTrue(path.exists())

    def test_managed_discord_p6_is_migrated_to_p4(self):
        path = self._settings_path()
        profiles = settings_manager._default_preset_profiles()
        profiles[0]['preset'] = 'p6'
        path.write_text(json.dumps({
            'preset_profiles': profiles,
            'default_preset': 'Discord 19.7 MB',
            'default_preset_managed': True,
        }), encoding='utf-8')
        with patch.object(settings_manager, 'SETTINGS_FILE', path):
            data = settings_manager._ensure_defaults()
        self.assertEqual(data['preset_profiles'][0]['preset'], 'p4')
        self.assertTrue(data['default_preset_managed'])

    def test_legacy_discord_default_without_management_flag_is_migrated(self):
        path = self._settings_path()
        profiles = settings_manager._default_preset_profiles()
        profiles[0]['preset'] = 'p6'
        path.write_text(json.dumps({
            'preset_profiles': profiles,
            'default_preset': 'Discord 19.7 MB',
        }), encoding='utf-8')
        with patch.object(settings_manager, 'SETTINGS_FILE', path):
            data = settings_manager._ensure_defaults()
        self.assertEqual(data['preset_profiles'][0]['preset'], 'p4')
        self.assertTrue(data['default_preset_managed'])

    def test_custom_discord_p6_is_preserved(self):
        path = self._settings_path()
        profiles = settings_manager._default_preset_profiles()
        profiles[0]['preset'] = 'p6'
        path.write_text(json.dumps({
            'preset_profiles': profiles,
            'default_preset': 'Discord 19.7 MB',
            'default_preset_managed': False,
        }), encoding='utf-8')
        with patch.object(settings_manager, 'SETTINGS_FILE', path):
            data = settings_manager._ensure_defaults()
        self.assertEqual(data['preset_profiles'][0]['preset'], 'p6')

    def test_editing_managed_default_preserves_explicit_p6(self):
        path = self._settings_path()
        with patch.object(settings_manager, 'SETTINGS_FILE', path):
            settings_manager._ensure_defaults()
            settings_manager.update_preset_profile('Discord 19.7 MB', {'preset': 'p6'})
            data = settings_manager._ensure_defaults()
        self.assertEqual(data['preset_profiles'][0]['preset'], 'p6')
        self.assertFalse(data['default_preset_managed'])

    def test_untouched_legacy_stock_is_migrated(self):
        path = self._settings_path()
        path.write_text(json.dumps({
            'size_buttons': list(settings_manager._LEGACY_STOCK_SIZE_BUTTONS),
            'preset_profiles': settings_manager._legacy_stock_profiles(),
            'default_preset': 'AV1 9.7MB (NVENC)',
            'default_preset_managed': True,
        }), encoding='utf-8')
        with patch.object(settings_manager, 'SETTINGS_FILE', path):
            data = settings_manager._ensure_defaults()
        self.assertEqual(data['default_preset'], 'Discord 19.7 MB')
        self.assertEqual(data['preset_profiles'][0]['name'], 'Discord 19.7 MB')
        self.assertEqual(data['size_buttons'][4], 19.7)

    def test_custom_legacy_settings_are_not_overwritten(self):
        path = self._settings_path()
        profiles = settings_manager._legacy_stock_profiles()
        profiles[0]['target_mb'] = 7.5
        custom_buttons = [3, 7.5, 12]
        path.write_text(json.dumps({
            'size_buttons': custom_buttons,
            'preset_profiles': profiles,
            'default_preset': profiles[0]['name'],
            'default_preset_managed': False,
        }), encoding='utf-8')
        with patch.object(settings_manager, 'SETTINGS_FILE', path):
            data = settings_manager._ensure_defaults()
        self.assertEqual(data['size_buttons'], custom_buttons)
        self.assertEqual(data['preset_profiles'][0]['target_mb'], 7.5)
        self.assertEqual(data['default_preset'], profiles[0]['name'])
