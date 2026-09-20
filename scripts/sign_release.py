"""Sign an already built Boron release archive using an operator-held key.

Usage: python -m scripts.sign_release VERSION ARCHIVE PRIVATE_KEY_PEM
The private key's *path* is provided on argv, never its bytes. The release
pipeline requires this step and verifies the result against its pinned public
key before publishing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shared.release_signature import PUBLIC_KEY_FILE, archive_digest, signed_message, verify_signature


def sign(version: str, archive: Path, key_file: Path) -> Path:
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("release signing key must be Ed25519")
    pinned = bytes.fromhex(PUBLIC_KEY_FILE.read_text().strip())
    digest = archive_digest(archive)
    signature = key.sign(signed_message(version, digest))
    verify_signature(version, digest, signature, pinned)
    path = Path(str(archive) + ".sig")
    path.write_bytes(signature)
    os.chmod(path, 0o644)
    return path


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: python -m scripts.sign_release VERSION ARCHIVE PRIVATE_KEY_PEM")
    print(sign(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])))
