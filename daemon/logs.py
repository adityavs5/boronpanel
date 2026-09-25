"""Per-account error log viewer (Phase 3 feature 9).

Strictly scoped to the requesting account's own log files -- every path
is built from known-safe components (the account's own home dir, plus a
vhost name derived from a domain already confirmed to belong to that
account) and then re-verified with the same os.path.realpath jail check
daemon/filemanager.py uses, so even a bug in the "safe components" logic
above it can't actually escape the account's own logs directory. No raw
user-supplied path segment ever reaches the filesystem here.
"""
from __future__ import annotations

import os
import base64
import datetime as dt
import gzip
import io
import re
import stat

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError, validate_domain, validate_username
from daemon import appunits, safeio

MAX_LINES = 500
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_VIEW_BYTES = 2 * 1024 * 1024
LOG_TYPES = ("ols", "error", "access", "php")


class LogsError(Exception):
    pass


def _account_log_dir(username: str) -> str:
    home = os.path.realpath(f"{settings.home_base}/{username}")
    return os.path.join(home, "logs")


def _jailed_log_path(username: str, filename: str) -> str:
    """filename is always derived from validated, known-safe inputs by
    the callers below -- this re-verification is defense in depth, not
    the primary defense (there is no user-supplied path segment to
    defend against in the first place)."""
    log_dir = _account_log_dir(username)
    candidate = os.path.realpath(os.path.join(log_dir, filename))
    if candidate != log_dir and not candidate.startswith(log_dir + os.sep):
        raise LogsError("resolved log path escapes the account's own log directory")
    return candidate


def _primary_domain(username: str, account_id: int) -> str:
    with write_session() as session:
        row = session.scalar(
            select(Domain).where(Domain.account_id == account_id, Domain.kind == "primary")
        )
        if row is None:
            raise LogsError(f"account '{username}' has no primary domain")
        return row.domain


def _domain_belongs_to_account(domain_name: str, account_id: int) -> bool:
    with write_session() as session:
        row = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account_id))
        return row is not None


def _vhost_name(domain: str) -> str:
    """Same domain -> vhost-identifier mapping as daemon/ols.py's own
    _vhost_name (collision-free: valid domains never contain
    underscores) -- duplicated rather than importing a private
    cross-module helper for what's a one-line, stable transform."""
    return domain.replace(".", "_")


def _tail_lines(path: str, max_lines: int) -> list[str]:
    # Open through anchored directory descriptors, never follow a path that a
    # tenant can replace between validation and reading, and bound bytes.
    try:
        return appunits.tail_log_file(path, max_lines)
    except (safeio.UnsafePathError, OSError, ValueError) as exc:
        raise LogsError("log path is not a safe regular file") from exc


def _base_filename(log_type: str, domain_name: str | None) -> str:
    if log_type == "php":
        return "php-error.log"
    suffix = "access" if log_type == "access" else "error"
    return f"{_vhost_name(domain_name)}-{suffix}.log"


def _safe_segments(username: str, base_name: str) -> list[dict]:
    log_dir = _account_log_dir(username)
    try:
        directory = safeio.open_dir_beneath(settings.home_base, f"{username}/logs")
    except FileNotFoundError:
        return []
    except (safeio.UnsafePathError, OSError) as exc:
        raise LogsError("log directory is inaccessible or unsafe") from exc
    rows = []
    try:
        for name in os.listdir(directory):
            if name != base_name and not name.startswith(base_name + "."):
                continue
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise LogsError("log segment is not a safe regular file")
            rows.append({
                "name": name,
                "active": name == base_name,
                "compressed": name.endswith(".gz"),
                "size": info.st_size,
                "modified_at": dt.datetime.fromtimestamp(info.st_mtime, dt.timezone.utc).isoformat(),
            })
    finally:
        os.close(directory)
    rows.sort(key=lambda row: (not row["active"], row["modified_at"]), reverse=False)
    active = [row for row in rows if row["active"]]
    rotated = sorted((row for row in rows if not row["active"]), key=lambda row: row["modified_at"], reverse=True)
    return active + rotated


