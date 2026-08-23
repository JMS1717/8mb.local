# Android release signing

Debug APKs and unsigned release artifacts are never public releases. Google
Play and direct APK distribution use one permanent upload/signing identity.

## One-time setup

1. Create an upload key offline with Android Studio or `keytool`. Back up the
   keystore and its passwords in a password manager before uploading anything.
2. Enroll the app in Google Play App Signing using package name
   `com.jms1717.eightmblocal`.
3. Add these GitHub Actions repository secrets:

   - `ANDROID_UPLOAD_KEYSTORE_BASE64`: base64-encoded keystore bytes
   - `ANDROID_UPLOAD_STORE_PASSWORD`
   - `ANDROID_UPLOAD_KEY_ALIAS`
   - `ANDROID_UPLOAD_KEY_PASSWORD`

Do not commit the keystore, passwords, or a populated `keystore.properties`.

## Release build

Run the **Build signed Android release** workflow against the reviewed tag or
commit. It refuses partial/missing credentials, verifies both signatures, and
uploads renamed APK/AAB artifacts with SHA-256 checksums.

For a local signed build, set all four `ANDROID_SIGNING_*` environment variables
documented in `app/build.gradle.kts`, then run:

```text
gradle verifyReleaseSigning testDebugUnitTest lintDebug assembleRelease bundleRelease
```
