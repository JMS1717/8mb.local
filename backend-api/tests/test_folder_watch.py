import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings_manager
from app.folder_watch import FolderWatchService, _candidate_files, celery_app


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

    def test_service_restores_queued_jobs_after_restart(self):
        service = FolderWatchService()
        task_id = 'folder-task-1'
        record = {
            'task_id': task_id,
            'input_path': '/watch/input.mp4',
            'output_path': '/watch/processed/input_8mblocal.mp4',
            'status': 'running',
        }
        with patch.object(
            settings_manager,
            'get_folder_watch_state',
            return_value={'/watch/input.mp4': record},
        ):
            service._restore_pending_from_state()

        self.assertEqual(service._pending[task_id], record)
        self.assertEqual(service.status()['queued_count'], 1)

    def test_dispatch_registers_metadata_before_task(self):
        events = []

        async def store_metadata(*args, **kwargs):
            events.append('metadata')

        def send_task(*args, **kwargs):
            events.append('dispatch')

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.mp4'
            source.write_bytes(b'video')
            config = {
                'profile': 'Default',
                'output_mode': 'same_folder',
            }
            profiles = {
                'default': 'Default',
                'profiles': [{
                    'name': 'Default',
                    'target_mb': 10,
                    'video_codec': 'libx264',
                    'audio_codec': 'aac',
                    'audio_kbps': 128,
                    'preset': 'medium',
                    'tune': 'film',
                    'container': 'mp4',
                }],
            }
            with patch.object(settings_manager, 'get_preset_profiles', return_value=profiles), \
                    patch('app.folder_watch.ffprobe'), \
                    patch.object(settings_manager, 'update_folder_watch_state'), \
                    patch('app.folder_watch.store_job_metadata', new=store_metadata), \
                    patch.object(celery_app, 'send_task', side_effect=send_task):
                asyncio.run(FolderWatchService()._dispatch(source, source.stat(), config))

        self.assertEqual(events, ['metadata', 'dispatch'])
