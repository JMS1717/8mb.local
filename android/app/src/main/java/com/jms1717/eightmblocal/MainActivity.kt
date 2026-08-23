package com.jms1717.eightmblocal

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import com.jms1717.eightmblocal.codec.HardwareCodecSelector
import com.jms1717.eightmblocal.compression.CompressionService
import com.jms1717.eightmblocal.history.CompressionHistory
import com.jms1717.eightmblocal.history.HistoryDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@UnstableApi
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { EightMbLocalApp() }
    }
}

private data class CodecChoice(val label: String, val mime: String)

@Composable
@UnstableApi
private fun EightMbLocalApp() {
    val context = androidx.compose.ui.platform.LocalContext.current
    var inputUri by remember { mutableStateOf<Uri?>(null) }
    var pendingOutput by remember { mutableStateOf(false) }
    var targetMb by remember { mutableStateOf("8") }
    var maxHeight by remember { mutableStateOf("0") }
    var trimStart by remember { mutableStateOf("0") }
    var trimEnd by remember { mutableStateOf("") }
    var availableCodecs by remember { mutableStateOf<List<CodecChoice>>(emptyList()) }
    var selectedMime by remember { mutableStateOf(MimeTypes.VIDEO_H264) }
    var status by remember { mutableStateOf("Choose a video to begin") }
    var progress by remember { mutableIntStateOf(0) }
    var running by remember { mutableStateOf(false) }
    var lastOutput by remember { mutableStateOf<Uri?>(null) }
    var history by remember { mutableStateOf<List<CompressionHistory>>(emptyList()) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) {}
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        availableCodecs = withContext(Dispatchers.Default) {
            listOf(
                CodecChoice("H.264", MimeTypes.VIDEO_H264),
                CodecChoice("HEVC", MimeTypes.VIDEO_H265),
                CodecChoice("AV1", MimeTypes.VIDEO_AV1),
            ).filter { HardwareCodecSelector.candidates(it.mime).isNotEmpty() }
        }
        selectedMime = availableCodecs.firstOrNull()?.mime ?: MimeTypes.VIDEO_H264
        history = withContext(Dispatchers.IO) {
            HistoryDatabase.get(context).history().recent()
        }
    }

    val inputPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching {
                context.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            inputUri = uri
            status = "Ready"
        }
    }
    val outputPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("video/mp4"),
    ) { output ->
        pendingOutput = false
        val input = inputUri
        if (output != null && input != null) {
            runCatching {
                context.contentResolver.takePersistableUriPermission(
                    output,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            }
            val intent = Intent(context, CompressionService::class.java)
                .putExtra(CompressionService.EXTRA_INPUT, input.toString())
                .putExtra(CompressionService.EXTRA_OUTPUT, output.toString())
                .putExtra(CompressionService.EXTRA_TARGET_MB, targetMb.toDoubleOrNull() ?: 8.0)
                .putExtra(CompressionService.EXTRA_VIDEO_MIME, selectedMime)
                .putExtra(CompressionService.EXTRA_MAX_HEIGHT, maxHeight.toIntOrNull() ?: 0)
                .putExtra(CompressionService.EXTRA_TRIM_START_MS, ((trimStart.toDoubleOrNull() ?: 0.0) * 1000).toLong())
                .putExtra(CompressionService.EXTRA_TRIM_END_MS, trimEnd.toDoubleOrNull()?.times(1000)?.toLong() ?: -1L)
            ContextCompat.startForegroundService(context, intent)
            running = true
            progress = 0
            status = "Starting hardware codec probe…"
        }
    }

    val receiver = remember {
        object : BroadcastReceiver() {
            override fun onReceive(receiverContext: Context?, intent: Intent?) {
                if (intent?.action != CompressionService.ACTION_STATUS) return
                val state = intent.getStringExtra(CompressionService.EXTRA_STATE).orEmpty()
                progress = intent.getIntExtra(CompressionService.EXTRA_PROGRESS, 0)
                status = intent.getStringExtra(CompressionService.EXTRA_MESSAGE).orEmpty()
                if (state in listOf("completed", "error", "cancelled")) running = false
                if (state == "completed") {
                    lastOutput = intent.getStringExtra(CompressionService.EXTRA_OUTPUT)?.let(Uri::parse)
                    (context as? ComponentActivity)?.lifecycleScope?.launch {
                        history = withContext(Dispatchers.IO) {
                            HistoryDatabase.get(context).history().recent()
                        }
                    }
                }
            }
        }
    }
    DisposableEffect(Unit) {
        ContextCompat.registerReceiver(
            context,
            receiver,
            IntentFilter(CompressionService.ACTION_STATUS),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        onDispose { context.unregisterReceiver(receiver) }
    }

    MaterialTheme {
        Surface(Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier.padding(20.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text("8mb.local", style = MaterialTheme.typography.headlineMedium)
                Text("Native Android • v143.0.0")
                OutlinedButton(onClick = { inputPicker.launch(arrayOf("video/*")) }, enabled = !running) {
                    Text(if (inputUri == null) "Choose video" else "Change video")
                }
                Text(inputUri?.lastPathSegment ?: "No video selected")

                OutlinedTextField(
                    value = targetMb,
                    onValueChange = { targetMb = it },
                    label = { Text("Target size (MB)") },
                    enabled = !running,
                    modifier = Modifier.fillMaxWidth(),
                )
                Text("Hardware-first video codec")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    availableCodecs.forEach { codec ->
                        if (codec.mime == selectedMime) {
                            Button(onClick = {}, enabled = !running) { Text(codec.label) }
                        } else {
                            OutlinedButton(onClick = { selectedMime = codec.mime }, enabled = !running) {
                                Text(codec.label)
                            }
                        }
                    }
                }
                OutlinedTextField(
                    value = maxHeight,
                    onValueChange = { maxHeight = it },
                    label = { Text("Max height (0 = source)") },
                    enabled = !running,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = trimStart,
                        onValueChange = { trimStart = it },
                        label = { Text("Start seconds") },
                        enabled = !running,
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedTextField(
                        value = trimEnd,
                        onValueChange = { trimEnd = it },
                        label = { Text("End (optional)") },
                        enabled = !running,
                        modifier = Modifier.weight(1f),
                    )
                }

                if (running) {
                    LinearProgressIndicator(progress = { progress / 100f }, modifier = Modifier.fillMaxWidth())
                    Button(onClick = {
                        context.startService(Intent(context, CompressionService::class.java).setAction(CompressionService.ACTION_CANCEL))
                    }) { Text("Cancel") }
                } else {
                    Button(
                        onClick = {
                            pendingOutput = true
                            outputPicker.launch("8mblocal-output.mp4")
                        },
                        enabled = inputUri != null && !pendingOutput && (targetMb.toDoubleOrNull() ?: 0.0) > 0,
                    ) { Text("Compress and save") }
                }
                Text(status)

                lastOutput?.let { uri ->
                    OutlinedButton(onClick = {
                        context.startActivity(
                            Intent.createChooser(
                                Intent(Intent.ACTION_SEND)
                                    .setType("video/mp4")
                                    .putExtra(Intent.EXTRA_STREAM, uri)
                                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                                "Share compressed video",
                            ),
                        )
                    }) { Text("Share last output") }
                }

                Spacer(Modifier.height(8.dp))
                Text("History", style = MaterialTheme.typography.titleLarge)
                history.forEach { item ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Text("${"%.2f".format(item.actualBytes / 1048576.0)} MB • ${item.actualEncoder}")
                            Text(if (item.hardwareUsed) "Hardware codec used" else "Software fallback used")
                        }
                    }
                }
            }
        }
    }
}
