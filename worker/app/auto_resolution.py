from typing import Optional, Tuple


def choose_auto_resolution(
    orig_width: Optional[int],
    orig_height: Optional[int],
    orig_video_kbps: Optional[float],
    target_video_kbps: float,
    min_height: int = 240,
    explicit_target_height: Optional[int] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Choose a reasonable target resolution (width,height) given original dimensions,
    original and target video bitrate. The heuristic aims to maintain a minimum
    bits-per-pixel-per-second (bpp) quality and avoid pointlessly encoding high
    resolutions at starvation bitrates.

    Inputs:
      - orig_width, orig_height: original dimensions (optional)
      - orig_video_kbps: source video bitrate if known
      - target_video_kbps: video bitrate budget after audio (kbps)
      - min_height: do not go below this height (default 240)
      - explicit_target_height: if provided, clamp to this height (e.g., 1080)

    Returns (max_width, max_height) for ffmpeg scale filter, or (None,None) to keep original.
    """
    if not orig_width or not orig_height or orig_width <= 0 or orig_height <= 0:
        # Without dimensions, cannot auto-scale
        return (None, None)

    # If user explicitly requested a height, respect it (but not below min)
    if explicit_target_height:
        h = max(min_height, int(explicit_target_height))
        return (None, h)

    # Define common ladder heights (progressive downscale)
    ladder = [2160, 1440, 1080, 720, 480, 360, 240]

    # Use a less aggressive heuristic:
    # - Prefer keeping original unless target density is clearly low
    # - Limit drop to 1-2 rungs unless severely starved
    MIN_OK = 550         # preferred density threshold (kbps per MPix)
    MIN_FALLBACK = 350   # acceptable floor in severe cases

    # Compute current megapixels (per frame)
    orig_mp = (orig_width * orig_height) / 1_000_000.0
    if orig_mp <= 0:
        return (None, None)

    # If original bitrate known, compare density; otherwise derive purely from target
    # Choose the largest height whose megapixel count keeps kbps_per_mpix above threshold.
    def height_to_mp(h: int) -> float:
        # keep aspect ratio using height only; width scales proportionally
        return (orig_width * (h / orig_height) * h) / 1_000_000.0

    # If target_video_kbps is 0 (mute or tiny target), don't upscale
    if target_video_kbps <= 0:
        return (None, min_height)

    # Determine how far we're allowed to drop based on density at original
    kbps_per_mpix_orig = target_video_kbps / orig_mp
    # Find index of original rung (first ladder height <= original height)
    try:
        orig_idx = next(i for i, h in enumerate(ladder) if h <= orig_height)
    except StopIteration:
        orig_idx = len(ladder) - 1

    if kbps_per_mpix_orig >= (MIN_OK + 300):
        # Plenty of bitrate density: keep original height
        return (None, ladder[orig_idx])
    elif kbps_per_mpix_orig >= MIN_OK:
        allowed_drop = 1
    elif kbps_per_mpix_orig >= MIN_FALLBACK:
        allowed_drop = 2
    else:
        allowed_drop = len(ladder)  # unconstrained drop in severe starvation

    # Find the first height meeting MIN_OK; else MIN_FALLBACK; else min rung
    chosen_h: Optional[int] = None
    for h in ladder:
        if h > orig_height:
            continue
        mp = height_to_mp(h)
        if mp <= 0:
            continue
        d = target_video_kbps / mp
        if d >= MIN_OK:
            chosen_h = h
            break
    if chosen_h is None:
        for h in ladder:
            if h > orig_height:
                continue
            mp = height_to_mp(h)
            if mp <= 0:
                continue
            d = target_video_kbps / mp
            if d >= MIN_FALLBACK:
                chosen_h = h
                break
    if chosen_h is None:
        chosen_h = min_height

    # Limit how many rungs we drop from the original rung
    try:
        rec_idx = ladder.index(chosen_h)
    except ValueError:
        rec_idx = orig_idx
    max_drop_idx = min(orig_idx + allowed_drop, len(ladder) - 1)
    limited_idx = min(rec_idx, max_drop_idx)
    limited_h = max(ladder[limited_idx], min_height)

    # Soft floor preferences: prefer 1080p/720p when feasible
    # Only drop below 720p when absolutely required (extremely starved density)
    def d_for(h: int) -> float:
        mp = height_to_mp(h)
        return target_video_kbps / mp if mp > 0 else 0.0

    MIN_OK = 550
    MIN_FALLBACK = 350
    EXTREME_FLOOR = 220  # Only allow <720p if below this at 720p

    # Prefer 1080p when source >= 1440p and 1080p has acceptable density
    if orig_height >= 1440 and limited_h < 1080:
        if d_for(1080) >= MIN_FALLBACK:
            limited_h = 1080

    # Enforce a soft 720p floor unless 720p is also severely starved
    if orig_height >= 720 and limited_h < 720:
        d720 = d_for(720)
        if d720 >= EXTREME_FLOOR:
            limited_h = 720

    # Width will be derived by ffmpeg scale; return height-only constraint
    return (None, limited_h)
