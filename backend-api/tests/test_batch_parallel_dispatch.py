"""Regression coverage for independent batch scheduling."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

os.environ["AUTH_ENABLED"] = "false"

from app.routers import upload


class _FakeRedis:
    def __init__(self, *, fail_setex: bool = False):
        self.batch_payload = None
        self.deleted = []
        self.fail_setex = fail_setex
        self.cancelled = []

    async def publish(self, *_args, **_kwargs):
        return 1

    async def setex(self, _key, _ttl, value):
        if self.fail_setex:
            raise RuntimeError("redis unavailable")
        self.batch_payload = value

    async def set(self, key, value, **_kwargs):
        self.cancelled.append((key, value))
        return True

    async def delete(self, key, **_kwargs):
        self.deleted.append(key)
        return 1

    async def zrem(self, key, task_id, **_kwargs):
        self.deleted.append(f"{key}:{task_id}")
        return 1


class _FakeSignature:
    def __init__(self, task_id):
        self.task_id = task_id

    def set(self, **_kwargs):
        return self


class _FakeGroup:
    def __init__(self, signatures, *, fail: bool = False):
        self.signatures = signatures
        self.applied = False
        self.fail = fail

    def apply_async(self):
        self.applied = True
        if self.fail:
            raise RuntimeError("broker publish failed")


async def _run_two_file_batch(testcase, temp_path, fake_redis, fake_group):
    def fake_signature(_name, kwargs, immutable):
        testcase.assertTrue(immutable)
        return _FakeSignature(kwargs["job_id"])

    async def fake_save(_file, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"video")

    fake_files = [
        SimpleNamespace(filename="one.mp4", content_type="video/mp4"),
        SimpleNamespace(filename="two.mp4", content_type="video/mp4"),
    ]
    with (
        patch.object(upload, "UPLOADS_DIR", temp_path / "uploads"),
        patch.object(upload, "OUTPUTS_DIR", temp_path / "outputs"),
        patch.object(upload, "redis", fake_redis),
        patch.object(upload, "group", return_value=fake_group),
        patch.object(upload.celery_app, "signature", side_effect=fake_signature),
        patch.object(upload, "save_upload_file", new=fake_save),
        patch.object(upload, "ffprobe", return_value={"duration": 1.0}),
        patch.object(upload, "is_video_upload", return_value=True),
        patch.object(upload, "store_job_metadata", new=lambda *args, **kwargs: asyncio.sleep(0)),
    ):
        return await upload.upload_batch(
            files=fake_files, target_size_mb=1.0, video_codec="libx264",
            audio_codec="aac", audio_bitrate_kbps=128, preset="p6",
            container="mp4", tune="hq", max_width=None, max_height=None,
            start_time=None, end_time=None, force_hw_decode=False,
            fast_mp4_finalize=False, auto_resolution=False,
            min_auto_resolution=240, target_resolution=None, audio_only=False,
            target_video_bitrate_kbps=None, max_output_fps=None,
        )


class TestBatchParallelDispatch(unittest.TestCase):
    def test_batch_record_is_persisted_before_any_task_dispatch(self):
        async def run():
            with tempfile.TemporaryDirectory(prefix="8mb-batch-persist-") as temp_dir:
                temp_path = Path(temp_dir)
                fake_redis = _FakeRedis(fail_setex=True)
                fake_group = _FakeGroup(())
                with self.assertRaises(HTTPException) as raised:
                    await _run_two_file_batch(self, temp_path, fake_redis, fake_group)
                self.assertEqual(raised.exception.status_code, 500)
                self.assertFalse(fake_group.applied)
                self.assertFalse(list((temp_path / "uploads").glob("*")))

        asyncio.run(run())

    def test_partial_group_failure_revokes_every_task_and_records_failure(self):
        async def run():
            with tempfile.TemporaryDirectory(prefix="8mb-batch-dispatch-") as temp_dir:
                temp_path = Path(temp_dir)
                fake_redis = _FakeRedis()
                fake_group = _FakeGroup((), fail=True)
                revoked = []
                with patch.object(
                    upload.celery_app.control, "revoke",
                    side_effect=lambda task_id, **_kwargs: revoked.append(task_id),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        await _run_two_file_batch(self, temp_path, fake_redis, fake_group)
                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(len(revoked), 2)
                self.assertEqual(len(fake_redis.cancelled), 2)
                self.assertIn('"state":"failed"', fake_redis.batch_payload)
                # Keep staging files until cancellation/periodic cleanup so a
                # partially accepted worker cannot race a deleted input.
                self.assertEqual(len(list((temp_path / "uploads").glob("*"))), 2)

        asyncio.run(run())

    def test_batch_uses_group_and_marks_execution_parallel(self):
        async def run():
            with tempfile.TemporaryDirectory(prefix="8mb-batch-test-") as temp_dir:
                temp_path = Path(temp_dir)
                fake_redis = _FakeRedis()
                captured = {}

                def fake_group(*signatures):
                    captured["group"] = _FakeGroup(signatures)
                    return captured["group"]

                def fake_signature(_name, kwargs, immutable):
                    self.assertTrue(immutable)
                    return _FakeSignature(kwargs["job_id"])

                async def fake_save(_file, destination):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b"video")

                fake_files = [
                    SimpleNamespace(filename="one.mp4", content_type="video/mp4"),
                    SimpleNamespace(filename="two.mp4", content_type="video/mp4"),
                ]

                with (
                    patch.object(upload, "UPLOADS_DIR", temp_path / "uploads"),
                    patch.object(upload, "OUTPUTS_DIR", temp_path / "outputs"),
                    patch.object(upload, "redis", fake_redis),
                    patch.object(upload, "group", side_effect=fake_group),
                    patch.object(upload.celery_app, "signature", side_effect=fake_signature),
                    patch.object(upload, "save_upload_file", new=fake_save),
                    patch.object(upload, "ffprobe", return_value={"duration": 1.0}),
                    patch.object(upload, "is_video_upload", return_value=True),
                    patch.object(upload, "store_job_metadata", new=lambda *args, **kwargs: asyncio.sleep(0)),
                ):
                    response = await upload.upload_batch(
                        files=fake_files,
                        target_size_mb=1.0,
                        video_codec="libx264",
                        audio_codec="aac",
                        audio_bitrate_kbps=128,
                        preset="p6",
                        container="mp4",
                        tune="hq",
                        max_width=None,
                        max_height=None,
                        start_time=None,
                        end_time=None,
                        force_hw_decode=False,
                        fast_mp4_finalize=False,
                        auto_resolution=False,
                        min_auto_resolution=240,
                        target_resolution=None,
                        audio_only=False,
                        target_video_bitrate_kbps=None,
                        max_output_fps=None,
                    )

                self.assertEqual(response.item_count, 2)
                self.assertTrue(captured["group"].applied)
                self.assertIn('"execution":"parallel"', fake_redis.batch_payload)

        asyncio.run(run())

    def test_invalid_item_does_not_discard_valid_batch_item(self):
        async def run():
            with tempfile.TemporaryDirectory(prefix="8mb-batch-failure-") as temp_dir:
                temp_path = Path(temp_dir)
                fake_redis = _FakeRedis()
                probe_calls = 0
                captured = {}

                def fake_group(*signatures):
                    captured["group"] = _FakeGroup(signatures)
                    return captured["group"]

                def fake_signature(_name, kwargs, immutable):
                    self.assertTrue(immutable)
                    return _FakeSignature(kwargs["job_id"])

                def fake_probe(_path):
                    nonlocal probe_calls
                    probe_calls += 1
                    if probe_calls == 2:
                        raise RuntimeError("invalid media")
                    return {"duration": 1.0}

                async def fake_save(_file, destination):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b"video")

                fake_files = [
                    SimpleNamespace(filename="one.mp4", content_type="video/mp4"),
                    SimpleNamespace(filename="two.mp4", content_type="video/mp4"),
                ]

                with (
                    patch.object(upload, "UPLOADS_DIR", temp_path / "uploads"),
                    patch.object(upload, "OUTPUTS_DIR", temp_path / "outputs"),
                    patch.object(upload, "redis", fake_redis),
                    patch.object(upload, "group", side_effect=fake_group),
                    patch.object(upload.celery_app, "signature", side_effect=fake_signature),
                    patch.object(upload, "save_upload_file", new=fake_save),
                    patch.object(upload, "ffprobe", side_effect=fake_probe),
                    patch.object(upload, "is_video_upload", return_value=True),
                    patch.object(upload, "store_job_metadata", new=lambda *args, **kwargs: asyncio.sleep(0)),
                ):
                    response = await upload.upload_batch(
                        files=fake_files,
                        target_size_mb=1.0,
                        video_codec="libx264",
                        audio_codec="aac",
                        audio_bitrate_kbps=128,
                        preset="p6",
                        container="mp4",
                        tune="hq",
                        max_width=None,
                        max_height=None,
                        start_time=None,
                        end_time=None,
                        force_hw_decode=False,
                        fast_mp4_finalize=False,
                        auto_resolution=False,
                        min_auto_resolution=240,
                        target_resolution=None,
                        audio_only=False,
                        target_video_bitrate_kbps=None,
                        max_output_fps=None,
                    )

                self.assertEqual(response.item_count, 2)
                self.assertEqual([item.state for item in response.items], ["queued", "failed"])
                self.assertEqual(len(captured["group"].signatures), 1)
                self.assertTrue(captured["group"].applied)
                saved = list((temp_path / "uploads").glob("*"))
                self.assertEqual(len(saved), 1)
                self.assertIn("one.mp4", saved[0].name)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
