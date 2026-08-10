"""Small cross-platform subprocess options shared by local runtimes."""
from __future__ import annotations

import subprocess
import sys


def hidden_process_kwargs() -> dict[str, int]:
    """Prevent console executables from flashing windows in the desktop app."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
