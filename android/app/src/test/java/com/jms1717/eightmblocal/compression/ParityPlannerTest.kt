package com.jms1717.eightmblocal.compression

import org.junit.Assert.assertEquals
import org.junit.Test

class ParityPlannerTest {
    @Test
    fun autoAudioKeepsDesktopDefaultWhenThereIsRoom() {
        assertEquals(128, AutoAudioPlanner.chooseKbps(19.7, 60_000, 128))
    }

    @Test
    fun autoAudioDownshiftsForTinyTargets() {
        assertEquals(32, AutoAudioPlanner.chooseKbps(1.0, 120_000, 128))
    }

    @Test
    fun autoResolutionKeepsHighDensitySource() {
        assertEquals(2160, AutoResolutionPlanner.chooseHeight(3840, 2160, 8_000.0, 240))
    }

    @Test
    fun autoResolutionProtectsStarvedVideo() {
        assertEquals(720, AutoResolutionPlanner.chooseHeight(3840, 2160, 500.0, 240))
    }
}
