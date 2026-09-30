package com.jms1717.eightmblocal.compression

import androidx.media3.common.MimeTypes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class AndroidDefaultsTest {
    @Test
    fun defaultsMatchDesktopApplication() {
        assertEquals(19.7, AndroidDefaults.TARGET_MB, 0.0)
        assertEquals(2500, AndroidDefaults.VIDEO_KBPS)
        assertEquals(128, AndroidDefaults.AUDIO_KBPS)
        assertEquals("p4", AndroidDefaults.PRESET)
        assertEquals(MimeTypes.AUDIO_OPUS, AndroidDefaults.AUDIO_MIME)
        assertEquals("mp4", AndroidDefaults.CONTAINER)
        assertEquals("hq", AndroidDefaults.TUNE)
        assertFalse(AndroidDefaults.AUTO_RESOLUTION)
        assertFalse(AndroidDefaults.FAST_MP4_FINALIZE)
        assertEquals(true, AndroidDefaults.AUTO_AUDIO_BITRATE)
        assertEquals(240, AndroidDefaults.MIN_AUTO_HEIGHT)
        assertEquals(listOf(4.0, 5.0, 8.0, 9.7, 19.7, 50.0, 100.0), AndroidDefaults.SIZE_BUTTONS)
    }
}
