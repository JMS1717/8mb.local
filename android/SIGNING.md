# Android release signing

Debug APKs and unsigned release artifacts are not suitable stable public
releases. Direct APK distribution needs a permanent app-signing key, kept for
every update. Do not replace an existing signing identity without reviewing
the effect on users who already installed the app.

## One-time setup

1. Check the signing certificate of any previously distributed APK first.
   Reuse its permanent app-signing key if it is available. A release signed
   with a different key cannot update the existing installation normally.
2. Create a key offline with Android Studio or `keytool` only if a new identity
   is needed. Back up the
   keystore and its passwords in a password manager before uploading anything.
3. Add these GitHub Actions repository secrets (the historical `UPLOAD` names
   also configure the signing key for the direct-download APK):

   - `ANDROID_UPLOAD_KEYSTORE_BASE64`: base64-encoded keystore bytes
   - `ANDROID_UPLOAD_STORE_PASSWORD`
   - `ANDROID_UPLOAD_KEY_ALIAS`
   - `ANDROID_UPLOAD_KEY_PASSWORD`

Do not commit the keystore, passwords, or a populated `keystore.properties`.

On Windows, `configure-release-signing.ps1 -Automatic` creates a permanent
4096-bit RSA app-signing key, random passwords, and repository signing secrets.
It reuses existing recovery information and refuses to overwrite an unknown
keystore. The default private directory is `%LOCALAPPDATA%\8mb.local-signing`,
accessible only to the current Windows account and SYSTEM. A local recovery
ZIP contains the password-encrypted keystore and DPAPI-encrypted passwords.
DPAPI recovery is tied to this Windows account/computer: this is **not** an
independent disaster-recovery backup. Before publishing, save both passwords
in a password manager and copy the keystore to separate protected storage.
GitHub secrets support CI but cannot be downloaded as a recovery backup.

Google Play enrollment is separate and is not required for a GitHub APK
release. With Play App Signing, the upload key signs submissions while the
app-signing key signs installed Play APKs. To permit updates between Play and
GitHub installs, both installed APKs must use the same app-signing certificate;
using the same upload key alone does not guarantee that.

## Release build

Run the **Build signed Android release** workflow against the reviewed tag or
commit. It refuses partial/missing credentials, verifies both signatures, and
uploads renamed APK/AAB artifacts with SHA-256 checksums.
It also rejects a debuggable public APK and runs instrumentation against the
signed release variant on API 35, checking that the APK hash is unchanged.
The private test APK is uploaded separately for physical ARM64 testing with
`physical-smoke.ps1 -BuildType release -SkipBuild -AppApk ... -TestApk ...`.
Physical telemetry is emitted by the test runner; release builds are not made
debuggable and no production export endpoint is added for testing.

For a local signed build, set all four `ANDROID_SIGNING_*` environment variables
documented in `app/build.gradle.kts`, then run:

```text
gradle verifyReleaseSigning testDebugUnitTest lintDebug assembleRelease bundleRelease
```
