"""Measured, resource-aware concurrency for local and Docker workers.

Automatic mode is a bounded starting policy informed by real NVENC tests on
the development machine. It is not a promise that every GPU can sustain the
same count. The encode gate rechecks live VRAM/RAM before each job so active
work can shrink when another process consumes GPU memory and grow again after
that memory is released.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

import psutil

from shared.subprocess_utils import hidden_process_kwargs

# Benchmark evidence on the RTX 4070 Ti SUPER showed 12 jobs were faster than
# 16 for the supplied 1080p clips, while 16 added contention. The live gate
# still reduces this when current resources are lower.
MAX_AUTO_CONCURRENCY = 12
GPU_MEMORY_HEADROOM_MB = 2048
GPU_MEMORY_PER_JOB_MB = 512
ADAPTIVE_GATE_REFRESH_SECONDS = 2.0
ADAPTIVE_LEASE_TTL_SECONDS = 3600
ADAPTIVE_REDIS_KEY = "8mblocal:adaptive:encode"


class JobCancellationRequested(Exception):
    """Cooperative cancellation requested while a job is waiting or running."""


def _nvidia_inventory() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            **hidden_process_kwargs(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    inventory: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            inventory.append(
                {
                    "name": parts[0],
                    "memory_total_mb": max(0, int(float(parts[1]))),
                    "memory_free_mb": max(0, int(float(parts[2]))),
                }
            )
        except ValueError:
            continue
    return inventory


def _parse_configured(value: object) -> int | None:
    text = str(value or "auto").strip().lower()
    if text in {"", "auto", "automatic", "system"}:
        return None
    try:
        return max(1, min(20, int(text)))
    except (TypeError, ValueError):
        return None


def _gpu_total_limit(total_mb: int) -> int:
    total_gb = total_mb / 1024.0
    if total_gb >= 14:
        return MAX_AUTO_CONCURRENCY
    if total_gb >= 8:
        return min(MAX_AUTO_CONCURRENCY, 8)
    if total_gb >= 4:
        return min(MAX_AUTO_CONCURRENCY, 4)
    return 2


def _available_ram_limit(available_gb: float, *, gpu: bool) -> int:
    # Leave room for the OS, browser/UI, upload buffers, and FFmpeg itself.
    per_job_gb = 1.5 if gpu else 2.0
    return max(1, int(available_gb // per_job_gb))


def _select_gpu(gpus: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(gpus, key=lambda gpu: int(gpu.get("memory_total_mb", 0)), default=None)


def auto_worker_concurrency() -> int:
    """Return the current safe starting/admission count.

    For NVIDIA, total VRAM chooses the tested hardware tier and free VRAM
    limits the live count. The latter lets the adaptive gate scale down/up
    while the process remains running.
    """
    physical_cpus = psutil.cpu_count(logical=False) or psutil.cpu_count() or 1
    available_gb = max(0.25, float(psutil.virtual_memory().available) / (1024**3))
    gpus = _nvidia_inventory()
    if gpus:
        gpu = _select_gpu(gpus) or {}
        total_mb = int(gpu.get("memory_total_mb", 0))
        free_mb = int(gpu.get("memory_free_mb", 0))
        tier_limit = _gpu_total_limit(total_mb)
        # A zero free value is treated as unavailable telemetry, rather than
        # forcing every new job into a false one-worker mode.
        if free_mb > 0:
            live_vram_limit = max(
                1,
                (free_mb - GPU_MEMORY_HEADROOM_MB) // GPU_MEMORY_PER_JOB_MB,
            )
        else:
            live_vram_limit = tier_limit
        memory_limit = _available_ram_limit(available_gb, gpu=True)
        return max(
            1,
            min(MAX_AUTO_CONCURRENCY, tier_limit, live_vram_limit, memory_limit),
        )

    cpu_limit = max(1, physical_cpus // 4)
    memory_limit = _available_ram_limit(available_gb, gpu=False)
    return max(1, min(MAX_AUTO_CONCURRENCY, cpu_limit, memory_limit))


def resolve_worker_concurrency(configured: object = "auto") -> int:
    explicit = _parse_configured(configured)
    return explicit if explicit is not None else auto_worker_concurrency()


def worker_concurrency_details(configured: object = "auto") -> dict[str, Any]:
    raw = str(configured or "auto").strip().lower() or "auto"
    gpus = _nvidia_inventory()
    gpu = _select_gpu(gpus) or {}
    return {
        "mode": "auto" if _parse_configured(raw) is None else "manual",
        "configured": raw,
        "concurrency": resolve_worker_concurrency(raw),
        "max_auto_concurrency": MAX_AUTO_CONCURRENCY,
        "dynamic": _parse_configured(raw) is None,
        "gpu_detected": bool(gpus),
        "gpu_name": gpu.get("name"),
        "gpu_total_vram_mb": gpu.get("memory_total_mb"),
        "gpu_free_vram_mb": gpu.get("memory_free_mb"),
        "gpu_memory_headroom_mb": GPU_MEMORY_HEADROOM_MB,
        "gpu_memory_per_job_mb": GPU_MEMORY_PER_JOB_MB,
    }


def configured_worker_concurrency() -> str:
    return os.getenv("WORKER_CONCURRENCY", "auto").strip() or "auto"


class AdaptiveConcurrencyGate:
    """Gate encode starts using live resources.

    Without a Redis client this is process-local and is used by the Windows
    launcher. With a Redis client it uses a lease sorted set, allowing
    multiple Celery child processes to share the same live limit.
    """

    _ACQUIRE_LUA = """
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) < limit then
  redis.call('ZADD', KEYS[1], now + ttl, token)
  redis.call('EXPIRE', KEYS[1], math.ceil(ttl / 1000) + 5)
  return 1
