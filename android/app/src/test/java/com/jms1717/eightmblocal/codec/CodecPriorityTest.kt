package com.jms1717.eightmblocal.codec

import androidx.media3.common.MimeTypes
import org.junit.Assert.assertEquals
import org.junit.Test

class CodecPriorityTest {
    @Test
    fun prefersAv1ThenHevcThenH264Hardware() {
        val all = setOf(MimeTypes.VIDEO_AV1, MimeTypes.VIDEO_H265, MimeTypes.VIDEO_H264)
        assertEquals(MimeTypes.VIDEO_AV1, CodecPriority.bestHardware(all, all))
        assertEquals(
            MimeTypes.VIDEO_H265,
            CodecPriority.bestHardware(setOf(MimeTypes.VIDEO_H265, MimeTypes.VIDEO_H264), all),
        )
        assertEquals(MimeTypes.VIDEO_H264, CodecPriority.bestHardware(setOf(MimeTypes.VIDEO_H264), all))
    }
}
