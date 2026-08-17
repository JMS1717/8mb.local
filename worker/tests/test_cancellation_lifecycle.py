import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from celery.exceptions import Ignore

from shared.concurrency import JobCancellationRequested
from worker.app.tasks import _cleanup_transient_input_after_task


class _Task:
    class Request:
        id = "cancel-test"

    request = Request()

    def update_state(self, **kwargs):
        self.state_update = kwargs


class _CancelGate:
    def current_limit(self):
        return 1

    def acquire(self, cancelled=None):
        if cancelled and cancelled():
            raise JobCancellationRequested("Job canceled while waiting for an encode slot")
        raise AssertionError("test gate should have observed cancellation")

    def release(self, _lease):
        raise AssertionError("a canceled wait must not release an unowned lease")


class CancellationLifecycleTests(unittest.TestCase):
    def test_gate_wait_cancel_is_terminal_and_still_cleans_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")

            @_cleanup_transient_input_after_task
            def task(_self, _job_id, _input_path, transient_input=False):
                raise AssertionError("canceled task must not enter the body")

            task_obj = _Task()
            with (
                patch("worker.app.tasks._encode_gate", return_value=_CancelGate()),
                patch("worker.app.tasks._is_cancelled", return_value=True),
                patch("worker.app.tasks._publish") as publish,
            ):
                with self.assertRaises(Ignore):
                    task(task_obj, "job", str(source), transient_input=True)

            self.assertFalse(source.exists())
            self.assertEqual(task_obj.state_update["state"], "CANCELED")
            self.assertEqual(task_obj.state_update["meta"]["state"], "canceled")
            self.assertEqual([call.args[1]["type"] for call in publish.call_args_list], ["canceled"])


if __name__ == "__main__":
    unittest.main()