end
return 0
"""

    def __init__(
        self,
        configured: object = "auto",
        *,
        redis_client: Any | None = None,
        key: str = ADAPTIVE_REDIS_KEY,
        refresh_seconds: float = ADAPTIVE_GATE_REFRESH_SECONDS,
        lease_refresh_seconds: float | None = None,
    ) -> None:
        self.configured = configured
        self.redis = redis_client
        self.key = key
        self.refresh_seconds = max(0.25, float(refresh_seconds))
        self.lease_refresh_seconds = max(
            0.01,
            float(lease_refresh_seconds)
            if lease_refresh_seconds is not None
            else min(60.0, ADAPTIVE_LEASE_TTL_SECONDS / 3),
        )
        self._condition = threading.Condition()
        self._active = 0
        self._cached_limit = 1
        self._cached_at = 0.0
        self._lease_lock = threading.Lock()
        self._lease_refreshers: dict[str, tuple[threading.Event, threading.Thread]] = {}

    def _start_lease_refresh(self, token: str) -> None:
        if self.redis is None:
            return
        stop = threading.Event()

        def refresh() -> None:
            while not stop.wait(self.lease_refresh_seconds):
                try:
                    now_ms = int(time.time() * 1000)
                    self.redis.zadd(
                        self.key,
                        {token: now_ms + ADAPTIVE_LEASE_TTL_SECONDS * 1000},
                    )
                    self.redis.expire(
                        self.key,
                        int(ADAPTIVE_LEASE_TTL_SECONDS) + 5,
                    )
                except Exception:
                    # The worker may be shutting down or Redis may be
                    # restarting. The lease will expire safely if renewal
                    # cannot resume; do not interrupt the encode thread.
                    continue

        thread = threading.Thread(
            target=refresh,
            name="8mblocal-adaptive-lease",
            daemon=True,
        )
        with self._lease_lock:
            self._lease_refreshers[token] = (stop, thread)
        thread.start()

    def _stop_lease_refresh(self, token: str) -> None:
        with self._lease_lock:
            refresh_state = self._lease_refreshers.pop(token, None)
        if refresh_state is None:
            return
        stop, thread = refresh_state
        stop.set()
        if thread is not threading.current_thread():
            thread.join(timeout=min(1.0, self.lease_refresh_seconds + 0.1))

    def current_limit(self) -> int:
        now = time.monotonic()
        if now - self._cached_at >= self.refresh_seconds:
            self._cached_limit = resolve_worker_concurrency(self.configured)
            self._cached_at = now
        return max(1, int(self._cached_limit))

    def acquire(self, cancelled: Callable[[], bool] | None = None) -> str:
        """Acquire a slot, checking ``cancelled`` while waiting.

        A task can spend a long time queued behind the adaptive limit.  The
        callback is deliberately checked before every broker/local wait so a
        cancellation request cannot strand a task before it enters the worker
        function that normally polls cancellation.
        """
        token = uuid.uuid4().hex
        def check_cancelled() -> None:
            if cancelled is not None and cancelled():
                raise JobCancellationRequested("Job canceled while waiting for an encode slot")

        if self.redis is not None:
            while True:
                check_cancelled()
                limit = self.current_limit()
                try:
                    accepted = self.redis.eval(
                        self._ACQUIRE_LUA,
                        1,
                        self.key,
                        int(time.time() * 1000),
                        ADAPTIVE_LEASE_TTL_SECONDS * 1000,
                        limit,
                        token,
                    )
                except Exception:
                    # Redis is also the task broker; retry a transient eval
                    # failure instead of turning a valid encode into a false
                    # application failure.
                    if cancelled is not None:
                        deadline = time.monotonic() + 1.0
                        while time.monotonic() < deadline:
                            check_cancelled()
                            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                    else:
                        time.sleep(1.0)
                    continue
                if int(accepted or 0) == 1:
                    try:
                        check_cancelled()
                    except JobCancellationRequested:
                        try:
                            self.redis.zrem(self.key, token)
                        except Exception:
                            pass
                        raise
                    self._start_lease_refresh(token)
                    return token
                if cancelled is not None:
                    deadline = time.monotonic() + 0.5
                    while time.monotonic() < deadline:
                        check_cancelled()
                        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                else:
                    time.sleep(0.5)

        while True:
            check_cancelled()
            with self._condition:
                if self._active < self.current_limit():
                    self._active += 1
                    try:
                        check_cancelled()
                    except JobCancellationRequested:
                        self._active = max(0, self._active - 1)
                        self._condition.notify_all()
                        raise
                    return token
                self._condition.wait(timeout=0.1 if cancelled is not None else 0.5)

    def release(self, token: str) -> None:
        if self.redis is not None:
            self._stop_lease_refresh(token)
            try:
                self.redis.zrem(self.key, token)
            except Exception:
                pass
            return
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()