def _safe_read_segment(username: str, name: str, *, max_bytes: int) -> bytes:
    try:
        directory = safeio.open_dir_beneath(settings.home_base, f"{username}/logs")
    except (FileNotFoundError, safeio.UnsafePathError, OSError) as exc:
        raise LogsError("log directory is missing or inaccessible") from exc
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        except OSError as exc:
            raise LogsError("log segment is missing or inaccessible") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise LogsError(f"log segment exceeds the {max_bytes // (1024 * 1024)} MiB safety limit")
            chunks = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > max_bytes:
                raise LogsError(f"log segment exceeds the {max_bytes // (1024 * 1024)} MiB safety limit")
            return data
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def _segment_lines(username: str, name: str, lines: int) -> list[str]:
    raw = _safe_read_segment(username, name, max_bytes=MAX_VIEW_BYTES)
    if name.endswith(".gz"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as archive:
                raw = archive.read(MAX_VIEW_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise LogsError("compressed log segment is corrupt") from exc
        if len(raw) > MAX_VIEW_BYTES:
            raise LogsError("expanded log segment exceeds the 2 MiB viewer safety limit; download it instead")
    content = raw.decode("utf-8", errors="replace").splitlines()
    return content[-lines:]


_DATE_PATTERNS = (
    (re.compile(r"\[(\d{2}/[A-Za-z]{3}/\d{4}):"), "%d/%b/%Y"),
    (re.compile(r"\[(\d{2}-[A-Za-z]{3}-\d{4})"), "%d-%b-%Y"),
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
)


def _line_date(line: str) -> dt.date | None:
    for pattern, fmt in _DATE_PATTERNS:
        match = pattern.search(line)
        if match:
            try:
                return dt.datetime.strptime(match.group(1), fmt).date()
            except ValueError:
                return None
    return None


def _date_value(value, name: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"{name} must be YYYY-MM-DD") from None


def _matches_severity(line: str, severity: str | None) -> bool:
    if not severity:
        return True
    return severity.lower() in line.lower()


def get_log(params: dict) -> dict:
    username = validate_username(params["username"])
    log_type = params.get("type", "ols")
    if log_type not in LOG_TYPES:
        raise ValidationError(f"log type must be one of {LOG_TYPES}")
    if log_type == "ols":
        log_type = "error"  # backward-compatible route/value
    severity = params.get("severity") or None
    lines_requested = max(1, min(int(params.get("lines", MAX_LINES)), MAX_LINES))
    from_date = _date_value(params.get("from_date"), "from_date")
    to_date = _date_value(params.get("to_date"), "to_date")
    if from_date and to_date and from_date > to_date:
        raise ValidationError("from_date must not be after to_date")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        account_id = account.id

    if log_type == "php":
        domain_used = None
    else:
        domain_param = params.get("domain")
        if domain_param:
            domain_name = validate_domain(domain_param)
            if not _domain_belongs_to_account(domain_name, account_id):
                raise RuntimeError(f"domain '{domain_name}' does not belong to account '{username}'")
        else:
            domain_name = _primary_domain(username, account_id)
        domain_used = domain_name
    base_name = _base_filename(log_type, domain_used)
    _jailed_log_path(username, base_name)
    segments = _safe_segments(username, base_name)
    requested_segment = str(params.get("segment") or base_name)
    if requested_segment not in {row["name"] for row in segments}:
        if requested_segment != base_name:
            raise ValidationError("selected log segment is not available")
        all_lines = []
        state = "missing"
    else:
        selected = next(row for row in segments if row["name"] == requested_segment)
        state = "empty" if selected["size"] == 0 else "available"
        if params.get("_metadata_only"):
            all_lines = []
        elif selected["active"] and not selected["compressed"]:
            all_lines = _tail_lines(_jailed_log_path(username, requested_segment), lines_requested)
        else:
            all_lines = _segment_lines(username, requested_segment, lines_requested)
    filtered = []
    for line in all_lines:
        if not _matches_severity(line, severity):
            continue
        if from_date or to_date:
            parsed = _line_date(line)
            if parsed is None or (from_date and parsed < from_date) or (to_date and parsed > to_date):
                continue
        filtered.append(line)

    return {
        "username": username,
        "type": log_type,
        "domain": domain_used,
        "state": state,
        "segment": requested_segment,
        "segments": segments,
        "severity_filter": severity,
        "from_date": from_date.isoformat() if from_date else None,
        "to_date": to_date.isoformat() if to_date else None,
        "line_count": len(filtered),
        "lines": filtered,
    }


def download_log(params: dict) -> dict:
    listing = get_log({**params, "lines": 1, "_metadata_only": True})
    name = listing["segment"]
    if listing["state"] == "missing":
        raise LogsError("log segment does not exist")
    raw = _safe_read_segment(listing["username"], name, max_bytes=MAX_DOWNLOAD_BYTES)
    return {
        "filename": name,
        "content_type": "application/gzip" if name.endswith(".gz") else "text/plain; charset=utf-8",
        "size": len(raw),
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }
