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

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError, validate_domain, validate_username

MAX_LINES = 500
LOG_TYPES = ("ols", "php")


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
    if not os.path.isfile(path):
        return []
    with open(path, "r", errors="replace") as f:
        lines = f.readlines()
    return [ln.rstrip("\n") for ln in lines[-max_lines:]]


def _matches_severity(line: str, severity: str | None) -> bool:
    if not severity:
        return True
    return severity.lower() in line.lower()


def get_log(params: dict) -> dict:
    username = validate_username(params["username"])
    log_type = params.get("type", "ols")
    if log_type not in LOG_TYPES:
        raise ValidationError(f"log type must be one of {LOG_TYPES}")
    severity = params.get("severity") or None
    lines_requested = min(int(params.get("lines", MAX_LINES)), MAX_LINES)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        account_id = account.id

    if log_type == "php":
        path = _jailed_log_path(username, "php-error.log")
        domain_used = None
    else:
        domain_param = params.get("domain")
        if domain_param:
            domain_name = validate_domain(domain_param)
            if not _domain_belongs_to_account(domain_name, account_id):
                raise RuntimeError(f"domain '{domain_name}' does not belong to account '{username}'")
        else:
            domain_name = _primary_domain(username, account_id)
        path = _jailed_log_path(username, f"{_vhost_name(domain_name)}-error.log")
        domain_used = domain_name

    all_lines = _tail_lines(path, lines_requested)
    filtered = [ln for ln in all_lines if _matches_severity(ln, severity)]

    return {
        "username": username,
        "type": log_type,
        "domain": domain_used,
        "severity_filter": severity,
        "line_count": len(filtered),
        "lines": filtered,
    }
