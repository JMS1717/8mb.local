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
user. Launching a shortcut starts a localhost-only server and opens the full
web UI in the default browser. Each Windows user keeps their own app data and
outputs under `%LOCALAPPDATA%\8mb.local`.

The app binds only to `127.0.0.1` and disables authentication by default for
that local-only process. Docker authentication behavior is unchanged.

## Build

On Windows with PowerShell, Node.js, Python 3.11+, and (optionally) Inno Setup:

```powershell
.\windows\build.ps1
```

The script builds the frontend, downloads the FFmpeg full build with
libsvtav1, bundles ffmpeg.exe and ffprobe.exe, creates dist\8mblocal.exe with
PyInstaller, and creates dist\8mblocal-Setup.exe when iscc.exe is present.
The upstream full Windows package is GPLv3 and requires 7-Zip for extraction;
preserve its license notices when distributing the executable.

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
