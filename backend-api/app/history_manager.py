"""
Compression history manager for 8mb.local
Tracks compression jobs (metadata only, not files)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings

HISTORY_FILE = Path(
    os.getenv("HISTORY_FILE", str(Path(settings.APP_DATA_DIR) / "history.json"))
)
HISTORY_LOCK_FILE = HISTORY_FILE.with_name(f".{HISTORY_FILE.name}.lock")
_HISTORY_LOCK = threading.RLock()


@contextmanager
def _history_file_lock():
    """Serialize history read/modify/write operations across worker processes."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_LOCK_FILE, "a+b") as lock_handle:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"0")
            lock_handle.flush()
        lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _read_history_unlocked() -> List[Dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as history_handle:
            value = json.load(history_handle)
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, OSError, TypeError):
        return []


def _write_history_unlocked(history: List[Dict[str, Any]]) -> None:
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(HISTORY_FILE.parent),
            prefix=f".{HISTORY_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(history, temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, HISTORY_FILE)
        temporary_name = None
        try:
            os.chmod(HISTORY_FILE, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    finally:
        if temporary_name:
            try:
                os.remove(temporary_name)
            except OSError:
                pass


def _read_history() -> List[Dict[str, Any]]:
    """Read history from JSON file"""
    with _HISTORY_LOCK:
        with _history_file_lock():
            return _read_history_unlocked()


def _write_history(history: List[Dict[str, Any]]) -> None:
    """Write history to JSON file"""
    with _HISTORY_LOCK:
        with _history_file_lock():
            _write_history_unlocked(history)


def add_history_entry(
    filename: str,
    original_size_mb: float,
    compressed_size_mb: float,
    video_codec: str,
    audio_codec: str,
    target_mb: float,
    preset: str,
    duration: float,
    task_id: str,
    *,
    container: Optional[str] = None,
    tune: Optional[str] = None,
    audio_bitrate_kbps: Optional[int] = None,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    encoder: Optional[str] = None,
    output_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a compression history entry"""
    entry = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'filename': filename,
        'original_size_mb': round(original_size_mb, 2),
        'compressed_size_mb': round(compressed_size_mb, 2),
        'reduction_percent': round((1 - compressed_size_mb / original_size_mb) * 100, 1) if original_size_mb > 0 else 0,
        'video_codec': video_codec,
        'audio_codec': audio_codec,
        'target_mb': target_mb,
        'preset': preset,
        'duration_seconds': round(duration, 1),
        'task_id': task_id
    }

    # Optional settings for richer history context
    if container is not None:
        entry['container'] = container
    if tune is not None:
        entry['tune'] = tune
    if audio_bitrate_kbps is not None:
        entry['audio_bitrate_kbps'] = int(audio_bitrate_kbps)
    if max_width is not None:
        entry['max_width'] = int(max_width)
    if max_height is not None:
        entry['max_height'] = int(max_height)
    if start_time is not None:
        entry['start_time'] = start_time
    if end_time is not None:
        entry['end_time'] = end_time
    if encoder is not None:
        entry['encoder'] = encoder
    if output_filename is not None:
        entry['output_filename'] = Path(output_filename).name

    with _HISTORY_LOCK:
        with _history_file_lock():
            history = _read_history_unlocked()
            history.insert(0, entry)  # Add to beginning (newest first)

            # Keep only last 100 entries
            if len(history) > 100:
                history = history[:100]

            _write_history_unlocked(history)
    return entry


def get_history(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get compression history"""
    history = _read_history()

    if limit and limit > 0:
        return history[:limit]

    return history


def get_history_entry(task_id: str) -> Optional[Dict[str, Any]]:
    """Get a specific history entry by task_id, or None if not found."""
    try:
        history = _read_history()
        for entry in history:
            if entry.get('task_id') == task_id:
                return entry
    except Exception:
        pass
    return None


def clear_history() -> None:
    """Clear all history"""
    with _HISTORY_LOCK:
        with _history_file_lock():
            _write_history_unlocked([])


def delete_history_entry(task_id: str) -> bool:
    """Delete a specific history entry by task_id"""
    with _HISTORY_LOCK:
        with _history_file_lock():
            history = _read_history_unlocked()
            original_len = len(history)

            history = [entry for entry in history if entry.get('task_id') != task_id]

            if len(history) < original_len:
                _write_history_unlocked(history)
                return True

            return False
