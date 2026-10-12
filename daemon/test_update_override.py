"""Root-only gate for disposable-lab update testing.

This module deliberately does not read ``boron.toml``, the panel database, or
environment variables.  Enabling the lab source requires two root-owned files
created outside the panel.  Production release selection remains in
``daemon.updates`` and continues to use the compiled-in publisher key and the
GitHub release-host restrictions.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import urllib.parse

from shared.validation import ValidationError


MARKER_PATH = Path("/etc/boron/test-update-override.enabled")
SOURCE_PATH = Path("/etc/boron/test-update-source.json")
ROOT_UID = 0
_PUBLIC_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class TestUpdateOverride:
    base_url: str
    public_key: bytes


def _read_root_file(path: Path, *, maximum: int) -> bytes:
    """Read a small, root-owned, non-symlink file without a pathname race."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        raise ValidationError(f"test update override file is missing: {path}") from None
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ValidationError(f"test update override file must be a regular file: {path}")
    if before.st_uid != ROOT_UID or before.st_mode & 0o077:
        raise ValidationError(
            f"test update override file must be owned by root and mode 0600 or stricter: {path}"
        )
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValidationError(f"cannot open test update override file safely: {path}") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValidationError(f"test update override file changed while opening: {path}")
        data = os.read(fd, maximum + 1)
        if len(data) > maximum:
            raise ValidationError(f"test update override file is too large: {path}")
        return data
    finally:
        os.close(fd)


def _validate_base_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        raise ValidationError("test update base_url must be a short URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("test update base_url must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError("test update base_url cannot contain credentials, a query, or a fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise ValidationError(
            "test update base_url must use a literal loopback IP address"
        ) from exc
    if not address.is_loopback:
        raise ValidationError("test update base_url must use a literal loopback IP address")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("test update base_url has an invalid port") from exc
    if port is None:
        raise ValidationError("test update base_url must specify an explicit loopback port")
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        raise ValidationError("test update base_url must include a dedicated path")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def load_test_update_override() -> TestUpdateOverride | None:
    """Return the active lab source, or ``None`` when the root marker is absent.

    A present marker with a missing or malformed descriptor is an error rather
    than a silent fallback to production.  This makes partial lab setup visible
    and prevents an operator from believing a throwaway key is in use when it
    is not.
    """
    try:
        MARKER_PATH.lstat()
    except FileNotFoundError:
        return None
    marker = _read_root_file(MARKER_PATH, maximum=64)
    if marker.strip():
        raise ValidationError("test update override marker must be empty")
    raw = _read_root_file(SOURCE_PATH, maximum=4096)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("test update source descriptor is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or set(data) != {"format", "base_url", "public_key_hex"}:
        raise ValidationError(
            "test update source descriptor must contain only format, base_url, and public_key_hex"
        )
    if data.get("format") != 1:
        raise ValidationError("test update source descriptor format must be 1")
    key_text = data.get("public_key_hex")
    if not isinstance(key_text, str) or _PUBLIC_KEY_RE.fullmatch(key_text) is None:
        raise ValidationError("test update public key must be exactly 32 bytes in hexadecimal")
    return TestUpdateOverride(
        base_url=_validate_base_url(data.get("base_url")),
        public_key=bytes.fromhex(key_text),
    )


def validate_test_asset_url(url: str, override: TestUpdateOverride) -> None:
    """Require an asset to stay on and below the configured loopback base."""
    candidate = urllib.parse.urlsplit(url)
    base = urllib.parse.urlsplit(override.base_url)
    if candidate.scheme != base.scheme or candidate.netloc != base.netloc:
        raise ValidationError("test update URL left the configured loopback origin")
    if candidate.username or candidate.password or candidate.query or candidate.fragment:
        raise ValidationError("test update URL contains disallowed URL components")
    prefix = base.path.rstrip("/") + "/"
    if not candidate.path.startswith(prefix):
        raise ValidationError("test update URL left the configured loopback path")
    # Recheck the host on every request, independent of descriptor parsing.
    try:
        address = ipaddress.ip_address(candidate.hostname or "")
    except ValueError as exc:
        raise ValidationError("test update URL must use a literal loopback IP") from exc
    if not address.is_loopback:
        raise ValidationError("test update URL must use a literal loopback IP")
