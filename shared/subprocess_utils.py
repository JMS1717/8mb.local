"""Small cross-platform subprocess options shared by local runtimes."""
from __future__ import annotations

import subprocess
import sys


def hidden_process_kwargs() -> dict[str, int]:
    """Prevent console executables from flashing windows in the desktop app."""
    # Tests and embedded runtimes may report a Windows platform while running
    # on a non-Windows host.  The creation flag is only available on Windows;
    # keep the helper safe in that situation while preserving the native flag
    # for real Windows launches.
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
