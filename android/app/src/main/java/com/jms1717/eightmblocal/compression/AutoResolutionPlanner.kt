package com.jms1717.eightmblocal.compression

object AutoResolutionPlanner {
    private val ladder = listOf(2160, 1440, 1080, 720, 480, 360, 240)

    /** Kotlin port of the desktop worker's auto-resolution heuristic. */
    fun chooseHeight(width: Int, height: Int, targetVideoKbps: Double, minHeight: Int = 240): Int? {
        if (width <= 0 || height <= 0) return null
        if (targetVideoKbps <= 0) return minHeight
        val originalMegapixels = width * height / 1_000_000.0
        if (originalMegapixels <= 0) return null
        val originalIndex = ladder.indexOfFirst { it <= height }.let { if (it < 0) ladder.lastIndex else it }
        val density = targetVideoKbps / originalMegapixels
        val allowedDrop = when {
            density >= 850 -> 0
            density >= 550 -> 1
            density >= 350 -> 2
            else -> ladder.size
        }
        fun densityAt(candidateHeight: Int): Double {
            val megapixels = width * (candidateHeight.toDouble() / height) * candidateHeight / 1_000_000.0
            return if (megapixels > 0) targetVideoKbps / megapixels else 0.0
        }
        var chosen = ladder.firstOrNull { it <= height && densityAt(it) >= 550 }
            ?: ladder.firstOrNull { it <= height && densityAt(it) >= 350 }
            ?: minHeight
        val recommendedIndex = ladder.indexOf(chosen).let { if (it < 0) originalIndex else it }
        val maximumDropIndex = (originalIndex + allowedDrop).coerceAtMost(ladder.lastIndex)
        chosen = ladder[minOf(recommendedIndex, maximumDropIndex)].coerceAtLeast(minHeight)
        if (height >= 1440 && chosen < 1080 && densityAt(1080) >= 350) chosen = 1080
        if (height >= 720 && chosen < 720 && densityAt(720) >= 220) chosen = 720
        return chosen.coerceAtMost(height)
    }
}
