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

    # Estimate bpp at original resolution
    # bpp = bits per pixel per frame*second approx: bitrate / (w*h*fps)
    # We don't always have FPS; use a bitrate-per-megapixel-per-second heuristic.
    # We'll target a minimum of ~0.07 bits/pixel/sec for acceptable quality.
    # Map that to kbps per megapixel per second.
    MIN_KBPS_PER_MPIX = 700  # heuristic floor

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

    chosen_h = None
    for h in ladder:
        if h > orig_height:
            continue  # don't upscale
        mp = height_to_mp(h)
        if mp <= 0:
            continue
        kbps_per_mpix = target_video_kbps / mp
        if kbps_per_mpix >= MIN_KBPS_PER_MPIX:
            chosen_h = h
            break

    if chosen_h is None:
        chosen_h = min_height

    chosen_h = max(chosen_h, min_height)
    # Width will be derived by ffmpeg scale; return height-only constraint
    return (None, chosen_h)
