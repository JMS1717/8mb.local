# Native Apple Silicon build

8mb.local uses the same Svelte/FastAPI/local-worker application as Windows,
wrapped in pywebview's native Cocoa/WebKit window. User data is stored under
`~/Library/Application Support/8mb.local`; completed downloads use the current
user's Downloads folder.

The release target is native ARM64 on macOS 14 or later. Run `bash
macos/build.sh` on Apple Silicon with Node 20, Python 3.11-3.13, Homebrew
FFmpeg, and Xcode command-line tools installed. The build refuses to continue
unless FFmpeg both lists VideoToolbox and successfully performs a one-frame
H.264 hardware encode with software fallback disabled.

By default the app is ad-hoc signed. Set `MACOS_SIGNING_IDENTITY` to a Developer
ID Application identity for a signed build. If `APPLE_NOTARY_PROFILE` names a
notarytool keychain profile, the DMG is also submitted and stapled.
