package com.jms1717.eightmblocal.codec

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Build
import androidx.annotation.RequiresApi
import androidx.media3.common.util.UnstableApi
import androidx.media3.transformer.EncoderSelector
import com.google.common.collect.ImmutableList

data class CodecCandidate(
    val info: MediaCodecInfo,
    val mimeType: String,
    val hardware: Boolean,
) {
    val name: String get() = info.name
}

/** Discovers, starts, and prioritizes the device's actual MediaCodec encoders. */
@UnstableApi
object HardwareCodecSelector {
    fun candidates(mimeType: String): List<CodecCandidate> {
        val seen = mutableSetOf<String>()
        return MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos
            .asSequence()
            .filter { it.isEncoder && it.supportedTypes.any { type -> type.equals(mimeType, true) } }
            .map { CodecCandidate(it, mimeType, isHardware(it)) }
            .filter { seen.add(it.name) }
            .sortedWith(compareByDescending<CodecCandidate> { it.hardware }.thenBy { it.name })
            .filter(::canConfigureAndStart)
            .toList()
    }

    fun encoderSelector(candidate: CodecCandidate): EncoderSelector = EncoderSelector { requested ->
        if (requested.equals(candidate.mimeType, true)) {
            ImmutableList.of(candidate.info)
        } else {
            ImmutableList.of()
        }
    }

    /** Hardware decoder components exposed by the OEM's MediaCodec stack. */
    fun hardwareDecoderNames(mimeType: String): List<String> =
        MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos
            .asSequence()
            .filter { !it.isEncoder && isHardware(it) }
            .filter { it.supportedTypes.any { type -> type.equals(mimeType, true) } }
            .map { it.name }
            .distinct()
            .sorted()
            .toList()

    fun isHardwareName(name: String): Boolean {
        val info = MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos.firstOrNull { it.name == name }
        return info?.let(::isHardware) ?: false
    }

    private fun canConfigureAndStart(candidate: CodecCandidate): Boolean {
        var codec: MediaCodec? = null
        var started = false
        return try {
            val format = MediaFormat.createVideoFormat(candidate.mimeType, 128, 128).apply {
                setInteger(MediaFormat.KEY_BIT_RATE, 256_000)
                setInteger(MediaFormat.KEY_FRAME_RATE, 24)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
                setInteger(
                    MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface,
                )
            }
            codec = MediaCodec.createByCodecName(candidate.name)
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            codec.createInputSurface().release()
            codec.start()
            started = true
            true
        } catch (_: Exception) {
            false
        } finally {
            if (started) runCatching { codec?.stop() }
            runCatching { codec?.release() }
        }
    }

    private fun isHardware(info: MediaCodecInfo): Boolean {
        if (Build.VERSION.SDK_INT >= 29) return isHardwareApi29(info)
        val name = info.name.lowercase()
        return listOf("omx.google.", "c2.android.", "c2.google.", "sw", "software")
            .none(name::contains)
    }

    @RequiresApi(29)
    private fun isHardwareApi29(info: MediaCodecInfo): Boolean = info.isHardwareAccelerated
}
