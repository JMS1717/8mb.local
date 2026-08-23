package com.jms1717.eightmblocal

import android.Manifest
import android.content.ContentValues
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.annotation.RequiresApi
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import com.jms1717.eightmblocal.codec.HardwareCodecSelector
import com.jms1717.eightmblocal.compression.CompressionService
import com.jms1717.eightmblocal.compression.AndroidDefaults
import com.jms1717.eightmblocal.history.CompressionHistory
import com.jms1717.eightmblocal.history.HistoryDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val Page = Color(0xFF030712)
private val CardColor = Color(0xFF111827)
private val CardBorder = Color(0xFF263244)
private val InputColor = Color(0xFF0B1220)
private val Indigo = Color(0xFF4F46E5)
private val IndigoBright = Color(0xFF6366F1)
private val Emerald = Color(0xFF34D399)
private val Amber = Color(0xFFFBBF24)
private val TextPrimary = Color(0xFFF9FAFB)
private val TextMuted = Color(0xFF9CA3AF)

@UnstableApi
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { EightMbLocalApp() }
    }
}

private data class CodecChoice(
    val label: String,
    val mime: String,
    val hardwareNames: List<String>,
    val hasSoftwareFallback: Boolean,
)

private data class PickedVideo(val uri: Uri, val name: String)

