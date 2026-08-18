"""Safe normalization for technical metadata read from media files.

ffprobe metadata is descriptive input from an untrusted user file.  Do not
pass raw enum/string metadata directly to FFmpeg output options.  Normalize
against known-safe values and omit unknown, reserved, or placeholder values.
"""
from __future__ import annotations

from typing import Any, Mapping


# These are the canonical names accepted by the FFmpeg 6.x build shipped by
# 8mb.local.  Keep this an allowlist: a new ffprobe token must be reviewed
# before it can become an encoder option.
_COLOR_VALUES: dict[str, frozenset[str]] = {
    "video_color_range": frozenset({"tv", "pc"}),
    "video_color_space": frozenset({
        "bt709", "fcc", "bt470bg", "smpte170m", "smpte240m",
        "ycgco", "bt2020nc", "bt2020c", "smpte2085",
        "chroma-derived-nc", "chroma-derived-c", "ictcp",
    }),
    "video_color_primaries": frozenset({
        "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film",
        "bt2020", "smpte428", "smpte431", "smpte432", "jedec-p22", "ebu3213",
    }),
    "video_color_transfer": frozenset({
        "bt709", "gamma22", "gamma28", "smpte170m", "smpte240m", "linear",
        "log", "log_sqrt", "iec61966-2-4", "bt1361e", "iec61966-2-1",
        "bt2020-10", "bt2020-12", "smpte2084", "smpte428", "arib-std-b67",
    }),
}

_COLOR_ALIASES: dict[str, dict[str, str]] = {
    "video_color_range": {
        "limited": "tv",
        "mpeg": "tv",
        "full": "pc",
        "jpeg": "pc",
    },
    "video_color_space": {
        "bt2020-ncl": "bt2020nc",
        "bt2020-nclc": "bt2020nc",
        "bt2020-cl": "bt2020c",
        "smpte-170m": "smpte170m",
    },
    "video_color_primaries": {
        "bt.709": "bt709",
        "bt.2020": "bt2020",
        "smpte-170m": "smpte170m",
    },
    "video_color_transfer": {
        "bt.709": "bt709",
        "bt.2020-10": "bt2020-10",
        "bt.2020-12": "bt2020-12",
        "smpte-st-2084": "smpte2084",
        "arib_std_b67": "arib-std-b67",
    },
}

_COLOR_OPTIONS: tuple[tuple[str, str], ...] = (
    ("-color_range", "video_color_range"),
    ("-colorspace", "video_color_space"),
    ("-color_primaries", "video_color_primaries"),
    ("-color_trc", "video_color_transfer"),
)

_PLACEHOLDERS = frozenset({
    "", "unknown", "unknown/unknown", "reserved", "unspecified", "undefined",
    "n/a", "none", "null",
})


def normalize_color_metadata_value(field: str, raw: Any) -> str | None:
    """Return one canonical FFmpeg value, or ``None`` to omit the field."""
    if field not in _COLOR_VALUES or raw is None:
        return None
    value = str(raw).strip().casefold()
    if value in _PLACEHOLDERS:
        return None
    value = _COLOR_ALIASES.get(field, {}).get(value, value)
    return value if value in _COLOR_VALUES[field] else None


def normalize_color_metadata(info: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalize each supported source color field independently.

    A bad transfer value must not discard valid primaries or matrix metadata.
    Unknown values fail closed to omission; they are never guessed.
    """
    if not info:
        return {}
    normalized: dict[str, str] = {}
    for _option, field in _COLOR_OPTIONS:
        value = normalize_color_metadata_value(field, info.get(field))
        if value is not None:
            normalized[field] = value
    return normalized


def source_color_metadata_args(info: Mapping[str, Any] | None) -> list[str]:
    """Build safe FFmpeg output options from normalized source metadata."""
    normalized = normalize_color_metadata(info)
    args: list[str] = []
    for option, field in _COLOR_OPTIONS:
        value = normalized.get(field)
        if value is not None:
            args.extend([option, value])
    return args


def supported_color_values(field: str) -> frozenset[str]:
    """Expose a read-only allowlist for focused command-construction tests."""
    return _COLOR_VALUES.get(field, frozenset())
