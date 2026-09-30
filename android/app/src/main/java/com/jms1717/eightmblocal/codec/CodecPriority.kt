package com.jms1717.eightmblocal.codec

import androidx.media3.common.MimeTypes

/** Desktop-equivalent automatic preference: best working hardware codec first. */
object CodecPriority {
    val qualityOrder = listOf(MimeTypes.VIDEO_AV1, MimeTypes.VIDEO_H265, MimeTypes.VIDEO_H264)

    fun bestHardware(workingHardwareMimes: Set<String>, availableMimes: Set<String>): String =
        qualityOrder.firstOrNull(workingHardwareMimes::contains)
            ?: qualityOrder.firstOrNull(availableMimes::contains)
            ?: MimeTypes.VIDEO_H264
}
