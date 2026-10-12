"""Validated, publisher-signed update behavior declared by each release."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


FORMAT_VERSION = 1
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:-]{1,128}$")
_CHANGE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class ReleaseMetadataError(ValueError):
    """A release behavior document is malformed or incompatible."""


def version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(str(value or ""))
    if match is None:
        raise ReleaseMetadataError(f"invalid semantic version {value!r}")
    return tuple(int(part) for part in match.groups())


def _string_list(data: dict, key: str, pattern: re.Pattern, *, maximum: int = 32) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or len(value) > maximum:
        raise ReleaseMetadataError(f"{key} must be a list with at most {maximum} entries")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or pattern.fullmatch(item) is None:
            raise ReleaseMetadataError(f"{key} contains an invalid value")
        if item not in result:
            result.append(item)
    return result


def validate_release_metadata(data: object) -> dict:
    if not isinstance(data, dict):
        raise ReleaseMetadataError("release metadata must be a JSON object")
    if data.get("format") != FORMAT_VERSION:
        raise ReleaseMetadataError(f"release metadata format must be {FORMAT_VERSION}")
    minimum = str(data.get("minimum_source_version") or "")
    version_tuple(minimum)
    maximum_raw = data.get("maximum_source_version")
    maximum = None if maximum_raw in (None, "") else str(maximum_raw)
    if maximum is not None and version_tuple(maximum) < version_tuple(minimum):
        raise ReleaseMetadataError("maximum_source_version is below minimum_source_version")
    data_plane = _string_list(data, "data_plane_reloads", _UNIT_RE)
    control_plane = _string_list(data, "control_plane_restarts", _UNIT_RE)
    rewrites = _string_list(data, "runtime_rewrites", _CHANGE_RE, maximum=64)
    return {
        "format": FORMAT_VERSION,
        "minimum_source_version": minimum,
        "maximum_source_version": maximum,
        "data_plane_reloads": data_plane,
        "control_plane_restarts": control_plane,
        "runtime_rewrites": rewrites,
        "requires_data_plane_acknowledgement": bool(data_plane),
    }


def parse_release_metadata(content: bytes | str) -> dict:
    try:
        raw = content.decode("utf-8") if isinstance(content, bytes) else content
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise ReleaseMetadataError("release metadata exceeds 64 KiB")
        data = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseMetadataError("release metadata is not valid UTF-8 JSON") from exc
    return validate_release_metadata(data)


def load_release_metadata(path: str | Path) -> dict:
    try:
        content = Path(path).read_bytes()
    except OSError as exc:
        raise ReleaseMetadataError(f"release metadata is missing: {path}") from exc
    return parse_release_metadata(content)


def metadata_digest(content: bytes) -> bytes:
    return hashlib.sha256(content).digest()


def source_version_supported(metadata: dict, source_version: str) -> tuple[bool, str]:
    source = version_tuple(source_version)
    minimum = version_tuple(metadata["minimum_source_version"])
    maximum_text = metadata.get("maximum_source_version")
    if source < minimum:
        return False, f"release requires Boron {metadata['minimum_source_version']} or newer"
    if maximum_text and source > version_tuple(maximum_text):
        return False, f"release supports source versions through Boron {maximum_text}"
    return True, "source version is supported"
