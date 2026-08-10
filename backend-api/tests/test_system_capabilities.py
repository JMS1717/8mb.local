from __future__ import annotations

import subprocess
from types import SimpleNamespace

from app import deps


def test_nvidia_inventory_allows_slow_laptop_gpu_startup(monkeypatch):
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["timeout"] = kwargs.get("timeout")
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "0, NVIDIA GeForce RTX 5070 Ti Laptop GPU, 12227, 143, "
                "610.88, GPU-test\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    monkeypatch.setattr(deps.os, "listdir", lambda _path: [])

    result = deps.get_system_capabilities()

    assert observed["timeout"] == 8
    assert result["nvidia_driver"] == "610.88"
    assert result["gpus"] == [
        {
            "index": 0,
            "name": "NVIDIA GeForce RTX 5070 Ti Laptop GPU",
            "memory_total_gb": 11.94,
            "memory_used_gb": 0.14,
            "uuid": "GPU-test",
        }
    ]


def test_nvidia_inventory_timeout_is_nonfatal(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("nvidia-smi", timeout=8)

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    monkeypatch.setattr(deps.os, "listdir", lambda _path: [])

    result = deps.get_system_capabilities()

    assert result["gpus"] == []
    assert result["nvidia_driver"] is None
