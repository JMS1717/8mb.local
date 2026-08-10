"""Local runtime smoke tests; run with LOCAL_RUNTIME=1."""
import os
import unittest


@unittest.skipUnless(
    os.getenv("LOCAL_RUNTIME", "").strip().lower() in {"1", "true", "yes", "on"},
    "local runtime is exercised in the dedicated LOCAL_RUNTIME test job",
)
class TestLocalCelery(unittest.TestCase):
    def test_real_worker_task_runs_without_redis(self):
        from app.celery_app import celery_app

        result = celery_app.send_task("worker.worker.get_hardware_info")
        value = result.get(timeout=30)
        self.assertEqual(result.state, "SUCCESS")
        self.assertIn("available_encoders", value)


if __name__ == "__main__":
    unittest.main()
