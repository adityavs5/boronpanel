"""Publisher authentication for privileged Boron release archives.

The Ed25519 public key is pinned in the installed source tree. The private key
is held separately by the release operator and never ships in a release.
The signature binds both the archive digest and the semantic version.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY_FILE = Path(__file__).resolve().parent.parent / "deploy" / "release-ed25519-public.hex"
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def archive_digest(path: str | Path) -> bytes:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def signed_message(version: str, digest: bytes) -> bytes:
    if not _VERSION_RE.fullmatch(version) or len(digest) != 32:
        raise ValueError("invalid release version or digest")
    return b"boron-release-v1\0" + version.encode("ascii") + b"\0" + digest


def verify_signature(version: str, digest: bytes, signature: bytes,
                     public_key: bytes | None = None) -> None:
    """Raise ValueError for missing/wrong key, version or signature."""
    if len(signature) != 64:
        raise ValueError("release signature must be 64 bytes")
    if public_key is None:
        try:
            public_key = bytes.fromhex(PUBLIC_KEY_FILE.read_text().strip())
        except (OSError, ValueError) as exc:
            raise ValueError("trusted release public key is unavailable") from exc
    if len(public_key) != 32:
        raise ValueError("trusted release public key is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, signed_message(version, digest))
    except InvalidSignature as exc:
        raise ValueError("release publisher signature is invalid") from exc
