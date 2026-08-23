package com.jms1717.eightmblocal

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import org.junit.Rule
import org.junit.Test

class MainActivityTest {
    @get:Rule val composeRule = createAndroidComposeRule<MainActivity>()

    @Test fun mainScreenOffersVideoSelection() {
        composeRule.onNodeWithText("8mb.local").assertIsDisplayed()
        composeRule.onNodeWithText("Choose video", substring = true).assertIsDisplayed()
        composeRule.onNodeWithText("Choose multiple videos", substring = true).assertIsDisplayed()
        composeRule.onNodeWithText("Target").assertIsDisplayed()
        composeRule.onNodeWithText("Video codec").assertIsDisplayed()
        composeRule.onNodeWithText("Show advanced options", substring = true).performScrollTo().performClick()
        composeRule.onNodeWithText("Frame-rate cap").performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText("Auto audio bitrate").performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText("Trim").performScrollTo().assertIsDisplayed()
    }
}
