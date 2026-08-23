package com.jms1717.eightmblocal.compression

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SizePlannerTest {
    @Test fun targetBitrateReservesAacAudio() {
        assertEquals(990_481, SizePlanner.videoBitrate(8.0, 60_000))
    }

    @Test fun overageToleranceIsStrictlyTwoPercent() {
        assertFalse(SizePlanner.exceedsTolerance(10_200, 10_000))
        assertTrue(SizePlanner.exceedsTolerance(10_201, 10_000))
    }

    @Test fun adjustedBitrateIncludesSafetyMargin() {
        assertEquals(784_000, SizePlanner.adjustedBitrate(1_000_000, 8_000_000, 10_000_000))
    }
}
