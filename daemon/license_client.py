"""Talks to the Boron license server and keeps the signed document current.

Runs as root inside borond (RPC ``license.*``) and from the periodic
``scripts/license_check.py``. Only the install ID, version, hostname and the
number of hosting accounts are sent; never any hosting data.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
import tempfile
import uuid

import httpx
from sqlalchemy import func, select

from shared import license as license_state
from shared.config import settings
from shared.db import read_session
from shared.models import Account
from shared.validation import ValidationError
from version import BORON_VERSION

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 15


def _write_atomic(path: Path, data: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def install_id() -> str:
    """Stable identifier for this installation, created on first use."""
    path = Path(settings.license_install_id_path)
    existing = license_state.read_install_id(path)
    if existing:
        return existing
    value = str(uuid.uuid4())
    _write_atomic(path, value + "\n", 0o644)
    return value


def read_key() -> str | None:
    try:
        return license_state.normalize_key(Path(settings.license_key_path).read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError):
        return None


def _account_count() -> int:
    with read_session() as session:
        return int(session.scalar(select(func.count(Account.id)).where(Account.status != "terminated")) or 0)


def _record(**fields) -> None:
    """Remember the last attempt so the License page can explain failures."""
    path = Path(settings.license_state_path)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    current.update(fields)
    _write_atomic(path, json.dumps(current, sort_keys=True), 0o644)


def _last_attempt() -> dict:
    try:
        return json.loads(Path(settings.license_state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class LicenseServerError(RuntimeError):
    pass


def _call(endpoint: str, key: str) -> dict:
    """POST to the license server; returns a verified document or raises."""
    body = {"license_key": key, "install_id": install_id(), "hostname": settings.panel_hostname[:253],
            "version": BORON_VERSION, "account_count": _account_count()}
    url = settings.license_server_url.rstrip("/") + f"/v1/{endpoint}"
    try:
        response = httpx.post(url, json=body, timeout=TIMEOUT_SECONDS,
                              headers={"User-Agent": f"boron-panel/{BORON_VERSION}"})
    except httpx.HTTPError as exc:
        raise LicenseServerError(f"license server unreachable: {exc.__class__.__name__}") from exc
    if response.status_code == 403 and response.headers.get("content-type", "").startswith("application/json"):
        if response.json().get("error") == "invalid_license":
            raise ValidationError("The license key was not recognised by the license server.")
    if response.status_code != 200:
        raise LicenseServerError(f"license server answered HTTP {response.status_code}")
    document = response.json()
    payload = license_state.verify(document)
    if payload.get("install_id") != body["install_id"]:
        raise LicenseServerError("license server answered for a different installation")
    return document


def _store_document(document: dict) -> None:
    _write_atomic(Path(settings.license_document_path), json.dumps(document, sort_keys=True), 0o644)


def refresh(params: dict | None = None) -> dict:
    """Periodic check. Activates instead when this server has no usable binding yet."""
    key = read_key()
    if key is None:
        return status()
    current = license_state.read_document()
    try:
        payload = license_state.verify(current) if current else None
    except ValueError:
        payload = None
    endpoint = "activate" if payload is None or payload.get("status") == "not_activated" else "check"
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        document = _call(endpoint, key)
    except (LicenseServerError, ValidationError, ValueError) as exc:
        _record(last_attempt_at=now, last_error=str(exc))
        logger.warning("license %s failed: %s", endpoint, exc)
        return status()
    _store_document(document)
    _record(last_attempt_at=now, last_success_at=now, last_error=None)
    return status()


def activate(params: dict) -> dict:
    """Save a license key entered by the administrator and activate it here."""
    key = license_state.normalize_key(str(params.get("license_key", "")))
    if key is None:
        raise ValidationError("Enter a license key in the form BRN-XXXX-XXXX-XXXX-XXXX.")
    try:
        document = _call("activate", key)
    except LicenseServerError as exc:
        raise ValidationError(f"Could not reach the license server: {exc}") from exc
    _write_atomic(Path(settings.license_key_path), key + "\n", 0o600)
    _store_document(document)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _record(last_attempt_at=now, last_success_at=now, last_error=None)
    return status()


def status(params: dict | None = None) -> dict:
    state = license_state.current_state()
    attempt = _last_attempt()
    state.update(
        enforcement=settings.license_enforcement,
        install_id=license_state.read_install_id(),
        account_count=_account_count(),
        last_attempt_at=attempt.get("last_attempt_at"),
        last_success_at=attempt.get("last_success_at"),
        last_error=attempt.get("last_error"),
        server=settings.license_server_url,
    )
    return state


def require_account_capacity() -> None:
    """Refuse a new hosting account the license does not cover."""
    limit = license_state.account_limit()
    if limit is None:
        return
    if limit == 0 and not license_state.current_state()["usable"]:
        raise ValidationError("This server's Boron license is not active; new accounts cannot be created. "
                              "See Admin > License.")
    if _account_count() >= limit:
        raise ValidationError(f"This server's license allows {limit} hosting account(s). "
                              "Upgrade the license to add more.")
