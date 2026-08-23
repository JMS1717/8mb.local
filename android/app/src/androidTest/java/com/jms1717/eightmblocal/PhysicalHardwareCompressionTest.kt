package com.jms1717.eightmblocal

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import android.net.Uri
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.jms1717.eightmblocal.compression.CompressionEngine
import com.jms1717.eightmblocal.compression.CompressionListener
import com.jms1717.eightmblocal.compression.CompressionRequest
import com.jms1717.eightmblocal.compression.CompressionResult
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Fully automatic physical-device proof: synthesize, compress, and verify. */
@RunWith(AndroidJUnit4::class)
@UnstableApi
class PhysicalHardwareCompressionTest {
    @Test
    fun syntheticVideoUsesHardwareMediaCodecAndProducesPlayableMp4() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val input = File(context.cacheDir, "physical-hardware-input.mp4")
        val output = File(context.cacheDir, "physical-hardware-output.mp4")
        input.delete()
        output.delete()
        createSyntheticH264(input)
        assertTrue("Synthetic input was not created", input.length() > 1_000)

        val done = CountDownLatch(1)
        var completed: CompressionResult? = null
        var failure: String? = null
        val listener = object : CompressionListener {
            override fun onProgress(percent: Int, encoder: String, hardware: Boolean) = Unit
            override fun onComplete(result: CompressionResult) {
                completed = result
                done.countDown()
            }
            override fun onError(message: String) {
                failure = message
                done.countDown()
            }
            override fun onCancelled() {
                failure = "Compression was cancelled"
                done.countDown()
            }
        }

        instrumentation.runOnMainSync {
            CompressionEngine(context, listener).start(
                CompressionRequest(
                    inputUri = Uri.fromFile(input),
                    outputUri = Uri.fromFile(output),
                    targetMb = 1.0,
                    videoBitrateOverride = null,
                    videoMime = MimeTypes.VIDEO_H264,
                    maxHeight = 240,
                    autoResolution = false,
                    minAutoHeight = 240,
                    maxFps = 30,
                    audioBitrate = 0,
                    autoAudioBitrate = false,
                    audioMime = MimeTypes.AUDIO_AAC,
                    keepAudio = false,
                    audioOnly = false,
                    allowSoftwareFallback = false,
                    trimStartMs = 0,
                    trimEndMs = null,
                ),
            )
        }

        assertTrue("Timed out waiting for Media3 export", done.await(90, TimeUnit.SECONDS))
        assertTrue("Hardware compression failed: $failure", failure == null)
        val result = completed
        assertNotNull("Compression did not return telemetry", result)
        assertTrue("Software encoder was used: ${result?.actualEncoder}", result?.hardwareUsed == true)
        assertTrue("Output MP4 is empty", output.length() > 1_000)

        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(output.absolutePath)
            val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: 0L
            assertTrue("Output MP4 has no playable duration", duration > 0)
        } finally {
            retriever.release()
            input.delete()
            output.delete()
        }
    }

    private fun createSyntheticH264(output: File) {
        val width = 320
        val height = 240
        val frameRate = 30
        val frameCount = 45
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)
            setInteger(MediaFormat.KEY_BIT_RATE, 600_000)
            setInteger(MediaFormat.KEY_FRAME_RATE, frameRate)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
        }
        val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        val muxer = MediaMuxer(output.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        var track = -1
        var muxerStarted = false
        val info = MediaCodec.BufferInfo()
        try {
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            codec.start()
            repeat(frameCount) { frame ->
                val inputIndex = codec.dequeueInputBuffer(10_000)
                check(inputIndex >= 0) { "Synthetic encoder did not provide an input buffer" }
                val buffer = codec.getInputBuffer(inputIndex) ?: error("Synthetic input buffer is missing")
                fillYuv420(buffer, width, height, frame)
                codec.queueInputBuffer(
                    inputIndex,
                    0,
                    width * height * 3 / 2,
                    frame * 1_000_000L / frameRate,
                    0,
                )
                val drain = drain(codec, muxer, info, track, muxerStarted, endOfStream = false)
                track = drain.first
                muxerStarted = drain.second
            }
            val endIndex = codec.dequeueInputBuffer(10_000)
            check(endIndex >= 0) { "Synthetic encoder did not accept end-of-stream" }
            codec.queueInputBuffer(
                endIndex,
                0,
                0,
                frameCount * 1_000_000L / frameRate,
                MediaCodec.BUFFER_FLAG_END_OF_STREAM,
            )
            var eos = false
            while (!eos) {
                val drain = drain(codec, muxer, info, track, muxerStarted, endOfStream = true)
                track = drain.first
                muxerStarted = drain.second
                eos = drain.third
            }
        } finally {
            runCatching { codec.stop() }
            codec.release()
            if (muxerStarted) runCatching { muxer.stop() }
            muxer.release()
        }
    }

    private fun fillYuv420(buffer: ByteBuffer, width: Int, height: Int, frame: Int) {
        buffer.clear()
        val ySize = width * height
        val luma = (48 + (frame * 4) % 160).toByte()
        repeat(ySize) { buffer.put(luma) }
        repeat(ySize / 4) { buffer.put((96 + frame % 32).toByte()) }
        repeat(ySize / 4) { buffer.put((160 - frame % 32).toByte()) }
    }

    private fun drain(
        codec: MediaCodec,
        muxer: MediaMuxer,
        info: MediaCodec.BufferInfo,
        initialTrack: Int,
        initiallyStarted: Boolean,
        endOfStream: Boolean,
    ): Triple<Int, Boolean, Boolean> {
        var track = initialTrack
        var started = initiallyStarted
        var eos = false
        while (true) {
            val outputIndex = codec.dequeueOutputBuffer(info, if (endOfStream) 10_000 else 0)
            when {
                outputIndex == MediaCodec.INFO_TRY_AGAIN_LATER -> return Triple(track, started, eos)
                outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    check(!started) { "Synthetic muxer format changed twice" }
                    track = muxer.addTrack(codec.outputFormat)
                    muxer.start()
                    started = true
                }
                outputIndex >= 0 -> {
                    val outputBuffer = codec.getOutputBuffer(outputIndex)
                        ?: error("Synthetic output buffer is missing")
                    if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) info.size = 0
                    if (info.size > 0) {
                        check(started) { "Synthetic samples arrived before the muxer format" }
                        outputBuffer.position(info.offset)
                        outputBuffer.limit(info.offset + info.size)
                        muxer.writeSampleData(track, outputBuffer, info)
                    }
                    eos = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                    codec.releaseOutputBuffer(outputIndex, false)
                    if (eos) return Triple(track, started, true)
                }
            }
        }
    }
}
