"""Panel license state, shared by the API and the daemon.

The license server (license.boronpanel.com) answers activations and periodic
checks with an Ed25519-signed document. The daemon stores the latest document
in ``settings.license_document_path``; this module verifies it and decides
whether panel administration is usable. Hosted websites, mail, DNS and
databases never depend on it: a missing or refused license only restricts
the administrator interface and account creation.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import threading

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from shared.config import settings

# license.boronpanel.com signing key (key_id ca6f182f8022c85f).
LICENSE_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAZ0YvNmZSS5IFUEUuFQiFqL5IEH4lk0RiaGg4s9GMW+0=
-----END PUBLIC KEY-----
"""
DOCUMENT_FORMAT = 1
# Admins are warned once the last successful check is this old; the panel
# keeps working until the document's own valid_until (14 days by default).
WARN_AFTER = timedelta(days=3)
KEY_PATTERN = re.compile(r"^BRN(-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){4}$")
# Administrator requests that stay available while the license is not usable,
# so an admin can always sign in, see why, and enter or fix the license.
EXEMPT_API_PREFIXES = (
    "/api/v1/admin/license",
    "/api/v1/whoami",
    "/api/v1/version",
    "/api/v1/branding",
    "/logout",
    "/change-password",
)
REASON_TEXT = {
    "no_license": "No license key has been entered for this server.",
    "not_activated": "The license has not been activated on this server yet.",
    "invalid_document": "The stored license document is damaged or was not issued by Boron.",
    "install_mismatch": "The stored license belongs to a different installation.",
    "suspended": "The license is suspended.",
    "cancelled": "The license has been cancelled.",
    "expired": "The license has expired.",
    "ip_mismatch": "The license is bound to a different server IP address.",
    "check_overdue": "The license server has not been reached for too long.",
    "check_overdue_soon": "The license server has not been reached recently.",
}


def normalize_key(raw: str) -> str | None:
    key = re.sub(r"\s+", "", raw or "").upper()
    return key if KEY_PATTERN.fullmatch(key) else None


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def verify(document: dict, public_key_pem: bytes | None = None) -> dict:
    """Return the payload of a document signed by the license server, or raise ValueError."""
    try:
        payload = document["payload"]
        signature = base64.b64decode(document["signature"], validate=True)
        if not isinstance(payload, dict) or payload.get("format") != DOCUMENT_FORMAT:
            raise TypeError
        load_pem_public_key(public_key_pem or LICENSE_PUBLIC_KEY_PEM).verify(signature, canonical(payload))
    except (KeyError, TypeError, binascii.Error, InvalidSignature) as exc:
        raise ValueError("license document signature is invalid") from exc
    return payload


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def evaluate(document: dict | None, *, install_id: str | None, key_present: bool,
             now: datetime | None = None, public_key_pem: bytes | None = None) -> dict:
    """Decide whether administration is usable from a stored document.

    ``state`` is ``active``, ``warning`` (usable, but checks are failing),
    ``unlicensed`` or ``locked``; ``usable`` is what enforcement looks at.
    """
    now = now or datetime.now(timezone.utc)
    result: dict = {"state": "locked", "usable": False, "reason": None, "license": None}
    if document is None:
        result.update(state="unlicensed", reason="not_activated" if key_present else "no_license")
        return _with_text(result)
    try:
        payload = verify(document, public_key_pem)
    except ValueError:
        result["reason"] = "invalid_document"
        return _with_text(result)

    result["license"] = {
        "license_id": payload.get("license_id"), "key_hint": payload.get("key_hint"),
        "tier": payload.get("tier"), "max_accounts": payload.get("max_accounts"),
        "bound_ip": payload.get("bound_ip"), "status": payload.get("status"),
        "channel": payload.get("channel"), "issued_at": payload.get("issued_at"),
        "valid_until": payload.get("valid_until"), "expires_at": payload.get("expires_at"),
        "updates_until": payload.get("updates_until"),
    }
    if install_id is None or payload.get("install_id") != install_id:
        result["reason"] = "install_mismatch"
    elif payload.get("status") != "active":
        result["reason"] = payload.get("status") or "invalid_document"
    else:
        try:
            issued_at = _parse_time(payload.get("issued_at"))
            valid_until = _parse_time(payload.get("valid_until"))
        except ValueError:
            issued_at = valid_until = None
        if issued_at is None or valid_until is None:
            result["reason"] = "invalid_document"
        elif now >= valid_until:
            expires_at = _parse_time(payload.get("expires_at"))
            result["reason"] = "expired" if expires_at is not None and now >= expires_at else "check_overdue"
        elif now - issued_at > WARN_AFTER:
            result.update(state="warning", usable=True, reason="check_overdue_soon")
        else:
            result.update(state="active", usable=True)
    return _with_text(result)


def _with_text(result: dict) -> dict:
    result["message"] = REASON_TEXT.get(result["reason"] or "", "")
    return result


def read_install_id(path: Path | None = None) -> str | None:
    try:
        value = (path or Path(settings.license_install_id_path)).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value or None


def read_document(path: Path | None = None) -> dict | None:
    try:
        document = json.loads((path or Path(settings.license_document_path)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


_cache_lock = threading.Lock()
_cache: dict = {"stamp": None, "document": None, "install_id": None, "key_present": False}


def _stamp() -> tuple:
    stamps = []
    for raw in (settings.license_document_path, settings.license_install_id_path, settings.license_key_path):
        try:
            stat = os.stat(raw)
            stamps.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append(None)
    return tuple(stamps)


def current_state(now: datetime | None = None) -> dict:
    """State from the files on disk; re-read only when they change."""
    stamp = _stamp()
    with _cache_lock:
        if _cache["stamp"] != stamp:
            _cache.update(stamp=stamp, document=read_document(), install_id=read_install_id(),
                          key_present=stamp[2] is not None)
        document, install_id, key_present = _cache["document"], _cache["install_id"], _cache["key_present"]
    return evaluate(document, install_id=install_id, key_present=key_present, now=now)


def admin_path_allowed(path: str) -> bool:
    """Whether an administrator request to ``path`` may proceed."""
    if not settings.license_enforcement or path.startswith(EXEMPT_API_PREFIXES):
        return True
    return current_state()["usable"]


def account_limit() -> int | None:
    """Maximum hosting accounts the license allows; None means unlimited or not enforced."""
    if not settings.license_enforcement:
        return None
    state = current_state()
    if not state["usable"] or state["license"] is None:
        return 0
    return state["license"]["max_accounts"]
