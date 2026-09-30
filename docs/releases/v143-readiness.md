# v143 release readiness

This checklist prepares v143 without merging, tagging, publishing a container,
or creating a GitHub release.

## September 30 review snapshot

Implementation candidate: `209b2de` on `feature/v143-multiplatform`; PR #47 is
still open. This is **not yet ready to publish**: Android needs a permanent
release signing key and a test of the exact signed APK, and the publisher must
approve the downloads and limitations in [v143-review.md](v143-review.md).

Fresh evidence:

- [Shared tests, frontend build/audit, and local end-to-end tests](https://github.com/JMS1717/8mb.local/actions/runs/36742197068): passed at `209b2de`.
- [Android unit tests, lint, APK/bundle assembly, and API 35 instrumentation](https://github.com/JMS1717/8mb.local/actions/runs/36742197085): passed at `209b2de`; release outputs remain unsigned.
- [Apple Silicon package build and executable/signature smoke](https://github.com/JMS1717/8mb.local/actions/runs/36742197462): passed at `209b2de`; not notarized and not proof of real-Mac hardware encoding.
- [Native Windows x64/ARM64 install and compression smoke](https://github.com/JMS1717/8mb.local/actions/runs/36742188680): x64 passed; ARM64 is still finishing after recovery of the verified 8.1.2 FFmpeg inputs.
- [Native Docker amd64/arm64 end-to-end smoke](https://github.com/JMS1717/8mb.local/actions/runs/36740087055): both passed. Container inputs did not change after `04b2039`.
- Local Python regression tests: 116 passed, 1 skipped; backend/API tests:
  80 passed, 1 skipped. Frontend type/build checks passed with no diagnostics;
  the full dependency audit reported zero vulnerabilities.
- The fresh x64 package at `04b2039` passed exact AMD hardware H.264, HEVC,
  and AV1 exports on the local GPU, edge inputs, batch ZIP, and restart/history
  recovery. Evidence is private under `dist/release-review-v143/`.
- Android MediaTek/Snapdragon physical results are from August 23, not a new
  September 30 device run. The compression implementation is unchanged; the
  public, permanently signed APK still needs its own device smoke.

The public v142 APK was verified as debug-signed, matching the local debug
certificate. A new permanent signing key requires a one-time reinstall for
existing debug installs, losing app-local history/settings. Review that
transition before creating or distributing the permanent key.

## Required validation

- [x] Shared tests, frontend, Android, and macOS ARM64 pass at the implementation candidate.
- [ ] Before advertising verified macOS hardware acceleration, run the default
      strict `macos/build.sh` probe on a real Apple Silicon Mac; hosted CI does
      not claim a VideoToolbox compression session.
- [ ] Windows x64 and native ARM64 release workflow passes install and runtime smoke.
- [x] Docker amd64 and native arm64 end-to-end workflow passes.
- [x] Android physical smoke records a hardware encoder and publishes a playable
      result to `DCIM/8mb.local` on both MT6835 and SM8650 ARM64 devices.
- [x] `scripts/check-version.ps1` confirms all package versions match `VERSION`.
- [ ] Release artifacts have SHA-256 checksums.

## Distribution credentials

- [ ] A permanent Android upload key is backed up securely and the four secrets
      in `android/SIGNING.md` are configured.
- [ ] The signed Android workflow passes both APK and AAB signature verification.
- [ ] Approve an experimental, unnotarized Mac download or configure the Developer
      ID/notarization secrets in `macos/SIGNING.md` and pass the notarized workflow.
- [ ] Approve unsigned Windows downloads with a clear warning or configure the
      Authenticode secrets in `windows/SIGNING.md` and pass the signed workflow.
- [ ] Approve preservation of the exact FFmpeg build-input archives with the
      GitHub release; upstream dated downloads expire.

Google Play and Microsoft Store submissions are separate from this GitHub
release. Store listing review is not a blocker for direct-download APK/EXE
distribution. No CodeRabbit approval is required for this preparation.

## Final approval gate

- [ ] Review the exact commit and artifacts.
- [ ] Merge only after explicit approval.
- [ ] Create the v143 tag/release only after explicit approval.
- [ ] Run Docker publish only with its explicit `PUBLISH` confirmation.
