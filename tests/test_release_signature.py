from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shared import release_signature


def test_release_signature_binds_version_digest_and_publisher():
    publisher = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    public = publisher.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    other_public = other.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    digest = hashlib.sha256(b"release fixture").digest()
    signature = publisher.sign(release_signature.signed_message("1.5.1", digest))

    release_signature.verify_signature("1.5.1", digest, signature, public)
    for version, data, key in [
        ("1.5.0", digest, public),
        ("1.5.1", hashlib.sha256(b"substituted").digest(), public),
        ("1.5.1", digest, other_public),
    ]:
        with pytest.raises(ValueError, match="signature is invalid"):
            release_signature.verify_signature(version, data, signature, key)


def test_release_signature_fails_without_pinned_key(monkeypatch, tmp_path):
    monkeypatch.setattr(release_signature, "PUBLIC_KEY_FILE", tmp_path / "absent")
    with pytest.raises(ValueError, match="unavailable"):
        release_signature.verify_signature("1.5.1", b"x" * 32, b"x" * 64)
