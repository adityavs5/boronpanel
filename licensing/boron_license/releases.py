"""Release indexes and short-lived download links for license-gated updates.

The release tooling uploads, per version, the archive and its checksum,
signature and signed metadata plus a ``release.json`` index into the protected
tree, and copies the index to ``channels/<channel>.json`` last. This module only
reads those indexes; panels verify the files themselves with the pinned release
key, so the index is trusted for routing, never for integrity.

Links are nginx ``secure_link`` URLs: nginx recomputes
``md5("$secure_link_expires$uri$remote_addr <secret>")`` and compares it with
the ``md5`` argument, so no application code sits in the download path.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

INDEX_FORMAT = 1
CHANNELS = ("stable", "beta")
# Exact X.Y.Z only; also keeps every path built from a version inside the tree.
VERSION = re.compile(r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$")
FILE_ROLES = ("archive", "checksum", "signature", "metadata", "metadata_signature")
URL_PREFIX = "/protected/releases"
MAX_INDEX_BYTES = 1 << 20
MAX_CHANGELOG = 200_000
MAX_ARCHIVE_BYTES = 1 << 32


class ReleaseNotFound(Exception):
    """No usable index; the reason is for logs and the audit trail only."""


def secure_link_token(*, expires: int, uri: str, client_ip: str, secret: str) -> str:
    """The ``md5`` argument nginx expects for this path, address and expiry."""
    digest = hashlib.md5(f"{expires}{uri}{client_ip} {secret}".encode(), usedforsecurity=False).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def signed_url(base_url: str, uri: str, *, client_ip: str, secret: str, expires: int) -> str:
    token = secure_link_token(expires=expires, uri=uri, client_ip=client_ip, secret=secret)
    return f"{base_url.rstrip('/')}{uri}?md5={token}&expires={expires}"


def allowed_channels(channel: str) -> tuple[str, ...]:
    """Releases a client on ``channel`` may receive: beta also gets stable."""
    return ("stable", "beta") if channel == "beta" else ("stable",)


def channel_index(releases_dir: Path, channel: str) -> dict:
    if channel not in CHANNELS:
        raise ReleaseNotFound(f"unknown channel {channel!r}")
    index = _validated(_read_json(releases_dir / "channels" / f"{channel}.json"))
    if index["channel"] != channel:
        raise ReleaseNotFound(f"channels/{channel}.json describes channel {index['channel']!r}")
    return index


def version_index(releases_dir: Path, version: str) -> dict:
    if not VERSION.fullmatch(version):
        raise ReleaseNotFound("invalid version")
    index = _validated(_read_json(releases_dir / "releases" / version / "release.json"))
    if index["version"] != version:
        raise ReleaseNotFound(f"releases/{version}/release.json describes {index['version']!r}")
    return index


def file_uri(version: str, name: str) -> str:
    return f"{URL_PREFIX}/{version}/{name}"


def _read_json(path: Path) -> object:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_INDEX_BYTES + 1)
    except OSError as exc:
        raise ReleaseNotFound(f"{path.name}: {exc.strerror or exc}") from exc
    if len(raw) > MAX_INDEX_BYTES:
        raise ReleaseNotFound(f"{path.name}: too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReleaseNotFound(f"{path.name}: not valid JSON") from exc


def _validated(data: object) -> dict:
    """Return a clean copy of an index, or refuse it as a whole."""
    if not isinstance(data, dict):
        raise ReleaseNotFound("index is not an object")
    if data.get("format") != INDEX_FORMAT or isinstance(data.get("format"), bool):
        raise ReleaseNotFound("unsupported index format")
    channel, version = data.get("channel"), data.get("version")
    if channel not in CHANNELS:
        raise ReleaseNotFound("invalid channel")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ReleaseNotFound("invalid version")
    published_at, changelog = data.get("published_at"), data.get("changelog", "")
    if not isinstance(published_at, str) or not 0 < len(published_at) <= 40:
        raise ReleaseNotFound("invalid published_at")
    if not isinstance(changelog, str) or len(changelog) > MAX_CHANGELOG:
        raise ReleaseNotFound("invalid changelog")

    files = data.get("files")
    if not isinstance(files, dict) or set(files) != set(FILE_ROLES):
        raise ReleaseNotFound("files must list exactly " + ", ".join(FILE_ROLES))
    # Plain names of this version's files: no separators, no "..", nothing
    # that could name another version or leave releases/<ver>/.
    name_pattern = re.compile(rf"^boron-{re.escape(version)}(\.[A-Za-z0-9]+)+$")
    names = {}
    for role in FILE_ROLES:
        name = files[role]
        if not isinstance(name, str) or len(name) > 100 or not name_pattern.fullmatch(name):
            raise ReleaseNotFound(f"invalid file name for {role}")
        names[role] = name
    if len(set(names.values())) != len(names):
        raise ReleaseNotFound("file names must be distinct")

    sizes = data.get("sizes")
    size = sizes.get("archive") if isinstance(sizes, dict) else None
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ARCHIVE_BYTES:
        raise ReleaseNotFound("invalid archive size")
    return {"format": INDEX_FORMAT, "channel": channel, "version": version, "published_at": published_at,
            "changelog": changelog, "files": names, "archive_size": size}
