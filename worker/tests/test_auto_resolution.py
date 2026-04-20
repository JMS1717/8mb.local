import unittest

from worker.app.auto_resolution import choose_auto_resolution


class TestAutoResolution(unittest.TestCase):
    def test_downscale_4k_low_bitrate(self):
        # 4K source, very low target bitrate -> softened heuristic should prefer a modest drop (likely 1440p)
        w, h = 3840, 2160
        target_video_kbps = 2500  # low for 4K
        mw, mh = choose_auto_resolution(w, h, orig_video_kbps=12000, target_video_kbps=target_video_kbps)
        self.assertIsNone(mw)  # width not specified directly
        # Accept 1440p or further if extremely starved; ensure not original and not upscale
        self.assertTrue(mh in (1440, 1080, 720, 480, 360, 240), f"Unexpected chosen height {mh}")
        self.assertLessEqual(mh, 2160)

    def test_keep_original_when_high_bitrate(self):
        w, h = 1920, 1080
        target_video_kbps = 8000  # ample
        mw, mh = choose_auto_resolution(w, h, orig_video_kbps=15000, target_video_kbps=target_video_kbps)
        # Expect original (None,None) or at worst 1080p (same height)
        self.assertTrue(mw is None)
        self.assertTrue(mh in (None, 1080))

    def test_min_height_respected(self):
        w, h = 640, 360
        target_video_kbps = 200  # tiny
        mw, mh = choose_auto_resolution(w, h, orig_video_kbps=800, target_video_kbps=target_video_kbps, min_height=360)
        self.assertEqual(mh, 360)

    def test_explicit_target_overrides(self):
        w, h = 3840, 2160
        mw, mh = choose_auto_resolution(w, h, orig_video_kbps=8000, target_video_kbps=2000, explicit_target_height=720)
        self.assertEqual(mh, 720)

if __name__ == '__main__':
    unittest.main()
