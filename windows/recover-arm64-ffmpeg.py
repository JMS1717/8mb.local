"""Retain the exact ARM64 FFmpeg binaries from our passing August build.

This is a prepublication recovery path, not an unpinned update. Once v143 is
approved, publish the deterministic archive so clean builds use its mirror.
"""
import argparse
import hashlib
import struct
import zipfile
from pathlib import Path

SOURCE_SHA256 = "150a251effca1e6167352e047cb9f40058ec722b50e04719d86c9af8c41c1a8d"
BINARIES = {
    "ffmpeg.exe": "5466a069b6fb812d3e5402a8ad2764c3ba94b0fefeec9319c66158dc5c98e023",
    "ffprobe.exe": "b3063dbcb68bae702df36498ae5f9ba5d6032c42810d8776ecf08c8ac776d6cc",
}
PROVENANCE = (
    "FFmpeg 8.1.2 native Windows ARM64; unchanged binaries recovered from\n"
    "https://github.com/JMS1717/8mb.local/actions/runs/32651740165\n"
    "artifact 8mblocal-windows-arm64, source commit 599a19c.\n"
    "Original upstream: BtbN/FFmpeg-Builds autobuild-2026-08-22-12-58,\n"
    "ffmpeg-n8.1.2-44-g7c533d0f86-winarm64-gpl-8.1.zip.\n"
    "License: GPLv3. FFmpeg source: https://github.com/FFmpeg/FFmpeg/tree/7c533d0f86\n"
    "Build scripts and dependency sources: https://github.com/BtbN/FFmpeg-Builds\n"
).encode()


def is_arm64_pe(data):
    if len(data) < 64 or data[:2] != b"MZ":
        return False
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    return (offset + 6 <= len(data) and data[offset:offset + 4] == b"PE\0\0"
            and struct.unpack_from("<H", data, offset + 4)[0] == 0xAA64)


def recover(source, output_bin, output_archive=None):
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Source EXE does not match the passing, pinned CI artifact")
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(source))
    files = {}
    for name, digest in BINARIES.items():
        data = archive.extract("bin\\" + name)
        if hashlib.sha256(data).hexdigest() != digest or not is_arm64_pe(data):
            raise ValueError(f"Invalid native ARM64 binary: {name}")
        files["bin/" + name] = data
    files["BUILD-PROVENANCE.txt"] = PROVENANCE
    files["COPYING.GPLv3"] = (Path(__file__).with_name("ffmpeg-COPYING.GPLv3")
                              .read_text(encoding="utf-8").encode())
    # Verify all inputs before writing any outputs. Never overwrite another
    # cached binary silently; it must already match this exact retained input.
    for name, digest in BINARIES.items():
        target = output_bin / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Refusing to overwrite a different binary: {target}")
    output_bin.mkdir(parents=True, exist_ok=True)
    for name in BINARIES:
        (output_bin / name).write_bytes(files["bin/" + name])
    if output_archive:
        output_archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_archive, "x", compression=zipfile.ZIP_STORED) as bundle:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 0
                info.external_attr = 0x20
                bundle.writestr(info, data)
        print("ARCHIVE_SHA256=" + hashlib.sha256(output_archive.read_bytes()).hexdigest())
    print("RECOVERED_VERIFIED_NATIVE_ARM64_FFMPEG=8.1.2")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-exe", type=Path, required=True)
    parser.add_argument("--output-bin", type=Path, required=True)
    parser.add_argument("--output-archive", type=Path)
    args = parser.parse_args()
    recover(args.source_exe, args.output_bin, args.output_archive)
