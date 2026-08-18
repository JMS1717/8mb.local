"""Hostile ffprobe metadata regression coverage."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from worker.app.ffmpeg_helpers import (
    COLOR_METADATA_OPTIONS,
    ffmpeg_rejected_color_metadata,
    remove_option_pairs,
)
from worker.app.media_metadata import (
    normalize_color_metadata,
    normalize_color_metadata_value,
    source_color_metadata_args,
)
from worker.app.utils import _normalize_rotation_degrees, ffprobe_info, parse_fps_fraction


class TestColorMetadataNormalization(unittest.TestCase):
    def test_windows_recorder_reserved_transfer_is_omitted_independently(self):
        source = {
            "video_color_range": "pc",
            "video_color_space": "bt470bg",
            "video_color_primaries": "bt709",
            "video_color_transfer": "reserved",
        }
        self.assertEqual(
            source_color_metadata_args(source),
            ["-color_range", "pc", "-colorspace", "bt470bg", "-color_primaries", "bt709"],
        )
        self.assertNotIn("reserved", source_color_metadata_args(source))

    def test_valid_hdr10_and_hlg_values_are_preserved(self):
        for transfer in ("smpte2084", "arib-std-b67"):
            normalized = normalize_color_metadata({
                "video_color_range": "tv",
                "video_color_space": "bt2020nc",
                "video_color_primaries": "bt2020",
                "video_color_transfer": transfer,
            })
            self.assertEqual(normalized["video_color_transfer"], transfer)
            self.assertEqual(normalized["video_color_space"], "bt2020nc")

    def test_unknown_values_fail_closed_per_field(self):
        source = {
            "video_color_range": "banana",
            "video_color_space": "foo bar",
            "video_color_primaries": "unknown/unknown",
            "video_color_transfer": "reserved",
        }
        self.assertEqual(normalize_color_metadata(source), {})
        self.assertEqual(source_color_metadata_args(source), [])

    def test_placeholder_matrix_never_becomes_an_option_value(self):
        placeholders = ["", "unknown", "reserved", "unspecified", "undefined", "n/a", "N/A", "none", "unknown/unknown"]
        for placeholder in placeholders:
            args = source_color_metadata_args({
                "video_color_range": placeholder,
                "video_color_space": placeholder,
                "video_color_primaries": placeholder,
                "video_color_transfer": placeholder,
            })
            self.assertEqual(args, [], placeholder)

    def test_case_whitespace_and_known_aliases_are_canonicalized(self):
        self.assertEqual(normalize_color_metadata_value("video_color_range", " FULL "), "pc")
        self.assertEqual(normalize_color_metadata_value("video_color_space", "BT2020-NCL"), "bt2020nc")
        self.assertEqual(normalize_color_metadata_value("video_color_transfer", " SMPTE-ST-2084 "), "smpte2084")
        self.assertIsNone(normalize_color_metadata_value("video_color_transfer", "<shell-looking>"))

    def test_partial_metadata_keeps_valid_fields(self):
        self.assertEqual(
            normalize_color_metadata({
                "video_color_primaries": "bt709",
                "video_color_transfer": "reserved",
            }),
            {"video_color_primaries": "bt709"},
        )


class TestMetadataRetryHelpers(unittest.TestCase):
    def test_remove_option_pairs_removes_only_optional_metadata(self):
        command = ["ffmpeg", "-i", "in.mp4", "-color_trc", "reserved", "-c:v", "libx265", "out.mp4"]
        self.assertEqual(
            remove_option_pairs(command, COLOR_METADATA_OPTIONS),
            ["ffmpeg", "-i", "in.mp4", "-c:v", "libx265", "out.mp4"],
        )

    def test_rejection_classifier_is_narrow(self):
        self.assertTrue(ffmpeg_rejected_color_metadata(
            "Unable to parse color_trc option value reserved\nError setting option color_trc"
        ))
        self.assertFalse(ffmpeg_rejected_color_metadata("Error while opening encoder h264_nvenc"))


class TestNumericProbeBounds(unittest.TestCase):
    def test_fps_rejects_nonfinite_negative_and_absurd_values(self):
        for value in ("nan", "inf", "-30", "1001", "nan/1", "100000/1"):
            self.assertIsNone(parse_fps_fraction(value), value)
        self.assertEqual(parse_fps_fraction("60/1"), 60.0)

    def test_rotation_rejects_nonfinite_and_arbitrary_angles(self):
        self.assertEqual(_normalize_rotation_degrees("-90"), 270)
        self.assertIsNone(_normalize_rotation_degrees("45"))
        self.assertIsNone(_normalize_rotation_degrees("nan"))
        self.assertIsNone(_normalize_rotation_degrees("inf"))

    def test_ffprobe_numeric_bounds_are_applied_to_parser_output(self):
        payload = {
            "format": {"duration": "2.0", "tags": {}},
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": "999999",
                "height": "0",
                "avg_frame_rate": "1001/1",
                "bits_per_raw_sample": "999",
                "display_aspect_ratio": "nan:1",
            }],
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch("worker.app.utils.subprocess.run", return_value=result):
            info = ffprobe_info("bounded-metadata.mp4")
        self.assertIsNone(info["width"])
        self.assertIsNone(info["height"])
        self.assertIsNone(info["video_fps"])
        self.assertIsNone(info["video_bits_per_raw_sample"])
        self.assertIsNone(info["display_aspect_ratio"])


if __name__ == "__main__":
    unittest.main()
