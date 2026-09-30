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

Google Play enrollment is separate and is not required for a GitHub APK
release. With Play App Signing, the upload key signs submissions while the
app-signing key signs installed Play APKs. To permit updates between Play and
GitHub installs, both installed APKs must use the same app-signing certificate;
using the same upload key alone does not guarantee that.

## Release build

Run the **Build signed Android release** workflow against the reviewed tag or
commit. It refuses partial/missing credentials, verifies both signatures, and
uploads renamed APK/AAB artifacts with SHA-256 checksums.

For a local signed build, set all four `ANDROID_SIGNING_*` environment variables
documented in `app/build.gradle.kts`, then run:

```text
gradle verifyReleaseSigning testDebugUnitTest lintDebug assembleRelease bundleRelease
```
