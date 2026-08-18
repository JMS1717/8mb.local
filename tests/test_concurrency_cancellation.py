import threading
import time
import unittest

from shared.concurrency import AdaptiveConcurrencyGate, JobCancellationRequested


class AdaptiveConcurrencyCancellationTests(unittest.TestCase):
    def test_waiting_job_cancels_without_acquiring_slot(self):
        gate = AdaptiveConcurrencyGate("1", refresh_seconds=60)
        first = gate.acquire()
        cancelled = threading.Event()
        result: list[BaseException] = []

        def wait_for_slot() -> None:
            try:
                gate.acquire(cancelled=cancelled.is_set)
            except BaseException as exc:  # assert the exact cooperative signal below
                result.append(exc)

        thread = threading.Thread(target=wait_for_slot)
        thread.start()
        time.sleep(0.15)
        cancelled.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), "canceled gate wait must not hang")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], JobCancellationRequested)
        self.assertEqual(gate._active, 1)
        gate.release(first)

    def test_cancel_after_slot_check_does_not_leak_local_slot(self):
        gate = AdaptiveConcurrencyGate("1", refresh_seconds=60)
        canceled = True
        with self.assertRaises(JobCancellationRequested):
            gate.acquire(cancelled=lambda: canceled)
        self.assertEqual(gate._active, 0)


if __name__ == "__main__":
    unittest.main()
