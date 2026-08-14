"""Live per-upload memory admission and reservation tests."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app import deps


class MemoryAdmissionTests(unittest.TestCase):
    def test_reservation_prevents_concurrent_overcommit_and_releases(self):
        with patch.object(deps, "_memory_capacity_bytes", return_value=100), patch.object(
            deps, "_MEMORY_RESERVED_BYTES", 0
        ):
            self.assertTrue(deps._reserve_memory_upload(80))
            self.assertFalse(deps._reserve_memory_upload(21))
            deps._release_memory_upload(80)
            self.assertTrue(deps._reserve_memory_upload(100))
            deps._release_memory_upload(100)


if __name__ == "__main__":
    unittest.main()
