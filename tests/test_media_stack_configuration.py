"""Static checks for the cross-vendor Linux media runtime contract."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_uses_matching_intel_vpl_runtime_and_preserves_amd_driver_path():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG INTEL_MEDIA_DRIVER_VERSION=intel-media-24.1.5" in dockerfile
    assert "ARG LIBVPL_VERSION=v2023.4.0" in dockerfile
    assert "libmfx-gen1.2" in dockerfile
    assert (
        "ENV LIBVA_DRIVERS_PATH=/usr/local/lib/dri:/usr/lib/x86_64-linux-gnu/dri:/usr/lib/dri"
        in dockerfile
    )


def test_vaapi_compose_keeps_host_device_and_dynamic_groups():
    compose = (ROOT / "docker-compose.vaapi.yml").read_text(encoding="utf-8")
    assert "- /dev/dri:/dev/dri" in compose
    assert '"${VIDEO_GID:-44}"' in compose
    assert '"${RENDER_GID:-109}"' in compose
    assert "105  # render group" not in compose
