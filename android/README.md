# 8mb.local for Android

This is a standalone native Kotlin/Jetpack Compose app. It uses AndroidX
Media3 Transformer and the platform MediaCodec stack; it does not embed
Python, Docker, a WebView, or FFmpeg.

The app supports one job at a time, H.264/HEVC/AV1 when the device exposes a
working encoder, AAC audio in MP4, target/custom size, optional trim and
resolution cap, progress, cancel, Room-backed history, Storage Access
Framework save, and Android sharing. Batch jobs, MKV/Opus, folder watch, and
remote-server control remain desktop/server features.

Codec inventory alone is not trusted. Each candidate is configured with an
input surface and started before it is offered. An export pins one encoder,
tries each working hardware codec first, and only then tries software codecs.
The completion report records the encoder Media3 actually used. Outputs over
the target by more than 2 percent receive one bitrate-adjusted retry.

Build with JDK 17, Android SDK 36, and Gradle 8.13:

```text
cd android
gradle test lint assembleDebug assembleRelease bundleRelease
```

For the physical smoke test, connect a device with USB debugging, build the
debug APK, then run `./physical-smoke.ps1`. The script installs and opens the
app, waits for one manual compression/playback check, and retrieves
`physical-codec-report.json` containing `actual_encoder`, `hardware_used`, and
fallback state. The script fails if the completed export did not actually use
a hardware MediaCodec.
