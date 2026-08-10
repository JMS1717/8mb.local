"""Regression coverage for output-path containment checks."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["AUTH_ENABLED"] = "false"

from app.routers.download import _media_type_for_path, _safe_output_path


class TestDownloadPaths(unittest.TestCase):
    def test_output_media_types_match_containers(self):
        self.assertEqual(_media_type_for_path(Path("output.mp4")), "video/mp4")
        self.assertEqual(_media_type_for_path(Path("audio.m4a")), "audio/mp4")
        self.assertEqual(_media_type_for_path(Path("output.mkv")), "video/x-matroska")

    def test_output_must_be_inside_output_directory(self):
        with tempfile.TemporaryDirectory(prefix="8mb-output-test-") as temp_dir:
            root = Path(temp_dir)
            output = root / "encoded.mp4"
            output.write_bytes(b"ok")
            outside = root.parent / f"{root.name}-outside.mp4"
            outside.write_bytes(b"no")
            try:
                with patch("app.routers.download.OUTPUTS_DIR", root):
                    self.assertEqual(_safe_output_path(output), output.resolve())
                    self.assertIsNone(_safe_output_path(outside))
                    self.assertIsNone(_safe_output_path(root / "missing.mp4"))
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
