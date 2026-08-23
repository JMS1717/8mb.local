# Windows release signing

The Windows workflow defaults to `unsigned-validation`, which fully builds,
installs, and exercises both native x64 and ARM64 packages. For public EXE
distribution, choose `require-authenticode` and configure:

- `WINDOWS_CODE_SIGNING_PFX_BASE64`
- `WINDOWS_CODE_SIGNING_PFX_PASSWORD`

The build signs and verifies both the portable executable and Inno Setup
installer. The Microsoft Store-submission MSIX intentionally remains unsigned
because the Store applies its distribution signature.

Never commit the PFX or its password.
