"""Keep retained Windows ARM64 build inputs pinned and native."""
import hashlib
import importlib.util
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "arm64_recovery", ROOT / "windows/recover-arm64-ffmpeg.py")
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


def pe(machine):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    return bytes(data)


def test_native_arm64_pe_required():
    assert RECOVERY.is_arm64_pe(pe(0xAA64))
    assert not RECOVERY.is_arm64_pe(pe(0x8664))
    assert not RECOVERY.is_arm64_pe(b"not an executable")
    data = bytearray(pe(0xAA64))
    struct.pack_into("<I", data, 0x3C, 4096)
    assert not RECOVERY.is_arm64_pe(bytes(data))


def test_wrong_source_refused_before_any_output(tmp_path):
    source = tmp_path / "untrusted.exe"
    source.write_bytes(pe(0xAA64))
    output = tmp_path / "bin"
    with pytest.raises(ValueError, match="pinned CI artifact"):
        RECOVERY.recover(source, output)
    assert not output.exists()


def test_recovery_and_build_are_pinned_to_reviewed_inputs():
    workflow = (ROOT / ".github/workflows/windows-exe.yml").read_text()
    build = (ROOT / "windows/build.ps1").read_text()
    assert "run-id: 32651740165" in workflow
    assert "recover-arm64-ffmpeg.py" in workflow
    assert "8mblocal-ffmpeg-8.1.2-winarm64.zip" in build
    assert "3287f8a6f70abb7037a5acad8c2efb208382b6bc2e50bd913a17a9ff6dc3ce26" in build
    assert "7b801cdd3a1a0bb54ae6f572187e68b4ed54f52086cfee133fab2180bfb429fa" in build
