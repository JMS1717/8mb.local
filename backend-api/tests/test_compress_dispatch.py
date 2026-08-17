import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.models import CompressRequest
from app.routers import compress as compress_router


class CompressDispatchTests(unittest.TestCase):
    def test_metadata_is_recorded_before_dispatch(self):
        events = []

        async def store_metadata(*args, **kwargs):
            events.append('metadata')

        def send_task(*args, **kwargs):
            events.append('dispatch')
            return SimpleNamespace(id=kwargs['task_id'])

        class FakeRedis:
            async def publish(self, *args, **kwargs):
                events.append('publish')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.mp4'
            source.write_bytes(b'video')
            request = CompressRequest(job_id='job-1', filename=source.name)
            fake_celery = SimpleNamespace(send_task=send_task)
            with patch.object(compress_router, 'resolve_uploaded_path', return_value=source), \
                    patch.object(compress_router, 'OUTPUTS_DIR', root), \
                    patch.object(compress_router, 'store_job_metadata', new=store_metadata), \
                    patch.object(compress_router, 'celery_app', fake_celery), \
                    patch.object(compress_router, 'redis', FakeRedis()):
                result = asyncio.run(compress_router.compress(request))

        self.assertEqual(events, ['metadata', 'dispatch', 'publish'])
        self.assertTrue(result['task_id'])

    def test_metadata_failure_does_not_dispatch_or_leave_source(self):
        async def fail_metadata(*args, **kwargs):
            raise RuntimeError('metadata unavailable')

        dispatched = []

        def send_task(*args, **kwargs):
            dispatched.append(True)
            return SimpleNamespace(id='unexpected')

        class FakeRedis:
            async def delete(self, *args, **kwargs):
                return 1

            async def zrem(self, *args, **kwargs):
                return 1

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.mp4'
            source.write_bytes(b'video')
            request = CompressRequest(job_id='job-1', filename=source.name)
            fake_celery = SimpleNamespace(send_task=send_task)
            with patch.object(compress_router, 'resolve_uploaded_path', return_value=source), \
                    patch.object(compress_router, 'OUTPUTS_DIR', root), \
                    patch.object(compress_router, 'store_job_metadata', new=fail_metadata), \
                    patch.object(compress_router, 'celery_app', fake_celery), \
                    patch.object(compress_router, 'redis', FakeRedis()):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(compress_router.compress(request))

        self.assertEqual(raised.exception.status_code, 500)
        self.assertFalse(source.exists())
        self.assertEqual(dispatched, [])


if __name__ == '__main__':
    unittest.main()
