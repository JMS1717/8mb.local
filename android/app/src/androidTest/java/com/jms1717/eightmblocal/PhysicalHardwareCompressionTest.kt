package com.jms1717.eightmblocal

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.ContentValues
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import android.graphics.Bitmap
import android.os.Environment
import android.os.Build
import android.provider.MediaStore
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.core.content.ContextCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.jms1717.eightmblocal.codec.CodecPriority
import com.jms1717.eightmblocal.codec.HardwareCodecSelector
import com.jms1717.eightmblocal.compression.CompressionService
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Fully automatic physical-device proof: synthesize, compress, and verify. */
@RunWith(AndroidJUnit4::class)
@UnstableApi
class PhysicalHardwareCompressionTest {
    @get:Rule val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun syntheticVideoUsesHardwareMediaCodecAndProducesPlayableMp4() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        composeRule.activity.apply {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        val iconResource = context.resources.getResourceName(context.applicationInfo.icon)
        assertTrue("Installed app is not using the 8mb.local launcher icon: $iconResource", iconResource.endsWith(":mipmap/ic_launcher"))
        assumeTrue("Camera-roll workflow test requires Android 10+", Build.VERSION.SDK_INT >= 29)
        val workingCandidates = CodecPriority.qualityOrder.associateWith(HardwareCodecSelector::candidates)
        val workingMimes = workingCandidates.filterValues { it.isNotEmpty() }.keys
        val workingHardwareMimes = workingCandidates.filterValues { candidates -> candidates.any { it.hardware } }.keys
        assumeTrue("Physical workflow requires a working hardware video encoder", workingHardwareMimes.isNotEmpty())
        val automaticMime = CodecPriority.bestHardware(workingHardwareMimes, workingMimes)
        val input = File(context.cacheDir, "physical-hardware-input.mp4")
        input.delete()
        createSyntheticH264(input)
        assertTrue("Synthetic input was not created", input.length() > 1_000)
        val inputUri = context.contentResolver.insert(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, "8mblocal-picker-input.mp4")
                put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4")
                put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DCIM}/8mb.local-test")
            },
        ) ?: error("Could not create picker-style input")
        context.contentResolver.openOutputStream(inputUri, "w")!!.use { output ->
            input.inputStream().use { it.copyTo(output) }
        }
        val outputUri = context.contentResolver.insert(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, "8mblocal-camera-roll-output.mp4")
                put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4")
                put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DCIM}/8mb.local")
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            },
        ) ?: error("Could not create camera-roll output")

        val done = CountDownLatch(1)
        var completed = false
        var failure: String? = null
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (intent?.action != CompressionService.ACTION_STATUS) return
                when (intent.getStringExtra(CompressionService.EXTRA_STATE)) {
                    "completed" -> { completed = true; done.countDown() }
                    "error", "cancelled" -> {
                        failure = intent.getStringExtra(CompressionService.EXTRA_MESSAGE)
                        done.countDown()
                    }
                }
            }
        }
        ContextCompat.registerReceiver(
            context,
            receiver,
            IntentFilter(CompressionService.ACTION_STATUS),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        composeRule.activity.let { activity ->
            ContextCompat.startForegroundService(
                activity,
                Intent(activity, CompressionService::class.java)
                    .putExtra(CompressionService.EXTRA_INPUT, inputUri.toString())
                    .putExtra(CompressionService.EXTRA_OUTPUT, outputUri.toString())
                    .putExtra(CompressionService.EXTRA_MEDIASTORE_OUTPUT, true)
                    .putExtra(CompressionService.EXTRA_TARGET_MB, 1.0)
                    .putExtra(CompressionService.EXTRA_VIDEO_KBPS, 0)
                    .putExtra(CompressionService.EXTRA_VIDEO_MIME, automaticMime)
                    .putExtra(CompressionService.EXTRA_MAX_HEIGHT, 240)
                    .putExtra(CompressionService.EXTRA_AUTO_RESOLUTION, false)
                    .putExtra(CompressionService.EXTRA_MIN_AUTO_HEIGHT, 240)
                    .putExtra(CompressionService.EXTRA_MAX_FPS, 30)
                    .putExtra(CompressionService.EXTRA_AUDIO_KBPS, 64)
                    .putExtra(CompressionService.EXTRA_AUTO_AUDIO_BITRATE, false)
                    .putExtra(CompressionService.EXTRA_AUDIO_MIME, MimeTypes.AUDIO_AAC)
                    .putExtra(CompressionService.EXTRA_KEEP_AUDIO, false)
                    .putExtra(CompressionService.EXTRA_AUDIO_ONLY, false)
                    .putExtra(CompressionService.EXTRA_ALLOW_SOFTWARE_FALLBACK, false)
                    .putExtra(CompressionService.EXTRA_TRIM_START_MS, 0L)
                    .putExtra(CompressionService.EXTRA_TRIM_END_MS, -1L),
            )
        }

        assertTrue("Timed out waiting for foreground-service export", done.await(90, TimeUnit.SECONDS))
        context.unregisterReceiver(receiver)
        assertTrue("Foreground-service compression failed: $failure", failure == null)
        assertTrue("Foreground-service export never completed", completed)
        val outputBytes = context.contentResolver.openFileDescriptor(outputUri, "r")?.use { it.statSize } ?: 0
        assertTrue("Camera-roll output MP4 is empty", outputBytes > 1_000)
        context.contentResolver.query(
            outputUri,
            arrayOf(MediaStore.MediaColumns.RELATIVE_PATH, MediaStore.MediaColumns.IS_PENDING),
            null,
            null,
            null,
        )!!.use { cursor ->
            assertTrue("Camera-roll output is missing", cursor.moveToFirst())
            assertTrue(
                "Output was not saved under DCIM/8mb.local",
                cursor.getString(0).startsWith("${Environment.DIRECTORY_DCIM}/8mb.local"),
            )
            assertTrue("Camera-roll output remained pending", cursor.getInt(1) == 0)
        }
        val report = JSONObject(File(context.filesDir, "last-codec-report.json").readText())
        assertTrue("Software encoder was used: ${report.optString("actual_encoder")}", report.getBoolean("hardware_used"))
        assertTrue(
            "Compression did not use the automatically selected codec: $automaticMime",
            report.getString("requested_mime") == automaticMime,
        )

        composeRule.waitUntil(10_000) {
            composeRule.onAllNodesWithText("Preview & share").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText("Preview & share").performScrollTo().assertIsDisplayed().performClick()
        composeRule.onNodeWithText("Preview compressed video").assertIsDisplayed()
        composeRule.onNodeWithText("Share").assertIsDisplayed()
        File(context.cacheDir, "physical-preview.png").outputStream().use { output ->
            instrumentation.uiAutomation.takeScreenshot().compress(Bitmap.CompressFormat.PNG, 100, output)
        }
        composeRule.onNodeWithText("Close").performClick()

        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(context, outputUri)
            val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: 0L
            assertTrue("Output MP4 has no playable duration", duration > 0)
        } finally {
            retriever.release()
            input.delete()
            context.contentResolver.delete(inputUri, null, null)
            context.contentResolver.delete(outputUri, null, null)
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
