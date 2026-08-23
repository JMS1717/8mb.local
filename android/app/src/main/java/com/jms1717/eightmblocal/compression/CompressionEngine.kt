package com.jms1717.eightmblocal.compression

import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.media3.common.Effect
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.Presentation
import androidx.media3.transformer.Composition
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
    val videoMime: String,
    val maxHeight: Int?,
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
        val candidates = HardwareCodecSelector.candidates(request.videoMime)
        if (candidates.isEmpty()) {
            listener.onError("No working encoder can configure and start for ${request.videoMime}")
            return
        }
        val durationMs = mediaDurationMs(request.inputUri)
        val effectiveDurationMs = (
            (request.trimEndMs ?: durationMs).coerceAtMost(durationMs) - request.trimStartMs
        ).coerceAtLeast(1)
        val bitrate = SizePlanner.videoBitrate(request.targetMb, effectiveDurationMs)
        attempt(request, candidates, candidateIndex = 0, bitrate = bitrate, bitrateRetry = false)
    }

    fun cancel() {
        cancelled = true
        transformer?.cancel()
        transformer = null
        handler.removeCallbacksAndMessages(null)
        activeTemp?.delete()
        listener.onCancelled()
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
        val encoderFactory = DefaultEncoderFactory.Builder(context)
            .setVideoEncoderSelector(HardwareCodecSelector.encoderSelector(candidate))
            .setRequestedVideoEncoderSettings(
                VideoEncoderSettings.Builder().setBitrate(max(64_000, bitrate)).build(),
            )
            .setEnableFallback(false)
            .build()

        val exportListener = object : Transformer.Listener {
            override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                handler.removeCallbacksAndMessages(null)
                val actualName = exportResult.videoEncoderName ?: candidate.name
                val bytes = temp.length()
                val targetBytes = (request.targetMb * 1024.0 * 1024.0).toLong()
                if (!bitrateRetry && SizePlanner.exceedsTolerance(bytes, targetBytes)) {
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
            .setAudioMimeType(MimeTypes.AUDIO_AAC)
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
        val edited = EditedMediaItem.Builder(mediaItem)
            .setEffects(Effects(emptyList(), videoEffects))
            .build()

        listener.onProgress(0, candidate.name, candidate.hardware)
        transformer?.start(edited, temp.absolutePath)
        pollProgress(candidate)
    }

    private fun pollProgress(candidate: CodecCandidate) {
        val progress = ProgressHolder()
        val current = transformer ?: return
        if (current.getProgress(progress) == Transformer.PROGRESS_STATE_AVAILABLE) {
            listener.onProgress(progress.progress, candidate.name, candidate.hardware)
        }
        handler.postDelayed({ pollProgress(candidate) }, 500)
    }

    private fun mediaDurationMs(uri: Uri): Long {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
                ?: error("The selected video has no readable duration")
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
            .put("attempted_encoders", attempted)
        File(context.filesDir, "last-codec-report.json").writeText(json.toString(2))
    }
}
