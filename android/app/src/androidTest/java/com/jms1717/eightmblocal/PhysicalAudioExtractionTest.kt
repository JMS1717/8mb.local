package com.jms1717.eightmblocal

import android.content.ContentValues
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.jms1717.eightmblocal.compression.AndroidDefaults
import com.jms1717.eightmblocal.compression.CompressionEngine
import com.jms1717.eightmblocal.compression.CompressionListener
import com.jms1717.eightmblocal.compression.CompressionRequest
import com.jms1717.eightmblocal.compression.CompressionResult
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.PI
import kotlin.math.sin

/** Proves that the desktop-default Opus audio path works without user media. */
@RunWith(AndroidJUnit4::class)
@UnstableApi
class PhysicalAudioExtractionTest {
    @Test
    fun syntheticWaveExtractsToPlayableOpusM4a() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val input = File(context.cacheDir, "physical-audio-input.wav")
        assumeTrue("MediaStore smoke requires Android 10+", Build.VERSION.SDK_INT >= 29)
        input.delete()
        createWave(input)
        val output = context.contentResolver.insert(
            MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
            ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, "8mblocal-automated-audio-smoke.m4a")
                put(MediaStore.MediaColumns.MIME_TYPE, "audio/mp4")
                put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_MUSIC}/8mb.local")
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            },
        ) ?: error("Could not create the automated MediaStore output")
        val done = CountDownLatch(1)
        var completed: CompressionResult? = null
        var failure: String? = null
        val listener = object : CompressionListener {
            override fun onProgress(percent: Int, encoder: String, hardware: Boolean) = Unit
            override fun onComplete(result: CompressionResult) { completed = result; done.countDown() }
            override fun onError(message: String) { failure = message; done.countDown() }
            override fun onCancelled() { failure = "Audio extraction was cancelled"; done.countDown() }
        }
        instrumentation.runOnMainSync {
            CompressionEngine(context, listener).start(
                CompressionRequest(
                    inputUri = Uri.fromFile(input),
                    outputUri = output,
                    targetMb = AndroidDefaults.TARGET_MB,
                    videoBitrateOverride = null,
                    videoMime = MimeTypes.VIDEO_H264,
                    maxHeight = null,
                    autoResolution = false,
                    minAutoHeight = AndroidDefaults.MIN_AUTO_HEIGHT,
                    maxFps = null,
                    audioBitrate = AndroidDefaults.AUDIO_KBPS * 1000,
                    autoAudioBitrate = AndroidDefaults.AUTO_AUDIO_BITRATE,
                    audioMime = AndroidDefaults.AUDIO_MIME,
                    keepAudio = true,
                    audioOnly = true,
                    allowSoftwareFallback = true,
                    trimStartMs = 0,
                    trimEndMs = null,
                ),
            )
        }
        assertTrue("Timed out waiting for audio export", done.await(60, TimeUnit.SECONDS))
        assertTrue("Opus audio extraction failed: $failure", failure == null)
        assertNotNull("Audio extraction returned no result", completed)
        val outputBytes = context.contentResolver.openFileDescriptor(output, "r")?.use { it.statSize } ?: 0
        assertTrue("Audio output is empty", outputBytes > 500)
        context.contentResolver.update(
            output,
            ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) },
            null,
            null,
        )
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(context, output)
            val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0
            assertTrue("Audio output has no playable duration", duration > 0)
        } finally {
            retriever.release()
            input.delete()
            context.contentResolver.delete(output, null, null)
        }
    }

    private fun createWave(output: File) {
        val sampleRate = 48_000
        val samples = sampleRate
        val dataSize = samples * 2
        val bytes = ByteBuffer.allocate(44 + dataSize).order(ByteOrder.LITTLE_ENDIAN)
        bytes.put("RIFF".toByteArray()).putInt(36 + dataSize).put("WAVE".toByteArray())
        bytes.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
        bytes.putInt(sampleRate).putInt(sampleRate * 2).putShort(2).putShort(16)
        bytes.put("data".toByteArray()).putInt(dataSize)
        repeat(samples) { sample ->
            bytes.putShort((sin(2.0 * PI * 440.0 * sample / sampleRate) * 8_000).toInt().toShort())
        }
        output.writeBytes(bytes.array())
    }
}
