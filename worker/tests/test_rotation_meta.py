"""Tests for display rotation metadata parsing (portrait phone video, etc.)."""
import unittest

from worker.app.utils import (
    coded_to_display_dimensions,
    infer_rotation_from_display_aspect_ratio,
    parse_stream_rotation_degrees,
    transpose_filters_for_rotation_degrees,
)


class TestRotationMeta(unittest.TestCase):
    def test_parse_rotate_tag_90(self):
        s = {"tags": {"rotate": "90"}}
        self.assertEqual(parse_stream_rotation_degrees(s, {}), 90)

    def test_parse_display_matrix_rotation(self):
        s = {
            "side_data_list": [
                {"side_data_type": "Display Matrix", "rotation": -90},
            ]
        }
        self.assertIn(parse_stream_rotation_degrees(s, {}), (90, 270))

    def test_coded_to_display_swap(self):
        self.assertEqual(coded_to_display_dimensions(1920, 1080, 90), (1080, 1920))
        self.assertEqual(coded_to_display_dimensions(1920, 1080, 270), (1080, 1920))
        self.assertEqual(coded_to_display_dimensions(1920, 1080, 0), (1920, 1080))
        self.assertEqual(coded_to_display_dimensions(1920, 1080, 180), (1920, 1080))

    def test_dar_infer_portrait_storage(self):
        self.assertEqual(infer_rotation_from_display_aspect_ratio(1920, 1080, "9:16"), 90)
        self.assertEqual(infer_rotation_from_display_aspect_ratio(1920, 1080, "1080:1920"), 90)
        self.assertEqual(infer_rotation_from_display_aspect_ratio(1920, 1080, "16:9"), 0)

    def test_transpose_chain(self):
        self.assertEqual(transpose_filters_for_rotation_degrees(90), ["transpose=1"])
        self.assertEqual(transpose_filters_for_rotation_degrees(270), ["transpose=2"])
        self.assertEqual(len(transpose_filters_for_rotation_degrees(180)), 2)


if __name__ == "__main__":
    unittest.main()
