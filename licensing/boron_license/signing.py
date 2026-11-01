"""Ed25519-signed license documents.

A document is ``{"payload": {...}, "signature": base64, "key_id": hex}``. The
signature covers the canonical JSON of the payload (sorted keys, no spaces,
ASCII), so any re-encoding by a client verifies identically.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def public_pem(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"{path} is not an Ed25519 private key")
    return key


def write_private_key(path: Path, key: Ed25519PrivateKey) -> None:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    # Create with 0600 from the start; never widen an existing file.
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        handle.write(pem)


class Signer:
    def __init__(self, private_key: Ed25519PrivateKey):
        self._key = private_key
        self.public_key = private_key.public_key()
        self.key_id = key_id(self.public_key)

    def sign(self, payload: dict) -> dict:
        signature = self._key.sign(canonical(payload))
        return {"payload": payload, "signature": base64.b64encode(signature).decode("ascii"),
                "key_id": self.key_id}


def verify_document(public_key: Ed25519PublicKey, document: dict) -> dict:
    """Return the payload of a correctly signed document, or raise ValueError."""
    try:
        payload = document["payload"]
        signature = base64.b64decode(document["signature"], validate=True)
        if not isinstance(payload, dict):
            raise TypeError
        public_key.verify(signature, canonical(payload))
    except (KeyError, TypeError, binascii.Error, InvalidSignature) as exc:
        raise ValueError("license document signature is invalid") from exc
    return payload
