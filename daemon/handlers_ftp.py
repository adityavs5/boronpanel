"""FTP sub-account CRUD (Phase 3 feature 5). Validates/jails the path
restriction and keeps SQLite bookkeeping (owning account, login, path --
never the password, which lives only in PureDB's own pureftpd.pdb);
daemon/ftp.py does the actual `pure-pw` provisioning.
"""
from __future__ import annotations

import os
import pwd

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, FtpAccount
from shared.validation import ValidationError, validate_db_identifier, validate_password_strength, validate_username

from daemon import account_mutation, ftp, resource_limits
from daemon.safeio import secure_mkdirs

MAX_LOGIN_SUFFIX_LEN = 32


def _ftp_login(username: str, label: str) -> str:
    validate_db_identifier(label, max_len=MAX_LOGIN_SUFFIX_LEN)
    return f"{username}_{label}"


def _resolve_path(username: str, relative_path: str) -> str:
    """Same symlink-aware jail-check pattern as daemon/filemanager.py's
    _resolve -- the path restriction IS the whole point of an FTP
    sub-account, so it gets the same defense-in-depth treatment as the
    file manager's own jail (checked here, in the daemon, regardless of
    whatever validation the API layer above also does)."""
    home = os.path.realpath(f"{settings.home_base}/{username}")
    if not os.path.isdir(home):
        raise ValidationError(f"account '{username}' has no home directory")

    relative_path = (relative_path or "").lstrip("/")
    candidate = os.path.normpath(os.path.join(home, relative_path))

    resolved = os.path.realpath(candidate)
    ancestor = resolved
    while not os.path.exists(ancestor) and ancestor != home and len(ancestor) > len(home):
        ancestor = os.path.dirname(ancestor)
    check_target = os.path.realpath(ancestor)

    if check_target != home and not check_target.startswith(home + os.sep):
        raise ValidationError("path escapes the account's home directory")
    if resolved != home and not resolved.startswith(home + os.sep):
        raise ValidationError("path escapes the account's home directory")
    return resolved


def _ensure_path_exists(username: str, path: str, uid: int, gid: int) -> None:
    home = os.path.join(settings.home_base, username)
    relative = os.path.relpath(path, home)
    if relative == ".":
        return
    secure_mkdirs(home, relative, uid, gid, 0o750)


def _row_to_dict(row: FtpAccount, username: str) -> dict:
    label = row.ftp_login[len(username) + 1 :] if row.ftp_login.startswith(f"{username}_") else row.ftp_login
    return {
        "id": row.id,
        "account_id": row.account_id,
        "ftp_login": row.ftp_login,
        "label": label,
        "path": row.path,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@account_mutation.locked
def create_ftp_account(params: dict) -> dict:
    username = validate_username(params["username"])
    label = params["label"]
    relative_path = params.get("path", "")
    password = validate_password_strength(params["password"])

    ftp_login = _ftp_login(username, label)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot add an FTP account to an account in status '{account.status}'")
        existing = session.scalar(select(FtpAccount).where(FtpAccount.ftp_login == ftp_login))
        if existing is not None:
            raise RuntimeError(f"FTP account '{ftp_login}' already exists")
        resource_limits.require_capacity(session, account.id, "ftp")
        account_id = account.id

    abs_path = _resolve_path(username, relative_path)
    pw = pwd.getpwnam(username)
    _ensure_path_exists(username, abs_path, pw.pw_uid, pw.pw_gid)

    ftp.create_ftp_user(ftp_login, pw.pw_uid, pw.pw_gid, abs_path, password)

    try:
        with write_session() as session:
            row = FtpAccount(account_id=account_id, ftp_login=ftp_login, path=abs_path)
            session.add(row)
            session.flush()
            return _row_to_dict(row, username)
    except Exception:
        ftp.delete_ftp_user(ftp_login)
        raise


def list_ftp_accounts(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        rows = session.scalars(select(FtpAccount).where(FtpAccount.account_id == account.id)).all()
        return {"ftp_accounts": [_row_to_dict(r, username) for r in rows]}


def _get_owned_ftp_account(session, username: str, label: str) -> FtpAccount:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    ftp_login = _ftp_login(username, label)
    row = session.scalar(select(FtpAccount).where(FtpAccount.ftp_login == ftp_login, FtpAccount.account_id == account.id))
    if row is None:
        raise RuntimeError(f"FTP account '{ftp_login}' not found for '{username}'")
    return row


def set_ftp_path(params: dict) -> dict:
    username = validate_username(params["username"])
    label = params["label"]
    relative_path = params["path"]

    with write_session() as session:
        row = _get_owned_ftp_account(session, username, label)
        ftp_login = row.ftp_login

    abs_path = _resolve_path(username, relative_path)
    pw = pwd.getpwnam(username)
    _ensure_path_exists(username, abs_path, pw.pw_uid, pw.pw_gid)
    ftp.set_path(ftp_login, pw.pw_uid, pw.pw_gid, abs_path)

    with write_session() as session:
        row = _get_owned_ftp_account(session, username, label)
        row.path = abs_path
        return _row_to_dict(row, username)


def change_ftp_password(params: dict) -> dict:
    username = validate_username(params["username"])
    label = params["label"]
    password = validate_password_strength(params["password"])

    with write_session() as session:
        row = _get_owned_ftp_account(session, username, label)
        ftp_login = row.ftp_login

    ftp.set_password(ftp_login, password)
    return {"ftp_login": ftp_login, "status": "password_changed"}


def delete_ftp_account(params: dict) -> dict:
    username = validate_username(params["username"])
    label = params["label"]

    with write_session() as session:
        row = _get_owned_ftp_account(session, username, label)
        ftp_login = row.ftp_login

    ftp.delete_ftp_user(ftp_login)
    with write_session() as session:
        row = _get_owned_ftp_account(session, username, label)
        session.delete(row)
    return {"ftp_login": ftp_login, "status": "deleted"}


def terminate_account_ftp(account: Account) -> None:
    """TERMINATE_HOOKS entry: remove every FTP sub-account this account
    owns. Idempotent -- safe even if the account never had one."""
    with write_session() as session:
        rows = session.scalars(select(FtpAccount).where(FtpAccount.account_id == account.id)).all()
        logins = [r.ftp_login for r in rows]

    for ftp_login in logins:
        ftp.delete_ftp_user(ftp_login)
        with write_session() as session:
            row = session.scalar(select(FtpAccount).where(FtpAccount.ftp_login == ftp_login, FtpAccount.account_id == account.id))
            if row is not None:
                session.delete(row)
