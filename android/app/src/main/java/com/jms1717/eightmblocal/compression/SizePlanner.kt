package com.jms1717.eightmblocal.compression

import kotlin.math.floor

object SizePlanner {
    fun videoBitrate(targetMb: Double, durationMs: Long, audioBitrate: Int = 128_000): Int {
        require(targetMb > 0 && durationMs > 0)
        val totalBits = targetMb * 1024.0 * 1024.0 * 8.0
        val audioBits = audioBitrate.toDouble() * durationMs / 1000.0
        return floor(
            (totalBits - audioBits).coerceAtLeast(totalBits * 0.25) / (durationMs / 1000.0),
        ).toInt()
    }

    fun adjustedBitrate(current: Int, targetBytes: Long, actualBytes: Long): Int =
        floor(current * targetBytes.toDouble() / actualBytes.toDouble() * 0.98).toInt()

    fun exceedsTolerance(actualBytes: Long, targetBytes: Long): Boolean =
        actualBytes > floor(targetBytes * 1.02).toLong()
}
