# Native Windows desktop app

The Windows build is the same 8mb.local web application used in Docker: the
Svelte UI, FastAPI routes, worker task code, hardware probes, history, queue,
and FFmpeg command construction are shared. Docker supplies Redis/Celery; the
desktop build uses a bounded in-process queue so it does not need Docker,
Redis, Python, or a separate service.

## Install

Download `8mblocal-Setup.exe` from the GitHub Actions artifact and run it. The
default install requests administrator permission, installs the executable for
all users under `Program Files\8mb.local`, and creates all-users Start Menu and
Desktop shortcuts. The installer also offers a current-user mode for machines
where administrator permission is unavailable; that mode installs under the
user's local application programs folder and creates shortcuts only for that
user. Launching a shortcut opens the full interface in a normal Windows
WebView2 application window; the local API, FFmpeg, and hardware probes run
without visible terminal windows. Pass `--browser` to the portable executable
to use the default browser instead. Each Windows user keeps their own app data
and outputs under `%LOCALAPPDATA%\8mb.local`.

For most Windows users, the Microsoft Store MSIX is the recommended install:
Microsoft signs the certified package, installation does not require an
administrator, updates are automatic, and uninstall is handled by Windows.
The portable executable and Inno Setup installer remain available for offline,
Store-disabled, and all-users installations. All three variants share and
preserve the same `%LOCALAPPDATA%\8mb.local` user data directory.

The app binds only to `127.0.0.1` and disables authentication by default for
that local-only process. The native window uses Microsoft's Edge WebView2
runtime. If WebView2 is unavailable, the app shows a startup error and stops;
install WebView2 or explicitly launch `8mblocal.exe --browser`. Docker
authentication behavior is unchanged.

## Windows security message

The GitHub `8mblocal.exe` and `8mblocal-Setup.exe` builds are currently
unsigned. Windows SmartScreen may therefore show **Windows protected your PC**
or identify the publisher as unknown. This warning means Windows cannot verify
a paid code-signing identity or reputation for that downloaded file; it does
not mean Defender detected malware. Download releases only from the official
`JMS1717/8mb.local` repository and compare the published SHA-256 checksum before
choosing **More info → Run anyway**. The Microsoft Store MSIX is submitted
unsigned and is signed by Microsoft after certification, so Store installs do
not rely on the unsigned GitHub executable's reputation.

## Build

The active version comes from the root `VERSION` file. For the complete local
release workflow, use the versioned command from the repository root:

```powershell
.\release-local.ps1 -Version 141.0.0.0
```

For only the native Windows build, use Windows PowerShell with Node.js,
Python 3.11+, and (optionally) Inno Setup:

```powershell
.\windows\build.ps1
```

The script builds the frontend, downloads the FFmpeg full build with
libsvtav1, bundles ffmpeg.exe and ffprobe.exe, creates dist\8mblocal.exe with
PyInstaller, and creates dist\8mblocal-Setup.exe when iscc.exe is present.
The upstream full Windows package is GPLv3 and requires 7-Zip for extraction;
preserve its license notices when distributing the executable.

After building the executable, create an unsigned package for Microsoft Store
submission with:

```powershell
.\windows\build-msix.ps1 `
  -PackageIdentityName '<Partner Center package identity name>' `
  -Publisher '<Partner Center publisher value>' `
  -PublisherDisplayName '<Partner Center display name>' `
  -StoreSubmission
```

Copy the identity and publisher values exactly from Partner Center after
reserving the app name. The currently configured Partner Center identity is
`jms1717.8mb.local`, publisher
`CN=AAE66F20-996E-4A3C-B08E-182952BAD9F7`, and display name `jms1717`.
The script downloads the command-line Windows SDK build
tools into a per-user build cache when `MakeAppx.exe` is not already installed.
`-StoreSubmission` rejects the development placeholder identity so a CI test
package cannot be uploaded accidentally. The resulting
`dist\8mblocal_<version>_x64.msix` is intentionally unsigned;
Microsoft signs it after Store certification. For a local structural build,
omit the identity arguments to use clearly marked development placeholders.

The MSIX deliberately omits `unvirtualizedResources` and
`FileSystemWriteVirtualization`. It retains only the `runFullTrust`
capability required by the current packaged Win32 architecture, which launches
the local Python/FastAPI runtime, FFmpeg/FFprobe, and WebView2. The package is
still unsigned locally; Microsoft signs the submitted package after Store
certification.

## Temporary media and Folder Watch

Native Windows uploads use normal filesystem paths. In `MEDIA_STORAGE=auto`
or `memory` mode, transient upload files receive the Windows
`FILE_ATTRIBUTE_TEMPORARY` cache hint so Windows can prefer keeping them in
memory while still allowing safe disk spill under pressure. This is
RAM-preferred temporary storage, not a guaranteed RAM disk and does not require
ImDisk, WinFsp, a driver, pipes, or whole-file Python buffers. `disk` mode
disables the hint. The configured `MEDIA_MEMORY_LIMIT_GB` budget and admission
checks protect the host, and temporary API inputs are removed after encoding,
retries, fallback, and validation finish.

The **Folder Watch (Advanced)** panel is at the bottom of Settings and is
collapsed by default. It can watch a local, UNC, or mounted Linux folder and
uses the normal compression queue. **Stable seconds** is the quiet period: the
file size and modified time must remain unchanged for that many seconds before
processing begins. It is not the video's duration or total encode time. Five
seconds is a good local default; use a longer value for slower network copies.
The polling interval is separate. Folder Watch remains disabled until enabled
and saved by the user.

For development without packaging:

```powershell
py -3 windows\desktop_app.py --no-browser --port 8001
```

Then open `http://127.0.0.1:8001`.

To validate a release rather than only checking that the process starts, run:

```powershell
.\windows\test-release.ps1 -Build -Install
```

This generates a real test video with the bundled FFmpeg, checks health and
the frontend shell, uploads and compresses the video, polls the job, downloads
the output, and validates it with `ffprobe`. `-Install` tests the selected
installer mode, verifies the Desktop shortcut points at the installed
executable, and removes that temporary installation afterward. It defaults to
the all-users path; pass `-InstallMode current-user` to test the no-admin path.
Pass `-UseDefaultInstallDir` to test the real default install directory instead
of the isolated test directory.
Use
`-KeepData` to retain smoke-test logs and media for diagnosis.

## Hardware behavior

At startup the worker performs real one-frame initialization probes, not just
an `ffmpeg -encoders` listing. It tests NVIDIA NVENC, Intel Quick Sync, AMD
AMF on Windows, Linux VAAPI, and CPU fallbacks. A driver that disappears
between the probe and a real job is retried once and then falls back to CPU.

The native executable also keeps the CLI compressor in `windows\8mblocal.py`
for scripted use; the installed desktop product is the web-app executable
described above.
