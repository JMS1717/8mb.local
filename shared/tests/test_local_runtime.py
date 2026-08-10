import asyncio
import json
import unittest

from shared.local_runtime import (
    cancel_task,
    get_async_redis,
    get_sync_redis,
    record_worker_event,
    reset_for_tests,
    task_snapshot,
    update_task,
)


class TestLocalRuntime(unittest.TestCase):
    def setUp(self):
        reset_for_tests()

    def test_key_value_and_sorted_set_operations(self):
        redis = get_sync_redis()
        self.assertTrue(redis.setex("key", 60, "value"))
        self.assertEqual(redis.get("key"), "value")
        redis.zadd("jobs:active", {"b": 2, "a": 1})
        self.assertEqual(redis.zrange("jobs:active", 0, -1), ["a", "b"])
        self.assertEqual(redis.zrem("jobs:active", "a"), 1)
        self.assertEqual(redis.zrange("jobs:active", 0, -1), ["b"])

    def test_pubsub_delivers_messages_and_closes(self):
        async def run():
            redis = get_async_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe("progress:test")
            await redis.publish("progress:test", json.dumps({"type": "progress"}))
            message = await asyncio.wait_for(pubsub.listen().__anext__(), timeout=1)
            self.assertEqual(message["type"], "message")
            self.assertIn("progress", message["data"])
            await pubsub.unsubscribe("progress:test")
            await pubsub.close()

        asyncio.run(run())

    def test_worker_event_updates_task_and_job_views(self):
        redis = get_sync_redis()
        redis.setex("job:task-1", 60, json.dumps({"state": "queued", "progress": 0.0}))
        record_worker_event("task-1", {"type": "progress", "progress": 42.5, "phase": "encoding"})
        self.assertEqual(task_snapshot("task-1")["state"], "PROGRESS")
        job = json.loads(redis.get("job:task-1"))
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["progress"], 42.5)

    def test_done_event_exposes_output_metadata_to_local_downloads(self):
        record_worker_event(
            "task-2",
            {
                "type": "done",
                "stats": {"output_path": "/tmp/output.m4a", "final_size_mb": 0.02},
            },
        )
        snapshot = task_snapshot("task-2")
        self.assertEqual(snapshot["state"], "SUCCESS")
        self.assertEqual(snapshot["info"]["output_path"], "/tmp/output.m4a")
        self.assertEqual(snapshot["info"]["progress"], 100.0)

    def test_cancel_request_does_not_clobber_terminal_task_state(self):
        update_task("task-3", state="SUCCESS", info={"detail": "done"})
        cancel_task("task-3")
        self.assertEqual(task_snapshot("task-3")["state"], "SUCCESS")
        self.assertEqual(get_sync_redis().get("cancel:task-3"), "1")


if __name__ == "__main__":
    unittest.main()
