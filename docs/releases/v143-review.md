# v143 approval packet — not published

Proposed title: **v143: Android App, Windows ARM64, and More Ways to Compress**

Proposed tag: `v143`; application version: `143.0.0.0` (Android: `143.0.0`).
The release body is [v143.md](v143.md). It leads with the Android app and
describes benefits and installation without implementation details.

## Changes to review

- README: Android is directly below the introduction, before the demo and
  feature list, with a download link and a phone/tablet installation guide.
- Release notes: Android is the first heading and first download; camera-roll
  saving, preview, automatic hardware selection, and local processing are clear.
- Windows ARM64 and ARM64 Docker are included. Apple Silicon is described as
  experimental, not as verified on real Mac hardware.
- Frontend dependency security patches and a repeatable dependency-audit gate.
- Android CI no longer asks for the removed legacy SDK `tools` package.

## Proposed public downloads

| Download | Purpose | Publication condition |
| --- | --- | --- |
| `8mblocal-143-android.apk` | Android phones/tablets | Permanent key, verified signature, update compatibility reviewed, signed APK tested |
| `8mblocal-Setup.exe` | Most Windows PCs | Fresh x64 install/runtime checks; approve unsigned publisher warning |
| `8mblocal.exe` | Portable Windows x64 | Same build and smoke checks as installer |
| `8mblocal-Setup-arm64.exe` | Windows on ARM | Fresh native ARM64 install/runtime checks; approve unsigned warning |
| `8mblocal-arm64.exe` | Portable Windows ARM64 | Rename the ARM64 workflow's `8mblocal.exe` to avoid colliding with x64 |
| `8mblocal_143.0.0.0_macos_arm64.dmg` | Apple Silicon (experimental) | Approve experimental/unnotarized distribution or omit until notarization and real-Mac validation |
| `SHA256SUMS.txt` | Integrity checks | Recalculate against the exact final, renamed downloads |

Also retain two developer-only build-input downloads:
`8mblocal-ffmpeg-8.1.3-win64.zip` and `8mblocal-ffmpeg-8.1.3-winarm64.zip`.
These are unchanged, SHA-256-pinned BtbN archives, not app installers. The
Windows build prefers these release mirrors and uses the dated upstream
archives only as a prepublication fallback. Upstream removes older autobuilds;
publishing the mirrored inputs with the approved release prevents another
expired-download failure. Include their hashes in the final checksum file.

Do not attach unsigned Android APKs, debug APKs, AAB submission bundles, or
unsigned Store-submission MSIX files as ordinary user downloads. Do not use
old local EXEs without matching build provenance. CI artifacts are build
outputs, not automatically public release downloads.

## Separate decisions before publishing

1. Review and approve the title, README, release notes, download list, and
   known limitations. No merge permission is assumed from preparing them.
2. Resolve Android signing and backups, including compatibility with the APK
   already attached to v142. Verify the exact signed APK on a device; debug
   smoke alone is not proof that the public signed APK works.
   The v142 APK was checked on September 30: it is validly **debug-signed**
   and has the same certificate as the local debug build. A new permanent
   signing key means current debug installs must be uninstalled first, which
   removes app-local history/settings. Already-saved gallery media should be
   preserved by Android, but app-local data cannot be promised to migrate.
3. Approve unsigned Windows downloads and decide whether the experimental
   Mac download should be included. Publisher signing can be added separately.
4. Review the final commit and final hashes after all builds pass. If source
   changes during integration with main, rerun affected checks before tagging.
5. Approve merge/integration and creation of `v143` and the GitHub release.
   Docker Hub publication is a separate explicit approval: it changes `latest`.

No Google Play, Microsoft Store, or Docker Hub submission is implied by this
GitHub release. The newer Easypanel documentation on main must be preserved
during any later integration; this preparation does not overwrite it.
