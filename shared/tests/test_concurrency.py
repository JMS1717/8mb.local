from __future__ import annotations

from unittest.mock import patch
import time

from shared import concurrency


def _gpu(name: str, total: int, free: int) -> str:
    return f"{name}, {total}, {free}\n"


def test_large_gpu_gets_more_parallelism_than_small_gpu():
    with patch.object(concurrency.psutil, "cpu_count", side_effect=lambda logical=True: 16 if logical else 8), \
         patch.object(concurrency.psutil, "virtual_memory", return_value=type("M", (), {"available": 24 * 1024**3})()), \
         patch.object(concurrency.subprocess, "run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = _gpu("RTX 4070 Ti SUPER", 16384, 14000)
        large = concurrency.auto_worker_concurrency()
        run.return_value.stdout = _gpu("GTX 1060", 6144, 5000)
        small = concurrency.auto_worker_concurrency()
    assert large > small


def test_cpu_only_mode_is_conservative():
    with patch.object(concurrency.psutil, "cpu_count", side_effect=lambda logical=True: 8 if logical else 4), \
         patch.object(concurrency.psutil, "virtual_memory", return_value=type("M", (), {"available": 8 * 1024**3})()), \
         patch.object(concurrency, "_nvidia_inventory", return_value=[]):
        assert concurrency.auto_worker_concurrency() == 1


def test_numeric_override_wins_over_auto():
    assert concurrency.resolve_worker_concurrency("6") == 6


def test_free_vram_reduces_live_automatic_limit():
    with patch.object(concurrency.psutil, "cpu_count", return_value=16), \
         patch.object(concurrency.psutil, "virtual_memory", return_value=type("M", (), {"available": 24 * 1024**3})()), \
         patch.object(concurrency.subprocess, "run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = _gpu("RTX 4070 Ti SUPER", 16384, 14000)
        plenty = concurrency.auto_worker_concurrency()
        run.return_value.stdout = _gpu("RTX 4070 Ti SUPER", 16384, 4096)
        constrained = concurrency.auto_worker_concurrency()
    assert plenty == concurrency.MAX_AUTO_CONCURRENCY
    assert constrained == 4


def test_gate_releases_a_local_slot():
    gate = concurrency.AdaptiveConcurrencyGate("1", refresh_seconds=60)
    token = gate.acquire()
    assert gate.current_limit() == 1
    gate.release(token)
    token2 = gate.acquire()
    gate.release(token2)


def test_gate_refreshes_its_limit_for_down_and_up_scaling():
    with patch.object(concurrency, "resolve_worker_concurrency", side_effect=[12, 4, 12]):
        gate = concurrency.AdaptiveConcurrencyGate("auto", refresh_seconds=0.25)
        assert gate.current_limit() == 12
        time.sleep(0.3)
        assert gate.current_limit() == 4
        time.sleep(0.3)
        assert gate.current_limit() == 12


def test_redis_gate_renews_long_running_lease_and_stops_on_release():
    class FakeRedis:
        def __init__(self):
            self.renewals = []
            self.released = []

        def eval(self, *_args):
            return 1

        def zadd(self, key, mapping):
            self.renewals.append((key, mapping))

        def expire(self, *_args):
            return True

        def zrem(self, key, token):
            self.released.append((key, token))

    redis = FakeRedis()
    gate = concurrency.AdaptiveConcurrencyGate(
        "1", redis_client=redis, lease_refresh_seconds=0.01,
    )
    token = gate.acquire()
    time.sleep(0.04)
    gate.release(token)
    renewal_count = len(redis.renewals)
    time.sleep(0.03)
    assert renewal_count >= 1
    assert len(redis.renewals) == renewal_count
    assert redis.released == [(concurrency.ADAPTIVE_REDIS_KEY, token)]
