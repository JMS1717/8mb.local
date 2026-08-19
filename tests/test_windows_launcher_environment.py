import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from windows.desktop_app import _download_target_path, _load_persisted_environment


class WindowsLauncherEnvironmentTests(unittest.TestCase):
    def test_download_target_uses_downloads_and_avoids_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            (downloads / 'video.zip').write_bytes(b'old')

            target = _download_target_path(r'C:\temporary\video.zip', downloads)

            self.assertEqual(target, downloads / 'video (2).zip')

    def test_download_target_sanitizes_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            target = _download_target_path(r'C:\temporary\bad:name?.mp4', Path(directory))

            self.assertEqual(target.name, 'bad_name_.mp4')

    def test_runtime_media_settings_reload_from_per_user_env(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / '.env').write_text(
                'MEDIA_STORAGE=memory\n'
                'MEDIA_MEMORY_LIMIT_GB=4\n'
                'MAX_UPLOAD_SIZE_MB=2048\n'
                'HISTORY_ENABLED=false\n'
                'APP_DATA_DIR=C:\\\\must-not-override\\\\app\n',
                encoding='utf-8',
            )
            with patch.dict(os.environ, {}, clear=True):
                _load_persisted_environment(data_dir)

                self.assertEqual(os.environ['MEDIA_STORAGE'], 'memory')
                self.assertEqual(os.environ['MEDIA_MEMORY_LIMIT_GB'], '4')
                self.assertEqual(os.environ['MAX_UPLOAD_SIZE_MB'], '2048')
                self.assertEqual(os.environ['HISTORY_ENABLED'], 'false')
                self.assertNotIn('APP_DATA_DIR', os.environ)


if __name__ == '__main__':
    unittest.main()
