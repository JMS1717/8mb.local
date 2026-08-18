"""Pure FFmpeg command helpers shared by the worker and unit tests."""
from __future__ import annotations


def cpu_filter_chain(filters: list[str] | None) -> list[str]:
    """Convert a hardware-frame filter chain to a CPU-safe chain.

    Filters can be stored as individual entries or as comma-joined entries;
    split both forms so ``hwdownload`` cannot survive a CPU fallback by being
    hidden inside a single string.
    """
    if not filters:
        return []
    converted: list[str] = []
    for filter_entry in filters:
        for item in str(filter_entry).split(","):
            item = item.strip()
            if not item:
                continue
            if item.startswith("scale_npp=") or item.startswith("scale_cuda="):
                item = "scale=" + item.split("=", 1)[1]
            if item.startswith("vpp_qsv="):
                item = "scale=" + item.split("=", 1)[1]
            if item == "hwdownload" or item.startswith("hwupload"):
                continue
            if item.startswith("format=") and (
                "vaapi" in item or item in {"format=nv12", "format=p010", "format=p010le"}
            ):
                continue
            converted.append(item)
    return [",".join(converted)] if converted else []


def replace_bitrate_args(command: list[str], video_kbps: int) -> list[str]:
    """Return a command with all video bitrate/VBV values adjusted."""
    maxrate = int(video_kbps * 1.2)
    bufsize = int(video_kbps * 2.0)
    replacements = {
        "-b:v": f"{video_kbps}k",
        "-maxrate": f"{maxrate}k",
        "-bufsize": f"{bufsize}k",
    }
    updated: list[str] = []
    i = 0
    while i < len(command):
        token = command[i]
        if token in replacements and i + 1 < len(command):
            updated.extend([token, replacements[token]])
            i += 2
            continue
        updated.append(token)
        i += 1
    return updated
