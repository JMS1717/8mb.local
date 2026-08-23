package com.jms1717.eightmblocal

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import org.junit.Rule
import org.junit.Test

class MainActivityTest {
    @get:Rule val composeRule = createAndroidComposeRule<MainActivity>()

    @Test fun mainScreenOffersVideoSelection() {
        composeRule.onNodeWithText("8mb.local").assertIsDisplayed()
        composeRule.onNodeWithText("Choose video").assertIsDisplayed()
    }
}
