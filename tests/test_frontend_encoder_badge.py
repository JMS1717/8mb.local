from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
CODECS = (ROOT / "frontend" / "src" / "lib" / "codecs.ts").read_text(encoding="utf-8")
PAGE = (ROOT / "frontend" / "src" / "routes" / "+page.svelte").read_text(encoding="utf-8")


class FrontendEncoderBadgeTests(unittest.TestCase):
    def test_canonical_helper_classifies_supported_hardware_families(self):
        for token, label in (
            ("_nvenc", "NVIDIA NVENC"),
            ("_qsv", "Intel Quick Sync"),
            ("_vaapi", "VAAPI hardware"),
            ("_amf", "AMD AMF"),
            ("videotoolbox", "Apple VideoToolbox"),
        ):
            self.assertIn(token, CODECS)
            self.assertIn(label, CODECS)
        self.assertIn("libx265", CODECS)
        self.assertIn("CPU/software", CODECS)

    def test_badge_uses_canonical_helper_not_nvenc_only_guess(self):
        self.assertIn("classifyEncoder(encodeMethod)", PAGE)
        self.assertNotIn("/_nvenc$/.test(encodeMethod)", PAGE)


if __name__ == "__main__":
    unittest.main()
