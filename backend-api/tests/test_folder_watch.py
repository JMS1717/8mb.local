import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings_manager
from app.folder_watch import FolderWatchService, _candidate_files


class FolderWatchTests(unittest.TestCase):
    def test_candidate_scan_is_recursive_and_ignores_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'nested').mkdir()
            (root / 'processed').mkdir()
            (root / 'a.mp4').touch()
            (root / 'nested' / 'b.mkv').touch()
            (root / 'processed' / 'c.mp4').touch()
            (root / 'already_8mblocal_x.mp4').touch()
            self.assertEqual(
                {path.name for path in _candidate_files(root, recursive=True)},
                {'a.mp4', 'b.mkv'},
            )

    def test_enabled_configuration_requires_real_absolute_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            input_dir = Path(directory) / 'watch'
            input_dir.mkdir()
            with patch.object(settings_manager, 'SETTINGS_FILE', path):
                saved = settings_manager.update_folder_watch_settings({
                    'enabled': True,
                    'input_folder': str(input_dir),
                    'stable_seconds': 2,
                    'poll_interval_seconds': 2,
                })
                self.assertTrue(saved['enabled'])
                with self.assertRaisesRegex(ValueError, 'existing readable'):
                    settings_manager.update_folder_watch_settings({
                        'enabled': True,
                        'input_folder': str(input_dir / 'missing'),
                    })

    def test_service_status_is_safe_when_disabled(self):
        status = FolderWatchService().status()
        self.assertFalse(status['running'])
        self.assertEqual(status['queued_count'], 0)
