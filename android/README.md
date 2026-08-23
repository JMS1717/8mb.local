# 8mb.local for Android

The Android port is a standalone native Kotlin/Jetpack Compose app. It uses
AndroidX Media3 Transformer and the platform MediaCodec stack; it does not
embed Python, Docker, a WebView, or a bundled FFmpeg executable.

## Desktop workflow parity

- Android Photo Picker for one video or an automatic multi-video batch
- Desktop stock defaults: 19.7 MB size mode, 2500 kbps direct-bitrate mode,
  Opus audio at 128 kbps, balanced/P4 intent, MP4, source resolution and frame
  rate, auto resolution off, and software fallback on
- The same seven size buttons, direct video bitrate, H.264/HEVC/AV1 selection,
  audio mute/Opus/AAC and all desktop audio bitrate choices
- Source/240p/360p/480p/720p/1080p/1440p/2160p output, the desktop
  auto-resolution heuristic and minimum height, and frame-rate caps through
  120 fps
- Automatic audio-bitrate downshift, start/end trim, audio-only M4A extraction,
  cancel, progress, recent history, in-app output preview, and share
- Automatic output to the camera roll at `DCIM/8mb.local` or to
  `Music/8mb.local`, with an optional Android Save As picker

Android uses vendor MediaCodec controls in place of backend-specific flags.
NVENC-only tune names, FFmpeg P1-P7 command-line switches, MKV muxing, folder
watch, and server administration do not have honest on-device Media3
equivalents. Hardware decode is selected automatically by Media3, and Android's
native MP4 muxer replaces the desktop fast-finalize switch.

## Hardware behavior

Codec inventory alone is not trusted. Each video encoder candidate is
configured and started before it is offered. An export pins one encoder, tries
working hardware codecs in AV1, HEVC, then H.264 quality order, and only then
tries software codecs when that fallback is enabled. The best working hardware
encoder is selected automatically when the scan completes. Encode and decode
capabilities are shown separately. Completion telemetry records the encoder
Media3 actually used. Size-mode outputs over target by more than 2 percent
receive one bitrate-adjusted retry.

## Automated tests

Build with JDK 17, Android SDK 36, and Gradle 8.13:

```text
cd android
gradlew testDebugUnitTest lintDebug assembleDebug assembleRelease bundleRelease assembleDebugAndroidTest
```

With an ARM64 Android device connected through ADB, run:

```powershell
.\physical-smoke.ps1 -Serial '<adb-serial>'
```

The script builds and installs both APKs, synthesizes its own WAV and H.264
inputs, proves a playable desktop-default Opus audio export, and sends a
MediaStore content URI through the real foreground service for a hardware-only
H.264 compression. It verifies playback, camera-roll publication, and actual
encoder telemetry, then opens the app's preview UI before Share. It also checks
the installed launcher-icon resource. It requires no taps and never reads
personal media. The generated `physical-codec-report.json` is local and ignored
by Git.
