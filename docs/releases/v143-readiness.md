# v143 release readiness

This checklist prepares v143 without merging, tagging, publishing a container,
or creating a GitHub release.

## Required validation

- [ ] Pull request checks pass: shared tests, frontend, Android, and macOS ARM64.
- [ ] Windows x64 and native ARM64 release workflow passes install and runtime smoke.
- [ ] Docker amd64 and native arm64 end-to-end workflow passes.
- [ ] Android physical smoke records a hardware encoder and publishes a playable
      result to `DCIM/8mb.local`.
- [ ] `scripts/check-version.ps1` confirms all package versions match `VERSION`.
- [ ] Release artifacts have SHA-256 checksums.

## Distribution credentials

- [ ] A permanent Android upload key is backed up securely and the four secrets
      in `android/SIGNING.md` are configured.
- [ ] The signed Android workflow passes both APK and AAB signature verification.
- [ ] For public macOS distribution, the Developer ID and notarization secrets in
      `macos/SIGNING.md` are configured and the notarized workflow passes.
- [ ] For public Windows EXE distribution, the Authenticode secrets in
      `windows/SIGNING.md` are configured and the signed workflow passes.
- [ ] Store listing text links to `PRIVACY.md`; screenshots and support contact
      have been reviewed by the publisher.

## Final approval gate

- [ ] Review the exact commit and artifacts.
- [ ] Merge only after explicit approval.
- [ ] Create the v143 tag/release only after explicit approval.
- [ ] Run Docker publish only with its explicit `PUBLISH` confirmation.
