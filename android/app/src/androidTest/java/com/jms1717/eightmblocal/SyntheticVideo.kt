package com.jms1717.eightmblocal

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer

/** Creates personal-media-free input for physical workflow and Play evidence tests. */
object SyntheticVideo {
    fun writeH264(output: File) {
        val width = 320
        val height = 240
        val frameRate = 30
        val frameCount = 90
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)
            setInteger(MediaFormat.KEY_BIT_RATE, 600_000)
            setInteger(MediaFormat.KEY_FRAME_RATE, frameRate)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
        }
        output.delete()
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
                val drained = drain(codec, muxer, info, track, muxerStarted, endOfStream = false)
                track = drained.first
                muxerStarted = drained.second
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
                val drained = drain(codec, muxer, info, track, muxerStarted, endOfStream = true)
                track = drained.first
                muxerStarted = drained.second
                eos = drained.third
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
        repeat(ySize) { buffer.put((48 + (frame * 4) % 160).toByte()) }
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