@Composable
@UnstableApi
private fun EightMbLocalApp() {
    val context = androidx.compose.ui.platform.LocalContext.current
    var inputUri by remember { mutableStateOf<Uri?>(null) }
    var inputName by remember { mutableStateOf("No video selected") }
    var batchQueue by remember { mutableStateOf<List<PickedVideo>>(emptyList()) }
    var pendingOutput by remember { mutableStateOf(false) }
    var targetMode by remember { mutableStateOf("size") }
    var targetMb by remember { mutableStateOf(AndroidDefaults.TARGET_MB.toString()) }
    var targetVideoKbps by remember { mutableStateOf(AndroidDefaults.VIDEO_KBPS.toString()) }
    var maxHeight by remember { mutableIntStateOf(0) }
    var autoResolution by remember { mutableStateOf(AndroidDefaults.AUTO_RESOLUTION) }
    var minAutoHeight by remember { mutableIntStateOf(AndroidDefaults.MIN_AUTO_HEIGHT) }
    var maxFps by remember { mutableIntStateOf(0) }
    var audioKbps by remember { mutableIntStateOf(AndroidDefaults.AUDIO_KBPS) }
    var autoAudioBitrate by remember { mutableStateOf(AndroidDefaults.AUTO_AUDIO_BITRATE) }
    var audioMime by remember {
        mutableStateOf(if (Build.VERSION.SDK_INT >= 29) MimeTypes.AUDIO_OPUS else MimeTypes.AUDIO_AAC)
    }
    var keepAudio by remember { mutableStateOf(true) }
    var audioOnly by remember { mutableStateOf(false) }
    var allowSoftwareFallback by remember { mutableStateOf(true) }
    var askWhereToSave by remember { mutableStateOf(false) }
    var trimStart by remember { mutableStateOf("0") }
    var trimEnd by remember { mutableStateOf("") }
    var advancedOpen by remember { mutableStateOf(false) }
    var codecScanRunning by remember { mutableStateOf(true) }
    var availableCodecs by remember { mutableStateOf<List<CodecChoice>>(emptyList()) }
    var selectedMime by remember { mutableStateOf(MimeTypes.VIDEO_H264) }
    var status by remember { mutableStateOf("Choose a video to begin") }
    var progress by remember { mutableIntStateOf(0) }
    var running by remember { mutableStateOf(false) }
    var lastOutput by remember { mutableStateOf<Uri?>(null) }
    var lastOutputAudio by remember { mutableStateOf(false) }
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
                "H.264" to MimeTypes.VIDEO_H264,
                "HEVC" to MimeTypes.VIDEO_H265,
                "AV1" to MimeTypes.VIDEO_AV1,
            ).mapNotNull { (label, mime) ->
                val candidates = HardwareCodecSelector.candidates(mime)
                if (candidates.isEmpty()) null else CodecChoice(
                    label = label,
                    mime = mime,
                    hardwareNames = candidates.filter { it.hardware }.map { it.name },
                    hasSoftwareFallback = candidates.any { !it.hardware },
                )
            }
        }
        codecScanRunning = false
        selectedMime = availableCodecs.firstOrNull {
            it.mime == MimeTypes.VIDEO_AV1 && it.hardwareNames.isNotEmpty()
        }?.mime ?: availableCodecs.firstOrNull { it.hardwareNames.isNotEmpty() }?.mime
            ?: availableCodecs.firstOrNull()?.mime
            ?: MimeTypes.VIDEO_H264
        history = withContext(Dispatchers.IO) { HistoryDatabase.get(context).history().recent() }
    }

    val videoPicker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) {
            inputUri = uri
            inputName = displayName(context, uri)
            batchQueue = emptyList()
            status = "Ready to compress"
        }
    }
    val batchPicker = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia(50)) { uris ->
        if (uris.isNotEmpty()) {
            batchQueue = uris.map { PickedVideo(it, displayName(context, it)) }
            inputUri = batchQueue.first().uri
            inputName = batchQueue.first().name
            askWhereToSave = false
            status = "${batchQueue.size} videos ready for automatic batch compression"
        }
    }

    val startCompression: (Uri, Uri, Boolean) -> Unit = { input, output, mediaStoreOutput ->
            val intent = Intent(context, CompressionService::class.java)
                .putExtra(CompressionService.EXTRA_INPUT, input.toString())
                .putExtra(CompressionService.EXTRA_OUTPUT, output.toString())
                .putExtra(CompressionService.EXTRA_MEDIASTORE_OUTPUT, mediaStoreOutput)
                .putExtra(CompressionService.EXTRA_TARGET_MB, targetMb.toDoubleOrNull() ?: AndroidDefaults.TARGET_MB)
                .putExtra(
                    CompressionService.EXTRA_VIDEO_KBPS,
                    if (targetMode == "bitrate") targetVideoKbps.toIntOrNull() ?: AndroidDefaults.VIDEO_KBPS else 0,
                )
                .putExtra(CompressionService.EXTRA_VIDEO_MIME, selectedMime)
                .putExtra(CompressionService.EXTRA_MAX_HEIGHT, maxHeight)
                .putExtra(CompressionService.EXTRA_AUTO_RESOLUTION, autoResolution)
                .putExtra(CompressionService.EXTRA_MIN_AUTO_HEIGHT, minAutoHeight)
                .putExtra(CompressionService.EXTRA_MAX_FPS, maxFps)
                .putExtra(CompressionService.EXTRA_AUDIO_KBPS, audioKbps)
                .putExtra(CompressionService.EXTRA_AUTO_AUDIO_BITRATE, autoAudioBitrate)
                .putExtra(CompressionService.EXTRA_AUDIO_MIME, audioMime)
                .putExtra(CompressionService.EXTRA_KEEP_AUDIO, keepAudio || audioOnly)
                .putExtra(CompressionService.EXTRA_AUDIO_ONLY, audioOnly)
                .putExtra(CompressionService.EXTRA_ALLOW_SOFTWARE_FALLBACK, allowSoftwareFallback)
                .putExtra(CompressionService.EXTRA_TRIM_START_MS, ((trimStart.toDoubleOrNull() ?: 0.0) * 1000).toLong())
                .putExtra(CompressionService.EXTRA_TRIM_END_MS, trimEnd.toDoubleOrNull()?.times(1000)?.toLong() ?: -1L)
            ContextCompat.startForegroundService(context, intent)
            running = true
            progress = 0
            status = "Starting hardware encoder…"
    }
    val videoOutputPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("video/mp4"),
    ) { output ->
        pendingOutput = false
        if (output != null && inputUri != null) {
            runCatching {
                context.contentResolver.takePersistableUriPermission(
                    output,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            }
            inputUri?.let { startCompression(it, output, false) }
        }
    }
    val audioOutputPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("audio/mp4"),
    ) { output ->
        pendingOutput = false
        if (output != null) inputUri?.let { startCompression(it, output, false) }
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
                    lastOutputAudio = audioOnly
                    if (batchQueue.isNotEmpty()) {
                        val remaining = batchQueue.drop(1)
                        batchQueue = remaining
                        if (remaining.isNotEmpty()) {
                            val next = remaining.first()
                            inputUri = next.uri
                            inputName = next.name
                            val base = next.name.substringBeforeLast('.').ifBlank { "video" }
                            val fileName = "${base}_8mblocal.${if (audioOnly) "m4a" else "mp4"}"
                            val output = if (Build.VERSION.SDK_INT >= 29) {
                                runCatching { createMediaStoreOutput(context, fileName, audioOnly) }.getOrNull()
                            } else null
                            if (output != null) {
                                status = "Batch: ${remaining.size} video${if (remaining.size == 1) "" else "s"} remaining"
                                startCompression(next.uri, output, true)
                            } else {
                                batchQueue = emptyList()
                                status = "Batch stopped: could not create the next output"
                            }
                        } else {
                            status = "Batch completed • all outputs saved"
                        }
                    }
                    (context as? ComponentActivity)?.lifecycleScope?.launch {
                        history = withContext(Dispatchers.IO) { HistoryDatabase.get(context).history().recent() }
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

    EightMbTheme {
        Box(
            Modifier
                .fillMaxSize()
                .background(Brush.verticalGradient(listOf(Color(0xFF07101F), Page, Page))),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .statusBarsPadding()
                    .navigationBarsPadding()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 14.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Header(codecScanRunning, availableCodecs.count { it.hardwareNames.isNotEmpty() })

                SectionCard {
                    Text("Video", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text("Choose from your photo library—no broad storage permission needed.", color = TextMuted, fontSize = 13.sp)
                    OutlinedButton(
                        onClick = {
                            videoPicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.VideoOnly))
                        },
                        enabled = !running,
                        modifier = Modifier.fillMaxWidth().height(54.dp),
                        border = BorderStroke(1.dp, IndigoBright),
                    ) {
                        Text(if (inputUri == null) "＋  Choose video" else "↻  Change video")
                    }
                    OutlinedButton(
                        onClick = { batchPicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.VideoOnly)) },
                        enabled = !running && Build.VERSION.SDK_INT >= 29,
                        modifier = Modifier.fillMaxWidth().height(48.dp),
                        border = BorderStroke(1.dp, CardBorder),
                    ) { Text("▦  Choose multiple videos") }
                    if (inputUri != null) {
                        Row(
                            Modifier.fillMaxWidth().background(InputColor, RoundedCornerShape(10.dp)).padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text("▶", color = IndigoBright)
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(inputName, maxLines = 1, fontWeight = FontWeight.Medium)
                                Text(
                                    if (batchQueue.isEmpty()) "Selected from Photos"
                                    else "${batchQueue.size} videos in automatic batch",
                                    color = TextMuted,
                                    fontSize = 12.sp,
                                )
                            }
                        }
                    }
                }

                SectionCard {
                    SectionTitle("Target", "Desktop default: 19.7 MB size mode")
                    OptionRow(
                        options = listOf("size", "bitrate"),
                        selected = targetMode,
                        label = { if (it == "size") "Target size" else "Video bitrate" },
                        enabled = !running && !audioOnly,
                        onSelect = { targetMode = it },
                    )
                    if (targetMode == "size") {
                        OptionRow(
                            options = AndroidDefaults.SIZE_BUTTONS.take(4),
                            selected = targetMb.toDoubleOrNull(),
                            label = { "${formatNumber(it)} MB" },
                            enabled = !running && !audioOnly,
                            onSelect = { targetMb = formatNumber(it) },
                        )
                        OptionRow(
                            options = AndroidDefaults.SIZE_BUTTONS.drop(4),
                            selected = targetMb.toDoubleOrNull(),
                            label = { "${formatNumber(it)} MB" },
                            enabled = !running && !audioOnly,
                            onSelect = { targetMb = formatNumber(it) },
                        )
                        OutlinedTextField(
                            value = targetMb,
                            onValueChange = { value -> targetMb = value.filter { it.isDigit() || it == '.' } },
                            label = { Text("Custom target (MB)") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                            singleLine = true,
                            enabled = !running && !audioOnly,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    } else {
                        OutlinedTextField(
                            value = targetVideoKbps,
                            onValueChange = { value -> targetVideoKbps = value.filter(Char::isDigit) },
                            label = { Text("Video bitrate (kbps)") },
                            supportingText = { Text("Desktop default: 2500 kbps") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            enabled = !running && !audioOnly,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }

                SectionCard {
                    SectionTitle("Video codec", "Hardware encoders are probed before they appear")
                    ToggleRow("Extract audio only (.m4a)", audioOnly, !running) {
                        audioOnly = it
                        if (it) keepAudio = true
                    }
                    if (codecScanRunning) {
                        LinearProgressIndicator(modifier = Modifier.fillMaxWidth(), color = IndigoBright)
                        Text("Testing MediaCodec hardware…", color = TextMuted, fontSize = 13.sp)
                    } else {
                        availableCodecs.forEach { codec ->
                            CodecOption(
                                codec = codec,
                                selected = codec.mime == selectedMime,
                                enabled = !running && !audioOnly,
                                onClick = { selectedMime = codec.mime },
                            )
                        }
                    }
                }

                SectionCard {
                    SectionTitle("Resolution", "Maximum output height")
                    ToggleRow("Auto resolution", autoResolution, !running && !audioOnly) { autoResolution = it }
                    if (autoResolution) {
                        Text("Desktop algorithm chooses the best height, never below:", color = TextMuted, fontSize = 12.sp)
                        OptionRow(
                            options = listOf(240, 360, 480, 720),
                            selected = minAutoHeight,
                            label = { "${it}p" },
                            enabled = !running && !audioOnly,
                            onSelect = { minAutoHeight = it },
                        )
                    }
                    OptionRow(
                        options = listOf(0, 240, 360, 480),
                        selected = maxHeight,
                        label = { if (it == 0) "Source" else "${it}p" },
                        enabled = !running && !autoResolution && !audioOnly,
                        onSelect = { maxHeight = it },
                    )
                    OptionRow(
                        options = listOf(720, 1080, 1440, 2160),
                        selected = maxHeight,
                        label = { "${it}p" },
                        enabled = !running && !autoResolution && !audioOnly,
                        onSelect = { maxHeight = it },
                    )
                }

                SectionCard {
                    TextButton(onClick = { advancedOpen = !advancedOpen }, modifier = Modifier.fillMaxWidth()) {
                        Text(if (advancedOpen) "⌃  Hide advanced options" else "⚙  Show advanced options")
                    }
                    if (advancedOpen) {
                        SectionTitle("Frame-rate cap", "Source keeps the original frame rate")
                        OptionRow(
                            options = listOf(0, 24, 30, 48),
                            selected = maxFps,
                            label = { if (it == 0) "Source" else "$it fps" },
                            enabled = !running && !audioOnly,
                            onSelect = { maxFps = it },
                        )
                        OptionRow(
                            options = listOf(60, 120),
                            selected = maxFps,
                            label = { "$it fps" },
                            enabled = !running && !audioOnly,
                            onSelect = { maxFps = it },
                        )
                        Spacer(Modifier.height(4.dp))
                        SectionTitle("Audio", "Desktop default: Opus at 128 kbps")
                        ToggleRow("Keep audio", keepAudio, !running && !audioOnly) { keepAudio = it }
                        ToggleRow("Auto audio bitrate", autoAudioBitrate, !running && keepAudio) {
                            autoAudioBitrate = it
                        }
                        if (autoAudioBitrate && keepAudio) {
                            Text("Reserves at least 100 kbps for video on very small targets.", color = TextMuted, fontSize = 12.sp)
                        }
                        OptionRow(
                            options = listOf(MimeTypes.AUDIO_OPUS, MimeTypes.AUDIO_AAC),
                            selected = audioMime,
                            label = { if (it == MimeTypes.AUDIO_OPUS) "Opus" else "AAC" },
                            enabled = !running && keepAudio && Build.VERSION.SDK_INT >= 29,
                            onSelect = { audioMime = it },
                        )
                        OptionRow(
                            options = listOf(32, 48, 64, 96),
                            selected = audioKbps,
                            label = { "$it k" },
                            enabled = !running && keepAudio && !autoAudioBitrate,
                            onSelect = { audioKbps = it },
                        )
                        OptionRow(
                            options = listOf(128, 160, 192, 256),
                            selected = audioKbps,
                            label = { "$it k" },
                            enabled = !running && keepAudio && !autoAudioBitrate,
                            onSelect = { audioKbps = it },
                        )
                        Spacer(Modifier.height(4.dp))
                        SectionTitle("Trim", "Optional start and end time")
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            NumberField("Start sec", trimStart, Modifier.weight(1f), !running) { trimStart = it }
                            NumberField("End sec", trimEnd, Modifier.weight(1f), !running) { trimEnd = it }
                        }
                        ToggleRow(
                            "Allow software fallback",
                            allowSoftwareFallback,
                            !running,
                        ) { allowSoftwareFallback = it }
                        Text(
                            if (allowSoftwareFallback) "A failed hardware encoder can retry in software."
                            else "The job will fail instead of silently using software.",
                            color = if (allowSoftwareFallback) TextMuted else Amber,
                            fontSize = 12.sp,
                        )
                        ToggleRow("Ask where to save", askWhereToSave, !running && batchQueue.isEmpty()) { askWhereToSave = it }
                        Text(
                            if (askWhereToSave) "Android will open a Save As dialog for every output."
                            else "Outputs save automatically to Movies/8mb.local or Music/8mb.local.",
                            color = TextMuted,
                            fontSize = 12.sp,
                        )
                        Spacer(Modifier.height(4.dp))
                        SectionTitle("Desktop compatibility", "Same intent, native Android controls")
                        Text(
                            "Balanced/P4 default • MP4 • hardware decode automatic • native fast finalize",
                            color = TextMuted,
                            fontSize = 12.sp,
                        )
                        Text(
                            "NVENC-only tuning and MKV are not exposed because Android MediaCodec has no honest equivalent.",
                            color = TextMuted,
                            fontSize = 12.sp,
                        )
                    }
                }

                SectionCard {
                    if (running) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("Compressing", fontWeight = FontWeight.SemiBold)
                            Text("$progress%", color = IndigoBright, fontWeight = FontWeight.Bold)
                        }
                        LinearProgressIndicator(
                            progress = { progress / 100f },
                            modifier = Modifier.fillMaxWidth().height(8.dp),
                            color = IndigoBright,
                            trackColor = InputColor,
                        )
                        OutlinedButton(
                            onClick = {
                                context.startService(Intent(context, CompressionService::class.java).setAction(CompressionService.ACTION_CANCEL))
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("Cancel") }
                    } else {
                        Button(
                            onClick = {
                                pendingOutput = true
                                val base = inputName.substringBeforeLast('.').ifBlank { "video" }
                                val fileName = "${base}_8mblocal.${if (audioOnly) "m4a" else "mp4"}"
                                if (askWhereToSave || Build.VERSION.SDK_INT < 29) {
                                    if (audioOnly) audioOutputPicker.launch(fileName) else videoOutputPicker.launch(fileName)
                                } else {
                                    val output = runCatching { createMediaStoreOutput(context, fileName, audioOnly) }.getOrNull()
                                    pendingOutput = false
                                    if (output == null) {
                                        status = "Could not create a media output; enable Ask where to save."
                                    } else {
                                        inputUri?.let { startCompression(it, output, true) }
                                    }
                                }
                            },
                            enabled = inputUri != null && !pendingOutput && !codecScanRunning && (audioOnly || when (targetMode) {
                                "bitrate" -> (targetVideoKbps.toIntOrNull() ?: 0) > 0
                                else -> (targetMb.toDoubleOrNull() ?: 0.0) > 0
                            }),
                            modifier = Modifier.fillMaxWidth().height(56.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = Indigo),
                        ) { Text(if (audioOnly) "♫  Extract and save audio" else "⚡  Compress and save", fontWeight = FontWeight.Bold) }
                    }
                    Text(status, color = if (status.contains("error", true)) Amber else TextMuted, fontSize = 13.sp)
                    lastOutput?.let { uri ->
                        OutlinedButton(
                            onClick = {
                                context.startActivity(
                                    Intent.createChooser(
                                        Intent(Intent.ACTION_SEND)
                                            .setType(if (lastOutputAudio) "audio/mp4" else "video/mp4")
                                            .putExtra(Intent.EXTRA_STREAM, uri)
                                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                                        "Share compressed video",
                                    ),
                                )
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("Share last output") }
                    }
                }

                if (history.isNotEmpty()) {
                    Text("Recent jobs", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    history.take(5).forEach { item -> HistoryCard(item) }
                }
                Spacer(Modifier.height(12.dp))
            }
        }
    }
}

@Composable
private fun Header(scanning: Boolean, hardwareCount: Int) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(
                "8mb.local",
                color = TextPrimary,
                fontSize = 32.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-1).sp,
            )
            Text("Native Android  •  v143.0.0", color = TextMuted, fontSize = 13.sp)
        }
        Surface(
            color = if (!scanning && hardwareCount > 0) Color(0xFF052E25) else InputColor,
            shape = RoundedCornerShape(999.dp),
            border = BorderStroke(1.dp, if (hardwareCount > 0) Color(0xFF166534) else CardBorder),
        ) {
            Text(
                if (scanning) "Scanning…" else "●  $hardwareCount HW codecs",
                color = if (hardwareCount > 0) Emerald else TextMuted,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp),
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
    }
}

