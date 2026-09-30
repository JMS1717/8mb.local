package com.jms1717.eightmblocal

import android.graphics.Bitmap
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Captures deterministic, personal-media-free screenshots for the Play listing. */
@RunWith(AndroidJUnit4::class)
class StoreListingScreenshotTest {
    @get:Rule val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun captureMainCodecAndAdvancedScreens() {
        composeRule.activity.apply {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        composeRule.waitUntil(30_000) {
            composeRule.onAllNodesWithText("Choose video", substring = true).fetchSemanticsNodes().isNotEmpty() &&
                composeRule.onAllNodesWithText("Testing MediaCodec hardware…").fetchSemanticsNodes().isEmpty()
        }
        capture("store-01-main.png")

        composeRule.onNodeWithText("HEVC").performScrollTo()
        composeRule.waitForIdle()
        capture("store-02-codecs.png")

        composeRule.onNodeWithText("Show advanced options", substring = true)
            .performScrollTo()
            .performClick()
        composeRule.onNodeWithText("Audio").performScrollTo()
        composeRule.waitForIdle()
        capture("store-03-advanced.png")
    }

    private fun capture(name: String) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        File(context.filesDir, name).outputStream().use { output ->
            InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
                .compress(Bitmap.CompressFormat.PNG, 100, output)
        }
    }
}
