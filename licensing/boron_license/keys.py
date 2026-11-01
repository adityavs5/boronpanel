"""License keys and admin tokens: generation, normalisation and hashing.

Keys carry 80 random bits, so a plain SHA-256 is a safe storage form: the
database never holds a usable key, and no extra secret is needed to verify one.
"""
from __future__ import annotations

import hashlib
import re
import secrets

# No look-alikes (0/O, 1/I): 32 symbols, 5 bits each.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
assert len(set(ALPHABET)) == 32
KEY_PATTERN = re.compile(rf"^BRN(-[{re.escape(ALPHABET)}]{{4}}){{4}}$")
ADMIN_TOKEN_PREFIX = "blt_"


def generate_key() -> str:
    symbols = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "BRN-" + "-".join(symbols[i:i + 4] for i in range(0, 16, 4))


def normalize_key(raw: str) -> str | None:
    """Return the canonical key, or None when ``raw`` cannot be a Boron key."""
    key = re.sub(r"\s+", "", raw or "").upper()
    return key if KEY_PATTERN.fullmatch(key) else None


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def key_hint(key: str) -> str:
    """Last group of a key; safe to show in lists and support conversations."""
    return key[-4:]


def generate_admin_token() -> str:
    return ADMIN_TOKEN_PREFIX + secrets.token_urlsafe(32)