@Composable
private fun SectionCard(content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = CardColor),
        border = BorderStroke(1.dp, CardBorder),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            content = content,
        )
    }
}

@Composable
private fun SectionTitle(title: String, subtitle: String) {
    Text(title, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
    Text(subtitle, color = TextMuted, fontSize = 12.sp)
}

@Composable
private fun <T> OptionRow(
    options: List<T>,
    selected: T?,
    label: (T) -> String,
    enabled: Boolean,
    onSelect: (T) -> Unit,
) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
        options.forEach { option ->
            val active = option == selected
            OutlinedButton(
                onClick = { onSelect(option) },
                enabled = enabled,
                modifier = Modifier.weight(1f),
                border = BorderStroke(1.dp, if (active) IndigoBright else CardBorder),
                colors = ButtonDefaults.outlinedButtonColors(
                    containerColor = if (active) Color(0xFF312E81) else InputColor,
                    contentColor = if (active) Color.White else TextMuted,
                ),
                contentPadding = PaddingValues(horizontal = 3.dp, vertical = 0.dp),
            ) { Text(label(option), fontSize = 12.sp, maxLines = 1) }
        }
    }
}

@Composable
private fun CodecOption(codec: CodecChoice, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val hardware = codec.hardwareNames.isNotEmpty()
    Surface(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.fillMaxWidth(),
        color = if (selected) Color(0xFF1E1B4B) else InputColor,
        shape = RoundedCornerShape(10.dp),
        border = BorderStroke(1.dp, if (selected) IndigoBright else CardBorder),
    ) {
        Row(Modifier.padding(13.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(codec.label, fontWeight = FontWeight.SemiBold)
                if (hardware) {
                    Text(
                        codec.hardwareNames.map(::hardwareVendor).distinct().joinToString(" • "),
                        color = TextMuted,
                        fontSize = 10.sp,
                        maxLines = 1,
                    )
                }
            }
            Text(
                when {
                    hardware -> "⚡ ${codec.hardwareNames.size} hardware"
                    codec.hasSoftwareFallback -> "software"
                    else -> "unavailable"
                },
                color = if (hardware) Emerald else TextMuted,
                fontSize = 12.sp,
            )
        }
    }
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, enabled: Boolean, onChecked: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f), fontSize = 14.sp)
        Switch(
            checked = checked,
            onCheckedChange = onChecked,
            enabled = enabled,
            colors = SwitchDefaults.colors(checkedTrackColor = IndigoBright),
        )
    }
}

