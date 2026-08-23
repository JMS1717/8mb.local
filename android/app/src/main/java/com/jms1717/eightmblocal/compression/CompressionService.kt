package com.jms1717.eightmblocal.compression

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ContentValues
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.media3.common.util.UnstableApi
import com.jms1717.eightmblocal.MainActivity
import com.jms1717.eightmblocal.history.CompressionHistory
import com.jms1717.eightmblocal.history.HistoryDatabase
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

@UnstableApi
class CompressionService : Service(), CompressionListener {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var engine: CompressionEngine? = null
    private lateinit var request: CompressionRequest
    private var mediaStoreOutput = false

    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel(CHANNEL_ID, "Video compression", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CANCEL) {
            engine?.cancel()
            return START_NOT_STICKY
        }
        val input = intent?.getStringExtra(EXTRA_INPUT)?.let(Uri::parse)
        val output = intent?.getStringExtra(EXTRA_OUTPUT)?.let(Uri::parse)
        if (input == null || output == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        request = CompressionRequest(
            inputUri = input,
            outputUri = output,
            targetMb = intent.getDoubleExtra(EXTRA_TARGET_MB, AndroidDefaults.TARGET_MB),
            videoBitrateOverride = intent.getIntExtra(EXTRA_VIDEO_KBPS, 0).takeIf { it > 0 }?.times(1000),
            videoMime = intent.getStringExtra(EXTRA_VIDEO_MIME) ?: "video/avc",
            maxHeight = intent.getIntExtra(EXTRA_MAX_HEIGHT, 0).takeIf { it > 0 },
            autoResolution = intent.getBooleanExtra(EXTRA_AUTO_RESOLUTION, AndroidDefaults.AUTO_RESOLUTION),
            minAutoHeight = intent.getIntExtra(EXTRA_MIN_AUTO_HEIGHT, AndroidDefaults.MIN_AUTO_HEIGHT),
            maxFps = intent.getIntExtra(EXTRA_MAX_FPS, 0).takeIf { it > 0 },
            audioBitrate = intent.getIntExtra(EXTRA_AUDIO_KBPS, AndroidDefaults.AUDIO_KBPS).coerceIn(32, 320) * 1000,
            autoAudioBitrate = intent.getBooleanExtra(EXTRA_AUTO_AUDIO_BITRATE, AndroidDefaults.AUTO_AUDIO_BITRATE),
            audioMime = intent.getStringExtra(EXTRA_AUDIO_MIME) ?: AndroidDefaults.AUDIO_MIME,
            keepAudio = intent.getBooleanExtra(EXTRA_KEEP_AUDIO, true),
            audioOnly = intent.getBooleanExtra(EXTRA_AUDIO_ONLY, false),
            allowSoftwareFallback = intent.getBooleanExtra(EXTRA_ALLOW_SOFTWARE_FALLBACK, true),
            trimStartMs = intent.getLongExtra(EXTRA_TRIM_START_MS, 0L),
            trimEndMs = intent.getLongExtra(EXTRA_TRIM_END_MS, -1L).takeIf { it >= 0 },
        )
        mediaStoreOutput = intent.getBooleanExtra(EXTRA_MEDIASTORE_OUTPUT, false)
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification(0, "Detecting hardware codecs…"),
            if (Build.VERSION.SDK_INT >= 35) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING else 0,
        )
        engine = CompressionEngine(this, this)
        android.os.Handler(mainLooper).post { engine?.start(request) }
        return START_NOT_STICKY
    }

    override fun onProgress(percent: Int, encoder: String, hardware: Boolean) {
        val label = when {
            request.audioOnly -> "Audio: $encoder"
            hardware -> "Hardware: $encoder"
            else -> "Software fallback: $encoder"
        }
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, notification(percent, label))
        broadcast("running", percent, label)
    }

    override fun onComplete(result: CompressionResult) {
        if (mediaStoreOutput && Build.VERSION.SDK_INT >= 29) {
            contentResolver.update(
                result.outputUri,
                ContentValues().apply { put(MediaStore.Video.Media.IS_PENDING, 0) },
                null,
                null,
            )
        }
        scope.launch {
            HistoryDatabase.get(this@CompressionService).history().insert(
                CompressionHistory(
                    createdAt = System.currentTimeMillis(),
                    outputUri = result.outputUri.toString(),
                    targetMb = request.targetMb,
                    actualBytes = result.actualBytes,
                    requestedMime = result.requestedMime,
                    actualEncoder = result.actualEncoder,
                    hardwareUsed = result.hardwareUsed,
                    fallbackOccurred = result.fallbackOccurred,
                    status = "completed",
                ),
            )
        }
        broadcast(
            "completed",
            100,
            if (request.audioOnly) "Saved audio • ${result.actualEncoder}"
            else "Saved • ${if (result.hardwareUsed) "Hardware" else "Software"}: ${result.actualEncoder}",
            result.outputUri,
        )
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onError(message: String) {
        deletePendingOutput()
        broadcast("error", 0, message)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onCancelled() {
        deletePendingOutput()
        broadcast("cancelled", 0, "Compression cancelled")
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        engine = null
        super.onDestroy()
    }

    private fun deletePendingOutput() {
        if (mediaStoreOutput && ::request.isInitialized) {
            runCatching { contentResolver.delete(request.outputUri, null, null) }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun notification(progress: Int, message: String) = NotificationCompat.Builder(this, CHANNEL_ID)
        .setSmallIcon(android.R.drawable.stat_sys_upload)
        .setContentTitle("8mb.local")
        .setContentText(message)
        .setProgress(100, progress, progress == 0)
        .setOngoing(true)
        .setContentIntent(
            PendingIntent.getActivity(
                this,
                0,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            ),
        )
        .addAction(
            android.R.drawable.ic_menu_close_clear_cancel,
            "Cancel",
            PendingIntent.getService(
                this,
                1,
                Intent(this, CompressionService::class.java).setAction(ACTION_CANCEL),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            ),
        )
        .build()

    private fun broadcast(state: String, progress: Int, message: String, output: Uri? = null) {
        sendBroadcast(
            Intent(ACTION_STATUS)
                .setPackage(packageName)
                .putExtra(EXTRA_STATE, state)
                .putExtra(EXTRA_PROGRESS, progress)
                .putExtra(EXTRA_MESSAGE, message)
                .putExtra(EXTRA_OUTPUT, output?.toString()),
        )
    }

    companion object {
        const val ACTION_STATUS = "com.jms1717.eightmblocal.COMPRESSION_STATUS"
        const val ACTION_CANCEL = "com.jms1717.eightmblocal.CANCEL"
        const val EXTRA_INPUT = "input_uri"
        const val EXTRA_OUTPUT = "output_uri"
        const val EXTRA_MEDIASTORE_OUTPUT = "mediastore_output"
        const val EXTRA_TARGET_MB = "target_mb"
        const val EXTRA_VIDEO_KBPS = "video_kbps"
        const val EXTRA_VIDEO_MIME = "video_mime"
        const val EXTRA_MAX_HEIGHT = "max_height"
        const val EXTRA_AUTO_RESOLUTION = "auto_resolution"
        const val EXTRA_MIN_AUTO_HEIGHT = "min_auto_height"
        const val EXTRA_MAX_FPS = "max_fps"
        const val EXTRA_AUDIO_KBPS = "audio_kbps"
        const val EXTRA_AUTO_AUDIO_BITRATE = "auto_audio_bitrate"
        const val EXTRA_AUDIO_MIME = "audio_mime"
        const val EXTRA_KEEP_AUDIO = "keep_audio"
        const val EXTRA_AUDIO_ONLY = "audio_only"
        const val EXTRA_ALLOW_SOFTWARE_FALLBACK = "allow_software_fallback"
        const val EXTRA_TRIM_START_MS = "trim_start_ms"
        const val EXTRA_TRIM_END_MS = "trim_end_ms"
        const val EXTRA_STATE = "state"
        const val EXTRA_PROGRESS = "progress"
        const val EXTRA_MESSAGE = "message"
        private const val CHANNEL_ID = "compression"
        private const val NOTIFICATION_ID = 143
    }
}
