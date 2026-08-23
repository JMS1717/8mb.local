# macOS release signing

Pull-request and ordinary manual builds use ad-hoc signing for architecture and
runtime validation. Public distribution outside the Mac App Store must use the
`require-developer-id` workflow option and these repository secrets:

- `MACOS_DEVELOPER_ID_P12_BASE64`
- `MACOS_DEVELOPER_ID_P12_PASSWORD`
- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_PASSWORD`

Export the Developer ID Application certificate and private key as a password-
protected P12, base64-encode the file, and store only the encoded value in the
repository secret. The workflow imports it into a temporary keychain, signs the
app with the hardened runtime, submits the DMG to Apple, staples the ticket, and
verifies Gatekeeper acceptance. It deletes the temporary keychain afterward.

Never commit the P12, its password, or the app-specific Apple password.
