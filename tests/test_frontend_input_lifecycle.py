from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
PAGE = (ROOT / "frontend" / "src" / "routes" / "+page.svelte").read_text(encoding="utf-8")


class FrontendInputLifecycleTests(unittest.TestCase):
    """Static regression checks for the browser File/server staging contract.

    The repository does not currently ship a browser test runner. These checks
    protect the important control-flow contract while the Docker E2E covers the
    server-side transient-input cleanup behavior.
    """

    def test_staged_input_is_explicit_and_not_filename_based(self):
        self.assertIn("let stagedFile: File | null = null;", PAGE)
        self.assertIn("let stagedInputToken: string | null = null;", PAGE)
        self.assertIn("let stagedInputValid = false;", PAGE)
        self.assertIn("stagedFile === selectedFile", PAGE)
        self.assertNotIn("uploadedFileName", PAGE)

    def test_compress_reuploads_after_terminal_cleanup(self):
        self.assertIn("async function doUpload(force = false): Promise<boolean>", PAGE)
        self.assertIn("const uploaded = await doUpload(true);", PAGE)
        self.assertIn("The previous server copy was released", PAGE)
        self.assertIn("disabled={!file || isCompressing || isUploading || isAnalyzing}", PAGE)

    def test_terminal_states_invalidate_server_staged_input(self):
        self.assertGreaterEqual(PAGE.count("invalidateStagedInput();"), 7)
        for terminal_type in ("done", "error", "canceled"):
            self.assertIn(f"data.type === '{terminal_type}'", PAGE)

    def test_missing_input_recovery_is_bounded(self):
        self.assertIn("function isMissingStagedInputError", PAGE)
        self.assertIn("let recoveryUsed = false;", PAGE)
        self.assertIn("recoveryUsed = true;", PAGE)
        self.assertIn("Automatic re-upload failed; the selected file could not be analyzed.", PAGE)
        self.assertIn("Compression start failed after automatic re-upload:", PAGE)

    def test_reset_and_new_file_selection_invalidate_staged_state(self):
        self.assertIn("function selectFile(next: File | null)", PAGE)
        self.assertIn("function reset()", PAGE)
        self.assertIn("function clearSelectedFile()", PAGE)
        self.assertGreaterEqual(PAGE.count("stagedFile = null;"), 3)


if __name__ == "__main__":
    unittest.main()
