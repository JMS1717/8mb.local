package com.jms1717.eightmblocal.compression

import kotlin.math.floor

object AutoAudioPlanner {
    private val allowedKbps = listOf(256, 192, 160, 128, 96, 64, 48, 32)

    /** Mirrors the desktop rule that reserves at least 100 kbps for video. */
    fun chooseKbps(targetMb: Double, durationMs: Long, preferredKbps: Int): Int {
        if (durationMs <= 0 || targetMb <= 0) return preferredKbps
        val totalKbps = targetMb * 8192.0 / (durationMs / 1000.0)
        if (totalKbps - preferredKbps >= 100.0) return preferredKbps
        val maximumAudio = floor(totalKbps - 100.0).toInt().coerceAtLeast(32)
        return allowedKbps.firstOrNull { it <= maximumAudio && it <= preferredKbps } ?: 32
    }
}