@Composable
private fun NumberField(
    label: String,
    value: String,
    modifier: Modifier,
    enabled: Boolean,
    onChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = { next -> onChange(next.filter { it.isDigit() || it == '.' }) },
        label = { Text(label) },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        singleLine = true,
        enabled = enabled,
        modifier = modifier,
    )
}

@Composable
private fun HistoryCard(item: CompressionHistory) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = CardColor),
        border = BorderStroke(1.dp, CardBorder),
    ) {
        Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("${"%.2f".format(item.actualBytes / 1048576.0)} MB", fontWeight = FontWeight.Bold)
                Text(item.actualEncoder, color = TextMuted, fontSize = 12.sp)
            }
            Text(
                if (item.hardwareUsed) "● HARDWARE" else "● SOFTWARE",
                color = if (item.hardwareUsed) Emerald else Amber,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
    }
}

private fun displayName(context: Context, uri: Uri): String {
    var cursor: Cursor? = null
    return try {
        cursor = context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
        if (cursor?.moveToFirst() == true) cursor?.getString(0) ?: "Selected video" else "Selected video"
    } catch (_: Exception) {
        "Selected video"
    } finally {
        cursor?.close()
    }
}

@RequiresApi(29)
private fun createMediaStoreOutput(context: Context, requestedName: String, audioOnly: Boolean): Uri? {
    check(Build.VERSION.SDK_INT >= 29)
    val safeName = requestedName.replace(Regex("[^A-Za-z0-9._ -]"), "_")
    val values = ContentValues().apply {
        put(MediaStore.Video.Media.DISPLAY_NAME, safeName)
        put(MediaStore.MediaColumns.MIME_TYPE, if (audioOnly) "audio/mp4" else "video/mp4")
        put(
            MediaStore.MediaColumns.RELATIVE_PATH,
            "${if (audioOnly) Environment.DIRECTORY_MUSIC else Environment.DIRECTORY_MOVIES}/8mb.local",
        )
        put(MediaStore.MediaColumns.IS_PENDING, 1)
    }
    val collection = if (audioOnly) MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
    else MediaStore.Video.Media.EXTERNAL_CONTENT_URI
    return context.contentResolver.insert(collection, values)
}

private fun hardwareVendor(codecName: String): String {
    val name = codecName.lowercase()
    return when {
        "qti" in name || "qcom" in name || "qualcomm" in name -> "Qualcomm"
        "mtk" in name || "mediatek" in name -> "MediaTek"
        "exynos" in name || "samsung" in name -> "Samsung"
        "kirin" in name || "hisi" in name -> "HiSilicon"
        else -> codecName.substringAfterLast('.').take(18)
    }
}

private fun formatNumber(value: Double): String =
    if (value % 1.0 == 0.0) value.toInt().toString() else value.toString()

@Composable
private fun EightMbTheme(content: @Composable () -> Unit) {
    val colors = darkColorScheme(
        primary = IndigoBright,
        secondary = Emerald,
        background = Page,
        surface = CardColor,
        onPrimary = Color.White,
        onBackground = TextPrimary,
        onSurface = TextPrimary,
        outline = CardBorder,
    )
    MaterialTheme(colorScheme = colors, content = content)
}
