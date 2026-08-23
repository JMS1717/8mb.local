package com.jms1717.eightmblocal.compression

import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.media3.common.Effect
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.Presentation
import androidx.media3.transformer.Composition
import androidx.media3.transformer.AudioEncoderSettings
import androidx.media3.transformer.DefaultEncoderFactory
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.ProgressHolder
import androidx.media3.transformer.Transformer
import androidx.media3.transformer.VideoEncoderSettings
import com.jms1717.eightmblocal.codec.CodecCandidate
import com.jms1717.eightmblocal.codec.HardwareCodecSelector
import org.json.JSONObject
import java.io.File
import kotlin.math.max

data class CompressionRequest(
    val inputUri: Uri,
    val outputUri: Uri,
    val targetMb: Double,
    val videoBitrateOverride: Int?,
    val videoMime: String,
    val maxHeight: Int?,
    val autoResolution: Boolean,
    val minAutoHeight: Int,
    val maxFps: Int?,
    val audioBitrate: Int,
    val autoAudioBitrate: Boolean,
    val audioMime: String,
    val keepAudio: Boolean,
    val audioOnly: Boolean,
    val allowSoftwareFallback: Boolean,
    val trimStartMs: Long,
    val trimEndMs: Long?,
)

data class CompressionResult(
    val outputUri: Uri,
    val actualBytes: Long,
    val requestedMime: String,
    val actualEncoder: String,
    val hardwareUsed: Boolean,
    val fallbackOccurred: Boolean,
    val bitrateRetried: Boolean,
)

interface CompressionListener {
    fun onProgress(percent: Int, encoder: String, hardware: Boolean)
    fun onComplete(result: CompressionResult)
    fun onError(message: String)
    fun onCancelled()
}

