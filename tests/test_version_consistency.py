import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VersionConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+\.\d+$")
        parts = self.version.split(".")
        self.display = ".".join(parts[:3])

    def test_frontend_generated_version_matches_root(self):
        text = (ROOT / "frontend/src/lib/generated-version.ts").read_text(encoding="utf-8")
        self.assertIn(f"export const APP_VERSION = '{self.version}';", text)
        self.assertIn(f"export const APP_VERSION_DISPLAY = '{self.display}';", text)

    def test_backend_generated_version_and_api_source_match_root(self):
        version_text = (ROOT / "backend-api/app/version.py").read_text(encoding="utf-8")
        config_text = (ROOT / "backend-api/app/config.py").read_text(encoding="utf-8")
        system_text = (ROOT / "backend-api/app/routers/system.py").read_text(encoding="utf-8")
        self.assertIn(f'APP_VERSION = "{self.version}"', version_text)
        self.assertIn("from .version import APP_VERSION as GENERATED_APP_VERSION", config_text)
        self.assertIn("APP_VERSION: str = Field(default=GENERATED_APP_VERSION)", config_text)
        self.assertIn('return {"version": settings.APP_VERSION}', system_text)

    def test_ui_imports_generated_version(self):
        text = (ROOT / "frontend/src/routes/+page.svelte").read_text(encoding="utf-8")
        self.assertIn("from '$lib/generated-version'", text)
        self.assertIn("appVersion: string = APP_VERSION", text)

    def test_docker_and_workflow_versions_are_derived_from_root(self):
        docker_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        workflow_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / ".github/workflows").glob("*")
            if path.is_file()
        )
        self.assertRegex(
            docker_text,
            rf"(?m)^\s*ARG\s+BUILD_VERSION\s*=\s*{re.escape(self.version)}\s*$",
        )
        for name in ("docker-compose.yml", "docker-compose.cpu.yml", "docker-compose.vaapi.yml"):
            compose = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(f'BUILD_VERSION: "${{APP_VERSION:-{self.version}}}"', compose)
        self.assertNotRegex(workflow_text, r"(?m)^\s*APP_VERSION\s*=\s*\d")
        self.assertNotRegex(workflow_text, r"BUILD_VERSION\s*:\s*\d")

    def test_existing_package_metadata_is_not_mismatched(self):
        for path in (ROOT / "frontend/package.json", ROOT / "package.json"):
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            package_version = data.get("version")
            if package_version is not None:
                self.assertIn(package_version, {self.version, self.display})

    def test_frontend_validation_scripts_exist(self):
        package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
        scripts = package.get("scripts", {})
        self.assertIn("check", scripts)
        self.assertIn("lint", scripts)

    def test_release_compose_validation_uses_requested_version(self):
        release = (ROOT / "release-local.ps1").read_text(encoding="utf-8")
        self.assertIn("$oldComposeVersion = $env:APP_VERSION", release)
        self.assertIn("$env:APP_VERSION = $Version", release)
        self.assertIn("docker-compose-config", release)
        self.assertNotIn("docker-load-verify", release)
        self.assertIn("docker-archive-manifest", release)

    def test_release_script_rejects_stale_or_unsafe_artifacts(self):
        release = (ROOT / "release-local.ps1").read_text(encoding="utf-8")
        self.assertIn(".8mblocal-release-output", release)
        self.assertIn("Refusing to overwrite an unmarked directory", release)
        self.assertNotIn("$searchRoots", release)
        self.assertNotIn("-notlike ($Version + '*')", release)
        self.assertIn("Get-NormalizedFourPartVersion", release)
        self.assertNotIn("Resolve-FfmpegTool", release)
        self.assertIn("'--entrypoint', 'ffmpeg'", release)
        self.assertIn("'--entrypoint', 'ffprobe'", release)
        self.assertNotIn("((if ($combined)", release)
        self.assertIn("$separator = if ($combined)", release)
        self.assertIn("-TimeoutSeconds 3600", release)
        self.assertIn("-TimeoutSeconds 1800", release)

    def test_local_evidence_is_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("8MB-REMOTE-TEST-LOGS/", ignore)
        self.assertIn("8MB-LOCAL-TEST-LOGS/", ignore)

    def test_windows_packaging_metadata_matches_root(self):
        installer = (ROOT / "windows/installer.iss").read_text(encoding="utf-8")
        self.assertRegex(installer, r"(?im)^\s*AppVersion=\{#MyAppVersion\}\s*$")
        self.assertIn(f'#define MyAppVersion "{self.version}"', installer)
        self.assertIn(f"VersionInfoVersion={{#MyAppVersion}}", installer)

        manifest = (ROOT / "windows/msix/AppxManifest.xml.template").read_text(encoding="utf-8")
        self.assertIn('Version="__VERSION__"', manifest)
        self.assertIn('Name="runFullTrust"', manifest)
        self.assertNotRegex(manifest, r"(?i)unvirtualizedResources|FileSystemWriteVirtualization")

        version_info = (ROOT / "windows/version_info.txt").read_text(encoding="utf-8")
        self.assertIn("filevers=(" + ", ".join(self.version.split(".")) + ")", version_info)
        self.assertIn(f'StringStruct("FileVersion", "{self.version}")', version_info)

        desktop = (ROOT / "windows/desktop_app.py").read_text(encoding="utf-8")
        portable = (ROOT / "windows/8mblocal.py").read_text(encoding="utf-8")
        msix_builder = (ROOT / "windows/build-msix.ps1").read_text(encoding="utf-8")
        self.assertRegex(
            desktop,
            rf"(?m)DESKTOP_VERSION\s*=\s*[\"']{re.escape(self.version)}[\"']",
        )
        self.assertNotRegex(portable, r"(?m)^VERSION\s*=\s*[\"']\d+(?:\.\d+){1,3}[\"']")
        self.assertNotRegex(msix_builder, r"(?m)^\s*\[string\]\$Version\s*=\s*[\"']\d+")

    def test_latest_batch_and_modular_encoder_architecture_is_preserved(self):
        required = (
            ROOT / "frontend/src/routes/batch/+page.svelte",
            ROOT / "frontend/src/lib/pendingBatch.ts",
            ROOT / "frontend/src/lib/codecs.ts",
            ROOT / "backend-api/app/routers/compress.py",
            ROOT / "backend-api/app/routers/settings.py",
            ROOT / "worker/app/encoder.py",
            ROOT / "worker/app/tasks.py",
        )
        for path in required:
            self.assertTrue(path.is_file(), f"required latest architecture file missing: {path}")

        settings_page = (ROOT / "frontend/src/routes/settings/+page.svelte").read_text(encoding="utf-8")
        self.assertIn(
            "defaultPresetName = presetProfiles[0]?.name ?? null",
            settings_page,
        )


if __name__ == "__main__":
    unittest.main()
