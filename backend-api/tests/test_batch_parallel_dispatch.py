"""Regression coverage for independent batch scheduling."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["AUTH_ENABLED"] = "false"

from app.routers import upload


class _FakeRedis:
    def __init__(self):
        self.batch_payload = None
        self.deleted = []

    async def publish(self, *_args, **_kwargs):
        return 1

    async def setex(self, _key, _ttl, value):
        self.batch_payload = value

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
    def __init__(self, signatures):
        self.signatures = signatures
        self.applied = False

    def apply_async(self):
        self.applied = True


class TestBatchParallelDispatch(unittest.TestCase):
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

    def test_batch_failure_cleans_prior_metadata_and_files(self):
        async def run():
            with tempfile.TemporaryDirectory(prefix="8mb-batch-failure-") as temp_dir:
                temp_path = Path(temp_dir)
                fake_redis = _FakeRedis()
                probe_calls = 0

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
                    patch.object(upload, "save_upload_file", new=fake_save),
                    patch.object(upload, "ffprobe", side_effect=fake_probe),
                    patch.object(upload, "is_video_upload", return_value=True),
                    patch.object(upload, "store_job_metadata", new=lambda *args, **kwargs: asyncio.sleep(0)),
                ):
                    with self.assertRaises(Exception):
                        await upload.upload_batch(
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

                self.assertFalse(list((temp_path / "uploads").glob("*")))
                self.assertTrue(any(key.startswith("job:") for key in fake_redis.deleted))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
