# v143 approval packet — not published

Proposed title: **v143: Android App and More Devices**

Proposed tag: `v143`; application version: `143.0.0.0` (Android: `143.0.0`).
The signed Android candidate was built from `4829d0b`; the Windows and
experimental Mac candidate artifacts were built from `209b2de`. Later changes
to the signing helper, tests, and release documentation do not change their
app runtime code. Per-platform build provenance and hashes remain explicit;
these are preparation artifacts, not a claim that integration/tagging is done.
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
- Android compatibility is explicit: Android 8–9 uses Save As/AAC; automatic
  gallery saving and Opus audio require Android 10+.
- The earlier Android preview used a debug key. The new permanent-key release
  requires a one-time uninstall/reinstall, resetting app-local history/settings;
  this is explained plainly in the installation guide and release notes.

## September 30 signed Android validation

- A permanent 4096-bit RSA key was created outside the repository; signing
  secrets are configured in GitHub. The public certificate SHA-256 is
  `96dbd77c852a382eafe9ac07cf5c59bb57300bb63a0ab7971e664cc7455cbbd9`.
- [Signed build and tests](https://github.com/JMS1717/8mb.local/actions/runs/36747146134)
  passed release unit tests/lint, APK/AAB signature checks, and API 35 release
  instrumentation. The public APK is non-debuggable. Four emulator tests ran;
  hardware-only compression and the opt-in demonstration were skipped there.
- The exact signed APK passed all five physical tests on a OnePlus 15 running
  Android 16: workflow UI, working encoder inventory, Opus audio extraction,
  hardware-only foreground-service compression, and screenshot/icon checks.
  Output was playable and published under `DCIM/8mb.local`, no longer pending,
  and the in-app preview and Share controls worked.
- Actual encoding used `c2.qti.hevc.encoder`, with hardware use proven and no
  software fallback. This phone exposes hardware AV1 decoders, but only a
  software AV1 encoder; automatic selection correctly prefers hardware HEVC.
- The opt-in default-settings demonstration also passed on the signed APK:
  Choose video, accept a synthetic Photo Picker result, Compress and save,
  and open Preview. The picker result is injected by the test, not a claim
  that every OEM's picker UI has been exercised. No personal media was used.
- APK SHA-256:
  `1406b51a3cae3f65978087f224b131b9475e18bd275caf1a75661ae8669c67e5`.
  Signature and APK identity were independently verified locally.
- With user approval, the old OnePlus debug APK and app-local data were backed
  up before replacing it. The Quest 3 was left untouched.
- Local DPAPI password recovery, restricted file permissions, repeat setup
  without identity changes, alias mismatch rejection, and preservation of an
  unknown existing key were tested. Disposable test keys were removed.
  **Independent/off-computer key and password backup is still pending.** The
  local recovery ZIP is tied to this Windows account/computer and is not a
  disaster-recovery backup by itself.

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
`8mblocal-ffmpeg-8.1.3-win64.zip` and `8mblocal-ffmpeg-8.1.2-winarm64.zip`.
These are SHA-256-pinned build inputs, not app installers. The x64 archive is
unchanged from BtbN. ARM64 retains the exact, verified 8.1.2 binaries from our
passing August CI build, with a license and provenance record; the newer
upstream ARM64 executable crashes at startup and is not used. The Windows
build prefers these release mirrors; CI can recover the ARM64 input from that
pinned passing artifact until publication. Upstream removes older autobuilds;
publishing the mirrored inputs with the approved release prevents another
expired-download failure. Include their hashes in the final checksum file.

Do not attach unsigned Android APKs, debug APKs, AAB submission bundles, or
unsigned Store-submission MSIX files as ordinary user downloads. Do not use
old local EXEs without matching build provenance. CI artifacts are build
outputs, not automatically public release downloads.

## Separate decisions before publishing

1. Review and approve the title, README, release notes, download list, and
   known limitations. No merge permission is assumed from preparing them.
2. Complete the independent signing-key/password backup. Permanent Android
   signing and exact signed-APK testing are done; debug smoke alone is not
   counted as proof that the public signed APK works.
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
