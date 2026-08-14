import unittest

from worker.app.tasks import effective_trim_duration


class TrimDurationTests(unittest.TestCase):
    def test_trimmed_duration_drives_target_size_math(self):
        self.assertEqual(effective_trim_duration(600, '0', '60'), 60)
        self.assertEqual(effective_trim_duration(600, None, '60'), 60)
        self.assertEqual(effective_trim_duration(600, '120', None), 480)

    def test_trim_end_is_clamped_to_source_duration(self):
        self.assertEqual(effective_trim_duration(60, None, '600'), 60)

    def test_invalid_trim_ranges_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, 'greater'):
            effective_trim_duration(60, '30', '20')
        with self.assertRaisesRegex(ValueError, 'beyond'):
            effective_trim_duration(60, '60', None)
