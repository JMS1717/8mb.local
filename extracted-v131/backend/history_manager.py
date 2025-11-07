"""
Compression history manager for 8mb.local
Tracks compression jobs (metadata only, not files)
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional


HISTORY_FILE = Path("/app/history.json")


def _read_history() -> List[Dict]:
    """Read history from JSON file"""
    if not HISTORY_FILE.exists():
        return []
    
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _write_history(history: List[Dict]):
    """Write history to JSON file"""
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
        os.chmod(HISTORY_FILE, 0o600)
    except IOError:
        pass  # Silently fail if can't write


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
) -> Dict:
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
    
    history = _read_history()
    history.insert(0, entry)  # Add to beginning (newest first)
    
    # Keep only last 100 entries
    if len(history) > 100:
        history = history[:100]
    
    _write_history(history)
    return entry


def get_history(limit: Optional[int] = None) -> List[Dict]:
    """Get compression history"""
    history = _read_history()
    
    if limit and limit > 0:
        return history[:limit]
    
    return history


def get_history_entry(task_id: str) -> Optional[Dict]:
    """Get a specific history entry by task_id, or None if not found."""
    try:
        history = _read_history()
        for entry in history:
            if entry.get('task_id') == task_id:
                return entry
    except Exception:
        pass
    return None


def clear_history():
    """Clear all history"""
    _write_history([])


def delete_history_entry(task_id: str) -> bool:
    """Delete a specific history entry by task_id"""
    history = _read_history()
    original_len = len(history)
    
    history = [entry for entry in history if entry.get('task_id') != task_id]
    
    if len(history) < original_len:
        _write_history(history)
        return True
    
    return False
