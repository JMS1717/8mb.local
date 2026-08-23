package com.jms1717.eightmblocal

import android.app.Activity
import android.app.Instrumentation.ActivityResult
import android.content.ContentValues
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.espresso.intent.Intents.intending
import androidx.test.espresso.intent.matcher.IntentMatchers.hasAction
import androidx.test.espresso.intent.rule.IntentsRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.runner.RunWith
import java.io.File

/** Records a real, synthetic-media workflow for Play's foreground-service declaration. */
@RunWith(AndroidJUnit4::class)
class ForegroundServiceDemoTest {
    private val composeRule = createAndroidComposeRule<MainActivity>()
    private val intentsRule = IntentsRule()
    private var demoInputUri: Uri? = null
    private var demoInputFile: File? = null

    @get:Rule
    val rules: RuleChain = RuleChain.outerRule(intentsRule).around(composeRule)

    @Before
    fun requireExplicitRecordingMode() {
        val arguments: Bundle = InstrumentationRegistry.getArguments()
        assumeTrue("Only runs when explicitly recording Play evidence", arguments.getString("recordStoreDemo") == "true")
    }

    @After
    fun leavePreviewVisibleBriefly() {
        Thread.sleep(1_000)
        demoInputUri?.let {
            InstrumentationRegistry.getInstrumentation().targetContext.contentResolver.delete(it, null, null)
        }
        demoInputFile?.delete()
    }

    @Test
    fun selectSyntheticVideoCompressAndOpenPreview() {
        composeRule.activity.apply {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        val inputUri = createSyntheticVideoEntry()
        intending(hasAction(MediaStore.ACTION_PICK_IMAGES)).respondWith(
            ActivityResult(
                Activity.RESULT_OK,
                Intent().setData(inputUri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
            ),
        )
        composeRule.waitUntil(30_000) {
            composeRule.onAllNodesWithText("Choose video", substring = true).fetchSemanticsNodes().isNotEmpty() &&
                composeRule.onAllNodesWithText("Testing MediaCodec hardware…").fetchSemanticsNodes().isEmpty()
        }
        Thread.sleep(1_500)
        composeRule.onNodeWithText("Choose video", substring = true).performClick()
        composeRule.waitUntil(10_000) {
            composeRule.onAllNodesWithText("Selected from Photos").fetchSemanticsNodes().isNotEmpty()
        }
        Thread.sleep(1_500)
        composeRule.onNodeWithText("Compress and save", substring = true).performScrollTo().performClick()
        composeRule.waitUntil(90_000) {
            composeRule.onAllNodesWithText("Preview & share").fetchSemanticsNodes().isNotEmpty()
        }
        Thread.sleep(2_000)
        composeRule.onNodeWithText("Preview & share").performScrollTo().performClick()
        composeRule.onNodeWithText("Preview compressed video").assertExists()
        Thread.sleep(4_000)
    }

    private fun createSyntheticVideoEntry(): Uri {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val source = File(context.cacheDir, "8mblocal-demo-input.mp4")
        SyntheticVideo.writeH264(source)
        check(source.length() > 1_000) { "Synthetic demo video is missing from app storage" }
        demoInputFile = source
        val resolver = context.contentResolver
        val uri = resolver.insert(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, "8mblocal-demo-input.mp4")
                put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4")
                put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_MOVIES}/8mb.local-test")
            },
        ) ?: error("Could not create synthetic Photo Picker entry")
        resolver.openOutputStream(uri, "w")!!.use { output -> source.inputStream().use { it.copyTo(output) } }
        demoInputUri = uri
        return uri
    }
}
