"""Platform-specific QSV software-frame filter selection."""
from __future__ import annotations

import sys


def qsv_input_filter(platform: str | None = None) -> str:
    """Return the safe software-decode-to-QSV filter for this platform.

    Linux oneVPL requires an explicitly sized hardware frame pool. Native
    Windows QSV accepts NV12 software frames and uploads them internally;
    forcing a D3D11 hwupload can fail on real vertical/rotated AV1 surfaces
    even though a small synthetic initialization probe succeeds.
    """
    current = platform or sys.platform
    if current == "win32":
        return "format=nv12"
    return "format=nv12,hwupload=extra_hw_frames=64"


def qsv_probe_size(platform: str | None = None) -> str:
    """Exercise a realistic vertical surface in native Windows probes."""
    return "1080x1920" if (platform or sys.platform) == "win32" else "256x256"