/** One-job Media3 exporter with hardware-first codec and size fallback logic. */
@UnstableApi
class CompressionEngine(
    private val context: Context,
    private val listener: CompressionListener,
) {
    private val handler = Handler(Looper.getMainLooper())
    private var transformer: Transformer? = null
    private var cancelled = false
    private var activeTemp: File? = null

    fun start(request: CompressionRequest) {
        check(Looper.myLooper() == Looper.getMainLooper())
        cancelled = false
        val media = mediaProperties(request.inputUri)
        if (request.audioOnly) {
            attemptAudio(request, media.durationMs)
            return
        }
        val candidates = HardwareCodecSelector.candidates(request.videoMime).filter {
            request.allowSoftwareFallback || it.hardware
        }
        if (candidates.isEmpty()) {
            listener.onError(
                if (request.allowSoftwareFallback) {
                    "No working encoder can configure and start for ${request.videoMime}"
                } else {
                    "No working hardware encoder is available for ${request.videoMime}"
                },
            )
            return
        }
        val durationMs = media.durationMs
        val effectiveDurationMs = (
            (request.trimEndMs ?: durationMs).coerceAtMost(durationMs) - request.trimStartMs
        ).coerceAtLeast(1)
        val effectiveAudioBitrate = if (
            request.keepAudio && request.autoAudioBitrate && request.videoBitrateOverride == null
        ) {
            AutoAudioPlanner.chooseKbps(request.targetMb, effectiveDurationMs, request.audioBitrate / 1000) * 1000
        } else request.audioBitrate
        val bitrate = request.videoBitrateOverride ?: SizePlanner.videoBitrate(
            request.targetMb,
            effectiveDurationMs,
            if (request.keepAudio) effectiveAudioBitrate else 0,
        )
        val effectiveHeight = if (request.autoResolution) {
            AutoResolutionPlanner.chooseHeight(media.width, media.height, bitrate / 1000.0, request.minAutoHeight)
        } else request.maxHeight
        attempt(
            request.copy(audioBitrate = effectiveAudioBitrate, maxHeight = effectiveHeight),
            candidates,
            candidateIndex = 0,
            bitrate = bitrate,
            bitrateRetry = false,
        )
    }

    fun cancel() {
        cancelled = true
        transformer?.cancel()
        transformer = null
        handler.removeCallbacksAndMessages(null)
        activeTemp?.delete()
        listener.onCancelled()
    }

    private fun attemptAudio(request: CompressionRequest, durationMs: Long) {
        val effectiveDurationMs = (
            (request.trimEndMs ?: durationMs).coerceAtMost(durationMs) - request.trimStartMs
        ).coerceAtLeast(1)
        val effectiveAudioBitrate = if (request.autoAudioBitrate) {
            AutoAudioPlanner.chooseKbps(request.targetMb, effectiveDurationMs, request.audioBitrate / 1000) * 1000
        } else request.audioBitrate
        val resolvedRequest = request.copy(audioBitrate = effectiveAudioBitrate)
        val temp = File.createTempFile("8mblocal-audio-", ".m4a", context.cacheDir).also {
            it.delete()
            activeTemp = it
        }
        val encoderFactory = DefaultEncoderFactory.Builder(context)
            .setRequestedAudioEncoderSettings(
                AudioEncoderSettings.Builder().setBitrate(effectiveAudioBitrate).build(),
            )
            .setEnableFallback(true)
            .build()
        val exportListener = object : Transformer.Listener {
            override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                handler.removeCallbacksAndMessages(null)
                val actualName = exportResult.audioEncoderName ?: "Android audio encoder"
                val bytes = temp.length()
                try {
                    context.contentResolver.openOutputStream(request.outputUri, "w")?.use { output ->
                        temp.inputStream().use { input -> input.copyTo(output) }
                    } ?: error("Could not open the selected output document")
                    val result = CompressionResult(
                        request.outputUri,
                        bytes,
                        request.audioMime,
                        actualName,
                        HardwareCodecSelector.isHardwareName(actualName),
                        fallbackOccurred = false,
                        bitrateRetried = false,
                    )
                    writeDiagnostic(resolvedRequest, result, listOf(actualName))
                    listener.onComplete(result)
                } catch (error: Exception) {
                    listener.onError("Could not save output: ${error.message}")
                } finally {
                    temp.delete()
                    activeTemp = null
                    transformer = null
                }
            }

            override fun onError(
                composition: Composition,
                exportResult: ExportResult,
                exportException: ExportException,
            ) {
                handler.removeCallbacksAndMessages(null)
                temp.delete()
                transformer = null
                listener.onError("Audio export failed: ${exportException.message}")
            }
        }
        transformer = Transformer.Builder(context)
            .setAudioMimeType(request.audioMime)
            .setEncoderFactory(encoderFactory)
            .addListener(exportListener)
            .build()
        val clip = MediaItem.ClippingConfiguration.Builder()
            .setStartPositionMs(request.trimStartMs)
            .apply { request.trimEndMs?.let(::setEndPositionMs) }
            .build()
        val edited = EditedMediaItem.Builder(
            MediaItem.Builder().setUri(request.inputUri).setClippingConfiguration(clip).build(),
        ).setRemoveVideo(true).build()
        listener.onProgress(0, "Android audio encoder", false)
        transformer?.start(edited, temp.absolutePath)
        pollProgress("Android audio encoder", false)
    }

    private fun attempt(
        request: CompressionRequest,
        candidates: List<CodecCandidate>,
        candidateIndex: Int,
        bitrate: Int,
        bitrateRetry: Boolean,
    ) {
        if (cancelled) return
        if (candidateIndex >= candidates.size) {
            listener.onError("Every hardware and software encoder failed to start the export")
            return
        }
        val candidate = candidates[candidateIndex]
        val temp = File.createTempFile("8mblocal-", ".mp4", context.cacheDir).also {
            it.delete()
            activeTemp = it
        }
        val encoderFactoryBuilder = DefaultEncoderFactory.Builder(context)
            .setVideoEncoderSelector(HardwareCodecSelector.encoderSelector(candidate))
            .setRequestedVideoEncoderSettings(
                VideoEncoderSettings.Builder().setBitrate(max(64_000, bitrate)).build(),
            )
            .setEnableFallback(false)
        if (request.keepAudio) {
            encoderFactoryBuilder.setRequestedAudioEncoderSettings(
                AudioEncoderSettings.Builder().setBitrate(request.audioBitrate).build(),
            )
        }
        val encoderFactory = encoderFactoryBuilder.build()

        val exportListener = object : Transformer.Listener {
            override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                handler.removeCallbacksAndMessages(null)
                val actualName = exportResult.videoEncoderName ?: candidate.name
                val bytes = temp.length()
                val targetBytes = (request.targetMb * 1024.0 * 1024.0).toLong()
                if (request.videoBitrateOverride == null &&
                    !bitrateRetry && SizePlanner.exceedsTolerance(bytes, targetBytes)
                ) {
                    val adjusted = SizePlanner.adjustedBitrate(bitrate, targetBytes, bytes)
                    temp.delete()
                    attempt(request, candidates, candidateIndex, adjusted, bitrateRetry = true)
                    return
                }
                try {
                    context.contentResolver.openOutputStream(request.outputUri, "w")?.use { output ->
                        temp.inputStream().use { input -> input.copyTo(output) }
                    } ?: error("Could not open the selected output document")
                    val hardware = HardwareCodecSelector.isHardwareName(actualName)
                    val fallback = candidateIndex > 0 || !hardware
                    val result = CompressionResult(
                        request.outputUri,
                        bytes,
                        request.videoMime,
                        actualName,
                        hardware,
                        fallback,
                        bitrateRetry,
                    )
                    writeDiagnostic(request, result, candidates.map { it.name })
                    listener.onComplete(result)
                } catch (error: Exception) {
                    listener.onError("Could not save output: ${error.message}")
                } finally {
                    temp.delete()
                    activeTemp = null
                    transformer = null
                }
            }

            override fun onError(
                composition: Composition,
                exportResult: ExportResult,
                exportException: ExportException,
            ) {
                handler.removeCallbacksAndMessages(null)
                temp.delete()
                transformer = null
                attempt(
                    request,
                    candidates,
                    candidateIndex + 1,
                    bitrate,
                    bitrateRetry,
                )
            }
        }

        transformer = Transformer.Builder(context)
            .setVideoMimeType(request.videoMime)
            .setAudioMimeType(request.audioMime)
            .setEncoderFactory(encoderFactory)
            .addListener(exportListener)
            .build()

        val clip = MediaItem.ClippingConfiguration.Builder()
            .setStartPositionMs(request.trimStartMs)
            .apply { request.trimEndMs?.let(::setEndPositionMs) }
            .build()
        val mediaItem = MediaItem.Builder()
            .setUri(request.inputUri)
            .setClippingConfiguration(clip)
            .build()
        val videoEffects = mutableListOf<Effect>()
        request.maxHeight?.takeIf { it > 0 }?.let {
            videoEffects += Presentation.createForHeight(it)
        }
        val editedBuilder = EditedMediaItem.Builder(mediaItem)
            .setRemoveAudio(!request.keepAudio)
            .setEffects(Effects(emptyList(), videoEffects))
        request.maxFps?.takeIf { it > 0 }?.let(editedBuilder::setFrameRate)
        val edited = editedBuilder.build()

        listener.onProgress(0, candidate.name, candidate.hardware)
        transformer?.start(edited, temp.absolutePath)
        pollProgress(candidate.name, candidate.hardware)
    }

    private fun pollProgress(encoder: String, hardware: Boolean) {
        val progress = ProgressHolder()
        val current = transformer ?: return
        if (current.getProgress(progress) == Transformer.PROGRESS_STATE_AVAILABLE) {
            listener.onProgress(progress.progress, encoder, hardware)
        }
        handler.postDelayed({ pollProgress(encoder, hardware) }, 500)
    }

    private data class MediaProperties(val durationMs: Long, val width: Int, val height: Int)

    private fun mediaProperties(uri: Uri): MediaProperties {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            MediaProperties(
                durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
                    ?: error("The selected video has no readable duration"),
                width = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0,
                height = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0,
            )
        } finally {
            retriever.release()
        }
    }

    private fun writeDiagnostic(
        request: CompressionRequest,
        result: CompressionResult,
        attempted: List<String>,
    ) {
        val json = JSONObject()
            .put("version", "143.0.0")
            .put("requested_mime", request.videoMime)
            .put("actual_encoder", result.actualEncoder)
            .put("hardware_used", result.hardwareUsed)
            .put("fallback_occurred", result.fallbackOccurred)
            .put("bitrate_retry", result.bitrateRetried)
            .put("actual_bytes", result.actualBytes)
            .put("target_mb", request.targetMb)
            .put("target_mode", if (request.videoBitrateOverride == null) "size" else "bitrate")
            .put("target_video_kbps", request.videoBitrateOverride?.div(1000) ?: 0)
            .put("max_height", request.maxHeight ?: 0)
            .put("auto_resolution", request.autoResolution)
            .put("min_auto_height", request.minAutoHeight)
            .put("max_fps", request.maxFps ?: 0)
            .put("audio_kbps", if (request.keepAudio) request.audioBitrate / 1000 else 0)
            .put("auto_audio_bitrate", request.autoAudioBitrate)
            .put("audio_mime", if (request.keepAudio) request.audioMime else "none")
            .put("audio_only", request.audioOnly)
            .put("software_fallback_allowed", request.allowSoftwareFallback)
            .put("attempted_encoders", attempted)
        File(context.filesDir, "last-codec-report.json").writeText(json.toString(2))
    }
}
