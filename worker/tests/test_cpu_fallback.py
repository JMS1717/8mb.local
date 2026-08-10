"""Regression coverage for inventory-aware hardware fallback selection."""
from __future__ import annotations

import unittest

from worker.app.tasks import _cpu_fallback_for


class TestCpuFallback(unittest.TestCase):
    def test_av1_hardware_fallback_fails_clearly_without_svt(self):
        with self.assertRaisesRegex(RuntimeError, "SVT-AV1"):
            _cpu_fallback_for(
                "av1_nvenc",
                {"libx264", "libx265", "libaom-av1"},
            )

    def test_unlisted_inventory_does_not_change_canonical_mapping(self):
        encoder, _flags = _cpu_fallback_for("h264_qsv", {"libx264", "libx265"})
        self.assertEqual(encoder, "libx264")


if __name__ == "__main__":
    unittest.main()
