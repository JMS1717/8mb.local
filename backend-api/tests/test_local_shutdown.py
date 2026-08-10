"""Regression coverage for clean native-desktop worker shutdown."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class TestLocalShutdown(unittest.TestCase):
    def test_shutdown_cancels_active_work_waits_and_rejects_new_tasks(self):
        repo_root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent("""
            import time
            from app.celery_app import celery_app
            from shared.local_runtime import ensure_task, get_sync_redis

            task_id = "shutdown-test"
            ensure_task(task_id)

            def active_work():
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if get_sync_redis().get(f"cancel:{task_id}") == "1":
                        return "cancelled"
                    time.sleep(0.01)
                raise RuntimeError("shutdown did not request cancellation")

            future = celery_app._executor.submit(active_work)
            celery_app._futures[task_id] = future
            celery_app.shutdown()
            assert future.done()
            assert future.result() == "cancelled"
            try:
                celery_app.send_task("unused", task_id="late")
            except RuntimeError as exc:
                assert "shutting down" in str(exc)
            else:
                raise AssertionError("shutdown runtime accepted a new task")
        """)
        with tempfile.TemporaryDirectory(prefix="8mb-local-shutdown-") as data_dir:
            env = os.environ.copy()
            env.update({
                "LOCAL_RUNTIME": "true",
                "APP_DATA_DIR": data_dir,
                "PYTHONPATH": os.pathsep.join([
                    str(repo_root / "backend-api"),
                    str(repo_root),
                ]),
            })
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=repo_root / "backend-api",
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
