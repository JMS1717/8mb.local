import asyncio
import ctypes
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from app import deps


class WindowsTemporaryStorageTests(unittest.TestCase):
    def test_disk_mode_does_not_apply_temporary_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "input.mp4"
            upload = UploadFile(file=io.BytesIO(b"media"), filename="input.mp4")
            with patch.object(deps.settings, "MEDIA_STORAGE", "disk"), patch.object(
                deps, "mark_file_temporary"
            ) as mark:
                asyncio.run(deps.save_upload_file(upload, destination))
            mark.assert_not_called()
            self.assertEqual(destination.read_bytes(), b"media")

    def test_auto_mode_applies_temporary_hint_to_normal_path(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "input.mp4"
            upload = UploadFile(file=io.BytesIO(b"media"), filename="input.mp4")
            with patch.object(deps.settings, "MEDIA_STORAGE", "auto"), patch.object(
                deps, "mark_file_temporary", return_value=True
            ) as mark:
                asyncio.run(deps.save_upload_file(upload, destination))
            mark.assert_called_once_with(destination)
            self.assertEqual(destination.read_bytes(), b"media")

    def test_memory_mode_rejects_when_hint_cannot_be_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "input.mp4"
            upload = UploadFile(file=io.BytesIO(b"media"), filename="input.mp4")
            with patch.object(deps.settings, "MEDIA_STORAGE", "memory"), patch.object(
                deps, "mark_file_temporary", return_value=False
            ):
                with self.assertRaisesRegex(HTTPException, "RAM-preferred"):
                    asyncio.run(deps.save_upload_file(upload, destination))
            self.assertFalse(destination.exists())

    @unittest.skipUnless(os.name == "nt", "Windows file attributes are Windows-only")
    def test_temporary_attribute_and_ffmpeg_path_access(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            bundled = Path(__file__).parents[2] / "windows" / "ffmpeg" / "ffmpeg.exe"
            if bundled.exists():
                ffmpeg = str(bundled)
        if not ffmpeg:
            self.skipTest("ffmpeg is not available")

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            output = Path(directory) / "output.mp4"
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                 "-i", "color=c=black:s=160x90:r=10", "-t", "0.3", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", str(source)],
                check=True, timeout=60,
            )
            self.assertTrue(deps.mark_file_temporary(source))
            get_attributes = ctypes.windll.kernel32.GetFileAttributesW
            get_attributes.restype = ctypes.c_uint32
            self.assertNotEqual(get_attributes(str(source)), 0xFFFFFFFF)
            self.assertTrue(get_attributes(str(source)) & deps.WINDOWS_FILE_ATTRIBUTE_TEMPORARY)
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                 "-c:v", "libx264", str(output)],
                check=True, timeout=60,
            )
            self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
