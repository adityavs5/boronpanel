"""Opaque browser session identifiers are never persisted in reusable form."""
from __future__ import annotations

import hashlib
import re

_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


def session_digest(raw: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError("invalid session identifier")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_session_digest(value: str) -> bool:
    return bool(_SHA256_HEX.fullmatch(value or ""))
