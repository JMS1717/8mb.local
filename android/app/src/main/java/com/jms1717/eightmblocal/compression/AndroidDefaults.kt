package com.jms1717.eightmblocal.compression

import androidx.media3.common.MimeTypes

/** Android equivalents of the desktop application's stock compression defaults. */
object AndroidDefaults {
    const val TARGET_MB = 19.7
    const val VIDEO_KBPS = 2500
    const val AUDIO_KBPS = 128
    const val PRESET = "p4"
    const val AUDIO_MIME = MimeTypes.AUDIO_OPUS
    const val CONTAINER = "mp4"
    const val TUNE = "hq"
    const val AUTO_RESOLUTION = false
    const val AUTO_AUDIO_BITRATE = true
    const val MIN_AUTO_HEIGHT = 240
    const val FAST_MP4_FINALIZE = false
    val SIZE_BUTTONS = listOf(4.0, 5.0, 8.0, 9.7, 19.7, 50.0, 100.0)
}
