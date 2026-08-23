package com.jms1717.eightmblocal

import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.jms1717.eightmblocal.codec.CodecPriority
import com.jms1717.eightmblocal.codec.HardwareCodecSelector
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Records the OEM MediaCodec components that the app can really initialize. */
@RunWith(AndroidJUnit4::class)
@UnstableApi
class PhysicalCodecInventoryTest {
    @Test
    fun workingVideoCodecInventoryAndAutomaticChoiceAreReported() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val formats = linkedMapOf(
            "av1" to MimeTypes.VIDEO_AV1,
            "hevc" to MimeTypes.VIDEO_H265,
            "h264" to MimeTypes.VIDEO_H264,
        )
        val workingMimes = mutableSetOf<String>()
        val workingHardwareMimes = mutableSetOf<String>()
        val formatReport = JSONObject()

        formats.forEach { (label, mime) ->
            val candidates = HardwareCodecSelector.candidates(mime)
            if (candidates.isNotEmpty()) workingMimes += mime
            if (candidates.any { it.hardware }) workingHardwareMimes += mime
            formatReport.put(
                label,
                JSONObject()
                    .put("mime", mime)
                    .put(
                        "working_encoders",
                        JSONArray().apply {
                            candidates.forEach { candidate ->
                                put(
                                    JSONObject()
                                        .put("name", candidate.name)
                                        .put("hardware", candidate.hardware),
                                )
                            }
                        },
                    )
                    .put(
                        "hardware_decoders",
                        JSONArray(HardwareCodecSelector.hardwareDecoderNames(mime)),
                    ),
            )
        }

        val automaticMime = CodecPriority.bestHardware(workingHardwareMimes, workingMimes)
        assertTrue("Automatic codec choice was not present in the working inventory", automaticMime in workingMimes)
        val versionName = context.packageManager.getPackageInfo(context.packageName, 0).versionName
        val report = JSONObject()
            .put("version", versionName)
            .put("automatic_mime", automaticMime)
            .put("formats", formatReport)
        context.openFileOutput("physical-codec-inventory.json", 0).use { output ->
            output.write(report.toString(2).toByteArray(Charsets.UTF_8))
        }
    }
}
