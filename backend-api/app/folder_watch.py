"""Cross-platform polling folder watcher backed by the existing Celery job path.

Folder Watch never performs transcoding itself and never exposes a public
arbitrary-path compression endpoint.  It discovers stable files, dispatches
the same ``worker.worker.compress_video`` task used by the upload UI, and only
deletes or moves an original after the worker output passes ffprobe validation.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import settings_manager
from .celery_app import celery_app
from .deps import VIDEO_EXTENSIONS, build_output_name, ffprobe, store_job_metadata

logger = logging.getLogger(__name__)

_GENERATED_MARKER = '_8mblocal_'
_IGNORED_SUFFIXES = {'.part', '.partial', '.tmp', '.crdownload', '.download'}


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _candidate_files(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        iterator = root.rglob('*')
    else:
        iterator = root.iterdir()
    result: list[Path] = []
    for path in iterator:
        try:
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if path.suffix.lower() in _IGNORED_SUFFIXES or _GENERATED_MARKER in path.name:
                continue
            if 'processed' in {part.lower() for part in path.relative_to(root).parts[:-1]}:
                continue
            result.append(path)
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: _key(item))


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f'{path.stem}_{index}{path.suffix}')
        if not candidate.exists():
            return candidate
    return path.with_name(f'{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}')


class FolderWatchService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._stable: dict[str, tuple[int, int, float]] = {}
        self._last_scan = 0.0
        self._last_error = ''
        self._queued_total = 0

    def status(self) -> dict[str, Any]:
        return {
            'running': bool(self._task and not self._task.done()),
            'last_scan': self._last_scan or None,
            'last_error': self._last_error or None,
            'queued_count': len(self._pending),
            'queued_total': self._queued_total,
        }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        await self.reload()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def reload(self) -> None:
        await self.stop()
        config = settings_manager.get_folder_watch_settings()
        if config.get('enabled'):
            self._restore_pending_from_state()
            self._task = asyncio.create_task(self._run(config), name='8mblocal-folder-watch')
            logger.info('folder-watch enabled: input=%s recursive=%s', config.get('input_folder'), config.get('recursive'))
        else:
            logger.info('folder-watch disabled')

    def _restore_pending_from_state(self) -> None:
        """Rehydrate watcher-owned jobs after an API/desktop restart.

        The Celery task and the per-file watcher state outlive the in-process
        ``_pending`` dictionary. Restoring only queued/running records lets the
        normal reconciliation path validate the output and apply the user's
        original-file action when the worker finishes.
        """
        try:
            state = settings_manager.get_folder_watch_state()
        except Exception as exc:
            logger.debug('folder-watch: persisted state unavailable: %s', exc)
            return
        if not isinstance(state, dict):
            return
        restored = 0
        for record in state.values():
            if not isinstance(record, dict):
                continue
            if str(record.get('status') or '') not in {'queued', 'running'}:
                continue
            task_id = str(record.get('task_id') or '')
            if not task_id or not record.get('input_path') or not record.get('output_path'):
                continue
            if task_id in self._pending:
                continue
            self._pending[task_id] = dict(record)
            restored += 1
        if restored:
            self._queued_total += restored
            logger.info('folder-watch: restored %d pending job(s) after restart', restored)

    async def _run(self, config: dict[str, Any]) -> None:
        try:
            while True:
                await self._reconcile_pending()
                await self._scan(config)
                await asyncio.sleep(int(config.get('poll_interval_seconds', 5)))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._last_error = 'Folder Watch stopped after an unexpected error; check application logs.'
            logger.exception('folder-watch loop stopped unexpectedly')

    async def _scan(self, config: dict[str, Any]) -> None:
        root = Path(str(config.get('input_folder', ''))).expanduser()
        if not root.is_dir():
            self._last_error = f'Input folder is unavailable: {root}'
            return
        self._last_error = ''
        self._last_scan = time.time()
        state = settings_manager.get_folder_watch_state()
        baseline = float(config.get('baseline_ts') or 0.0)
        files = await asyncio.to_thread(_candidate_files, root, bool(config.get('recursive')))
        queued_this_scan = 0
        for path in files:
            if queued_this_scan >= 10:
                break
            try:
                stat = path.stat()
            except OSError:
                continue
            if config.get('existing_files') == 'new_only' and stat.st_mtime <= baseline:
                continue
            key = _key(path)
            fingerprint = (int(stat.st_size), int(stat.st_mtime_ns))
            previous = self._stable.get(key)
            now = time.monotonic()
            if not previous or previous[:2] != fingerprint:
                self._stable[key] = (*fingerprint, now)
                continue
            if now - previous[2] < int(config.get('stable_seconds', 5)):
                continue
            if key in self._pending:
                continue
            saved = state.get(key, {})
            if saved.get('size') == fingerprint[0] and saved.get('mtime_ns') == fingerprint[1] and saved.get('status') in {
                'queued', 'running', 'succeeded', 'failed',
            }:
                continue
            try:
                await self._dispatch(path, stat, config)
                queued_this_scan += 1
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning('folder-watch: could not queue %s: %s', path, exc)

    async def _dispatch(self, path: Path, stat: os.stat_result, config: dict[str, Any]) -> None:
        profiles = settings_manager.get_preset_profiles()
        profile_name = config.get('profile') or profiles.get('default')
        profile = next((item for item in profiles.get('profiles', []) if item.get('name') == profile_name), None)
        if profile is None:
            raise RuntimeError('Folder Watch has no valid compression profile')
        await asyncio.to_thread(ffprobe, path)

        fingerprint = (int(stat.st_size), int(stat.st_mtime_ns))
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'8mblocal-folder-watch:{_key(path)}:{fingerprint[0]}:{fingerprint[1]}'))
        job_id = str(uuid.uuid4())
        output_root = path.parent if config.get('output_mode') == 'same_folder' else Path(str(config.get('output_folder'))).expanduser()
        output_root.mkdir(parents=True, exist_ok=True)
        output_path = output_root / build_output_name(path, task_id, str(profile.get('container', 'mp4')))
        record = {
            'task_id': task_id,
            'job_id': job_id,
            'input_path': str(path),
            'output_path': str(output_path),
            'size': fingerprint[0],
            'mtime_ns': fingerprint[1],
            'status': 'queued',
        }
        settings_manager.update_folder_watch_state(_key(path), record)
        try:
            # Register the job before dispatching. A fast worker can finish
            # before the watcher returns, and a metadata failure must never
            # leave an untracked output behind.
            await store_job_metadata(
                task_id,
                job_id,
                path.name,
                float(profile.get('target_mb', 19.7)),
                str(profile.get('video_codec', 'h264_nvenc')),
                str(path),
                str(output_path),
            )
            await asyncio.to_thread(
                celery_app.send_task,
                'worker.worker.compress_video',
                task_id=task_id,
                kwargs={
                    'job_id': job_id,
                    'input_path': str(path),
                    'output_path': str(output_path),
                    'target_size_mb': float(profile.get('target_mb', 19.7)),
                    'video_codec': str(profile.get('video_codec', 'h264_nvenc')),
                    'audio_codec': str(profile.get('audio_codec', 'libopus')),
                    'audio_bitrate_kbps': int(profile.get('audio_kbps', 128)),
                    'preset': str(profile.get('preset', 'p6')),
                    'tune': str(profile.get('tune', 'hq')),
                    'max_output_fps': profile.get('max_output_fps'),
                },
            )
        except Exception:
            settings_manager.update_folder_watch_state(_key(path), {**record, 'status': 'failed'})
            raise
        self._pending[task_id] = record
        self._queued_total += 1
        logger.info('folder-watch: queued %s as task=%s output=%s', path, task_id[:8], output_path)

    async def _reconcile_pending(self) -> None:
        for task_id, record in list(self._pending.items()):
            try:
                state = await asyncio.to_thread(lambda: celery_app.AsyncResult(task_id).state)
            except Exception as exc:
                logger.debug('folder-watch: task state unavailable %s: %s', task_id[:8], exc)
                continue
            if state in {'PENDING', 'RECEIVED', 'STARTED', 'RETRY'}:
                continue
            if state == 'SUCCESS':
                output = Path(record['output_path'])
                try:
                    await asyncio.to_thread(ffprobe, output)
                    if not output.exists() or output.stat().st_size <= 0:
                        raise RuntimeError('validated output is empty')
                    await asyncio.to_thread(self._apply_original_behavior, Path(record['input_path']), output)
                    record = {**record, 'status': 'succeeded'}
                    settings_manager.update_folder_watch_state(_key(Path(record['input_path'])), record)
                except Exception as exc:
                    record = {**record, 'status': 'failed', 'error': f'output validation/action failed: {exc}'}
                    settings_manager.update_folder_watch_state(_key(Path(record['input_path'])), record)
                    logger.warning('folder-watch: output validation failed for %s: %s', record['input_path'], exc)
                self._pending.pop(task_id, None)
            elif state in {'FAILURE', 'REVOKED', 'CANCELED'}:
                record = {**record, 'status': 'failed', 'error': f'worker state {state}'}
                settings_manager.update_folder_watch_state(_key(Path(record['input_path'])), record)
                self._pending.pop(task_id, None)

    def _apply_original_behavior(self, source: Path, output: Path) -> None:
        config = settings_manager.get_folder_watch_settings()
        behavior = config.get('original_behavior', 'keep')
        if behavior == 'delete':
            source.unlink(missing_ok=True)
        elif behavior == 'move' and source.exists():
            destination_root = output.parent / 'processed'
            destination_root.mkdir(parents=True, exist_ok=True)
            source.rename(_unique_destination(destination_root / source.name))


folder_watch_service = FolderWatchService()
