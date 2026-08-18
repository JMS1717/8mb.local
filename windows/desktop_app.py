"""Launch the full 8mb.local web application as a local Windows desktop app.

The browser UI, FastAPI routes, worker task functions, and FFmpeg command
construction are shared with Docker.  Only Redis/Celery are replaced by the
in-process runtime when ``LOCAL_RUNTIME=1``.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# Generated from the root VERSION file by scripts/set-version.ps1.
DESKTOP_VERSION = "142.0.0.0"


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "8mb.local"
    return Path.home() / ".8mb.local"


def _free_port(requested: int) -> int:
    if requested:
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _load_persisted_environment(data_dir: Path) -> None:
    """Load restart-sensitive values saved by the Settings page."""
    env_path = data_dir / ".env"
    if not env_path.is_file():
        return
    # These values are persisted by the Settings page and must be restored
    # before the native runtime imports its configuration modules.
    allowed = {
        "AUTH_ENABLED", "AUTH_USER", "AUTH_PASS", "WORKER_CONCURRENCY",
        "MEDIA_STORAGE", "MEDIA_MEMORY_LIMIT_GB", "MAX_UPLOAD_SIZE_MB",
        "FILE_RETENTION_HOURS", "HISTORY_ENABLED", "SVTAV1_LP",
        "LOG_LEVEL", "LOG_LEVEL_APP", "LOG_LEVEL_UVICORN",
    }
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in allowed or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _configure_environment(data_dir: Path, bundle_root: Path, port: int) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (data_dir / "outputs").mkdir(parents=True, exist_ok=True)

    _load_persisted_environment(data_dir)
    os.environ["LOCAL_RUNTIME"] = "1"
    os.environ.setdefault("AUTH_ENABLED", "false")
    os.environ.setdefault("HISTORY_ENABLED", "true")
    os.environ.setdefault("WORKER_CONCURRENCY", "auto")
    os.environ["BACKEND_HOST"] = "127.0.0.1"
    os.environ["BACKEND_PORT"] = str(port)
    os.environ["APP_DATA_DIR"] = str(data_dir)
    os.environ["UPLOADS_DIR"] = str(data_dir / "uploads")
    os.environ["OUTPUTS_DIR"] = str(data_dir / "outputs")
    os.environ["ENV_FILE"] = str(data_dir / ".env")
    os.environ["SETTINGS_FILE"] = str(data_dir / "settings.json")
    os.environ["HISTORY_FILE"] = str(data_dir / "history.json")
    os.environ["LOG_FILE"] = str(data_dir / "8mblocal.log")

    # The worker and API invoke FFmpeg by name.  Put the bundled binaries on
    # PATH once so ffprobe, diagnostics, startup probes, and compression all
    # resolve the same version.
    repo_root = Path(__file__).resolve().parents[1]
    if getattr(sys, "frozen", False):
        frontend_dir = bundle_root / "frontend-build"
        bin_dir = bundle_root / "bin"
    else:
        frontend_dir = repo_root / "frontend" / "build"
        bin_dir = Path(__file__).resolve().parent / "ffmpeg" / "bin"
        if not bin_dir.is_dir():
            bin_dir = repo_root / "bin"
    os.environ["FRONTEND_BUILD_DIR"] = str(frontend_dir)
    if bin_dir.is_dir():
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")

    # In a source checkout app is under backend-api; PyInstaller embeds it as
    # the top-level ``app`` package.  These paths are harmless in the frozen
    # build and make the launcher easy to run directly during development.
    # Insert in reverse priority because each entry goes to the front.  The
    # backend package must win over ``worker/app`` when both source trees are
    # on sys.path; frozen builds do not have this name collision.
    for path in (repo_root / "worker", repo_root, repo_root / "backend-api"):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _ensure_stdio(data_dir: Path) -> None:
    """Give windowed PyInstaller builds real stdio streams.

    PyInstaller's ``--windowed`` bootloader sets ``sys.stdout`` and
    ``sys.stderr`` to ``None``.  Uvicorn and a few libraries used during
    FastAPI startup still expect file-like streams; without them the frozen
    executable can stop after importing the app, before it binds its port.
    Keep the streams in the per-user data directory so startup diagnostics
    remain available without opening a console window.
    """
    for attr, filename in (("stdout", "desktop.stdout.log"), ("stderr", "desktop.stderr.log")):
        if getattr(sys, attr, None) is None:
            stream = open(data_dir / filename, "a", encoding="utf-8", buffering=1)
            setattr(sys, attr, stream)


def _wait_until_ready(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    return False


def _wait_and_open_browser(url: str, timeout: float = 30.0) -> None:
    if _wait_until_ready(url, timeout):
        webbrowser.open(url)
    else:
        # A server that is still starting should remain usable; the log tells
        # the user exactly where to open it if startup takes unusually long.
        print(f"8mb.local is starting at {url}", flush=True)


def _show_native_error(message: str) -> None:
    """Surface startup failures from the windowed executable."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "8mb.local", 0x10)
    except Exception:
        logging.getLogger(__name__).error(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the local 8mb.local web app")
    parser.add_argument("--data-dir", type=Path, default=None, help="Persistent app-data directory")
    parser.add_argument("--port", type=int, default=8001, help="Local HTTP port (default: 8001)")
    launch_group = parser.add_mutually_exclusive_group()
    launch_group.add_argument("--browser", action="store_true", help="Open in the default browser instead of a native window")
    launch_group.add_argument("--no-browser", action="store_true", help="Run the local server without opening a window")
    parser.add_argument("--version", action="version", version=f"8mb.local v{DESKTOP_VERSION}")
    args = parser.parse_args(argv)

    port = _free_port(args.port)
    data_dir = (args.data_dir or _default_data_dir()).expanduser().resolve()
    bundle_root = _bundle_root()
    _configure_environment(data_dir, bundle_root, port)
    _ensure_stdio(data_dir)

    # Import only after the runtime environment is complete.  Backend module
    # constants intentionally read these paths once at import time.
    import uvicorn
    from app.main import app

    url = f"http://127.0.0.1:{port}"
    import logging

    logging.getLogger(__name__).info("Starting desktop app at %s (data=%s)", url, data_dir)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)

    if args.no_browser or args.browser:
        if args.browser:
            threading.Thread(
                target=_wait_and_open_browser,
                args=(url,),
                name="8mblocal-browser",
                daemon=True,
            ).start()
        try:
            server.run()
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    # The Windows release uses the installed Edge WebView2 runtime to provide
    # a normal application window. The API and frontend remain the same code
    # served by Docker; this is intentionally only a thin desktop shell.
    server_thread = threading.Thread(target=server.run, name="8mblocal-server", daemon=True)
    server_thread.start()
    if not _wait_until_ready(url):
        server.should_exit = True
        server_thread.join(timeout=10)
        raise RuntimeError(f"8mb.local did not become ready at {url}")

    try:
        import webview

        webview.create_window(
            "8mb.local",
            url,
            width=1200,
            height=820,
            min_size=(760, 600),
            text_select=True,
        )
        webview.start(gui="edgechromium", debug=False)
    except (ImportError, RuntimeError, OSError) as exc:
        # Do not leave an invisible server running indefinitely after an
        # automatic browser fallback. Users who intentionally want browser
        # mode can launch with --browser and manage that server explicitly.
        logging.getLogger(__name__).error("Native window unavailable: %s", exc)
        _show_native_error(
            "8mb.local could not open its native window. The local server "
            "has been stopped. Reinstall Microsoft Edge WebView2, or launch "
            "8mblocal.exe --browser from a terminal."
        )
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
