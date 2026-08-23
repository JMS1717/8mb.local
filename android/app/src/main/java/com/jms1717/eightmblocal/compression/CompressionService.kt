package com.jms1717.eightmblocal.compression

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
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
            targetMb = intent.getDoubleExtra(EXTRA_TARGET_MB, 8.0),
            videoMime = intent.getStringExtra(EXTRA_VIDEO_MIME) ?: "video/avc",
            maxHeight = intent.getIntExtra(EXTRA_MAX_HEIGHT, 0).takeIf { it > 0 },
            trimStartMs = intent.getLongExtra(EXTRA_TRIM_START_MS, 0L),
            trimEndMs = intent.getLongExtra(EXTRA_TRIM_END_MS, -1L).takeIf { it >= 0 },
        )
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
        val label = if (hardware) "Hardware: $encoder" else "Software fallback: $encoder"
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, notification(percent, label))
        broadcast("running", percent, label)
    }

    override fun onComplete(result: CompressionResult) {
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
            "${if (result.hardwareUsed) "Hardware" else "Software"}: ${result.actualEncoder}",
            result.outputUri,
        )
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onError(message: String) {
        broadcast("error", 0, message)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onCancelled() {
        broadcast("cancelled", 0, "Compression cancelled")
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        engine = null
        super.onDestroy()
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
        const val EXTRA_TARGET_MB = "target_mb"
        const val EXTRA_VIDEO_MIME = "video_mime"
        const val EXTRA_MAX_HEIGHT = "max_height"
        const val EXTRA_TRIM_START_MS = "trim_start_ms"
        const val EXTRA_TRIM_END_MS = "trim_end_ms"
        const val EXTRA_STATE = "state"
        const val EXTRA_PROGRESS = "progress"
        const val EXTRA_MESSAGE = "message"
        private const val CHANNEL_ID = "compression"
        private const val NOTIFICATION_ID = 143
    }
}
