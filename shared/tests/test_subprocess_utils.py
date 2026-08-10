from __future__ import annotations

import unittest
from unittest.mock import patch

from shared import subprocess_utils


class TestHiddenProcessKwargs(unittest.TestCase):
    def test_windows_children_use_create_no_window(self):
        with (
            patch.object(subprocess_utils.sys, "platform", "win32"),
            patch.object(subprocess_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            self.assertEqual(
                subprocess_utils.hidden_process_kwargs(),
                {"creationflags": 0x08000000},
            )

    def test_non_windows_children_keep_normal_process_behavior(self):
        with patch.object(subprocess_utils.sys, "platform", "linux"):
            self.assertEqual(subprocess_utils.hidden_process_kwargs(), {})


if __name__ == "__main__":
    unittest.main()
