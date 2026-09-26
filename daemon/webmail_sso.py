"""One-time Roundcube launch exchange with short-lived Dovecot credentials."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
import pwd
import secrets
import socket
import stat
from pathlib import Path

from sqlalchemy import delete, select, update

from daemon import audit, mail
from daemon.appcrypto import decrypt_secret, encrypt_secret
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain, MailUser, WebmailLaunch
from shared.validation import ValidationError, validate_email_address, validate_username

logger = logging.getLogger("borond.webmail_sso")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ensure_schema() -> None:
    connection = mail._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS webmail_session (
                  launch_id BIGINT NOT NULL PRIMARY KEY,
                  mailbox VARCHAR(320) NOT NULL,
                  password VARCHAR(255) NOT NULL,
                  expires_at DATETIME NOT NULL,
                  revoked TINYINT(1) NOT NULL DEFAULT 0,
                  INDEX ix_webmail_mailbox (mailbox),
                  INDEX ix_webmail_expiry (expires_at)
                ) ENGINE=InnoDB
            """)
            cursor.execute("DELETE FROM webmail_session WHERE revoked=1 OR expires_at < UTC_TIMESTAMP()")
    finally:
        connection.close()


def _mailbox_owned(db, account: Account, mailbox: str) -> MailUser:
    local, domain = validate_email_address(mailbox).rsplit("@", 1)
    domain_row = db.scalar(select(MailDomain).where(
        MailDomain.domain == domain, MailDomain.account_id == account.id,
    ))
    if domain_row is None:
        raise ValidationError("Mailbox does not belong to this account")
    row = db.scalar(select(MailUser).where(
        MailUser.mail_domain_id == domain_row.id,
        MailUser.local_part == local,
        MailUser.domain == domain,
    ))
    if row is None:
        raise ValidationError("Mailbox does not exist")
    # Recheck the live authentication source instead of trusting only cache.
    live = next((item for item in mail.list_mailboxes(domain) if item["local_part"] == local), None)
    if live is None or not live.get("active"):
        raise ValidationError("Mailbox is disabled")
    return row


def create_launch(params: dict) -> dict:
    username = validate_username(params["username"])
    mailbox = validate_email_address(params["mailbox"]).lower()
    now = _now()
    with write_session() as db:
        db.execute(delete(WebmailLaunch).where(
            WebmailLaunch.created_at < now - dt.timedelta(days=7),
        ))
        account = db.scalar(select(Account).where(Account.username == username))
        if account is None or account.status != "active":
            raise ValidationError("Hosting account is not active")
        _mailbox_owned(db, account, mailbox)
        recent = db.scalars(select(WebmailLaunch).where(
            WebmailLaunch.account_id == account.id,
            WebmailLaunch.created_at >= now - dt.timedelta(minutes=1),
        )).all()
        if len(recent) >= 10:
            raise ValidationError("Too many webmail launch requests; wait a minute and try again")
        token = secrets.token_urlsafe(32)
        credential = secrets.token_urlsafe(36)
        expiry = now + dt.timedelta(seconds=max(15, min(settings.webmail_launch_ttl_seconds, 60)))
        row = WebmailLaunch(
            token_hash=hashlib.sha256(token.encode()).hexdigest(), account_id=account.id,
            mailbox=mailbox, credential_hash=mail.hash_password(credential),
            credential_enc=encrypt_secret(credential), source_ip=params.get("source_ip"),
            expires_at=expiry,
        )
        db.add(row)
        db.flush()
        launch_id = row.id

    ensure_schema()
    connection = mail._connect()
    try:
        with connection.cursor() as cursor:
            credential_expiry = now + dt.timedelta(seconds=max(300, min(settings.webmail_session_ttl_seconds, 86400)))
            cursor.execute(
                "INSERT INTO webmail_session (launch_id,mailbox,password,expires_at,revoked) VALUES (%s,%s,%s,%s,0)",
                (launch_id, mailbox, mail.hash_password(credential), credential_expiry.replace(tzinfo=None)),
            )
    except Exception:
        with write_session() as db:
            failed = db.get(WebmailLaunch, launch_id)
            if failed:
                db.delete(failed)
        raise
    finally:
        connection.close()
    return {
        "token": token, "url": settings.webmail_url,
        "expires_at": expiry.isoformat(), "mailbox": mailbox,
    }


def _as_utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _source_ip(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValidationError("Invalid source address")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValidationError("Invalid source address") from exc


def _audit_event(op: str, target: str | None, source_ip: str | None, result: str,
                 *, launch_id: int | None = None, detail: str = "") -> None:
    """Audit the local Roundcube exchange without making login depend on logging."""
    params = {"source_ip": source_ip}
    if launch_id is not None:
        params["launch_id"] = launch_id
    try:
        audit.record("roundcube", "service", op, target, params, result, detail)
    except Exception:  # noqa: BLE001 -- a full audit disk/database must not consume a login
        logger.exception("could not record %s audit event", op)


def redeem(token: str, source_ip: str | None = None) -> dict:
    if not isinstance(token, str) or not (32 <= len(token) <= 128):
        raise ValidationError("Invalid launch token")
    source_ip = _source_ip(source_ip)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with write_session() as db:
        row = db.scalar(select(WebmailLaunch).where(WebmailLaunch.token_hash == digest))
        if row is None or row.redeemed_at is not None or row.revoked_at is not None:
            raise ValidationError("Launch token is invalid or was already used")
        if _as_utc(row.expires_at) < _now():
            row.revoked_at = _now()
            raise ValidationError("Launch token has expired")
        account = db.get(Account, row.account_id)
        if account is None or account.status != "active":
            row.revoked_at = _now()
            raise ValidationError("Hosting account is not active")
        _mailbox_owned(db, account, row.mailbox)
        credential = decrypt_secret(row.credential_enc)
        redeemed_at = _now()
        claimed = db.execute(update(WebmailLaunch).where(
            WebmailLaunch.id == row.id,
            WebmailLaunch.redeemed_at.is_(None),
            WebmailLaunch.revoked_at.is_(None),
        ).values(redeemed_at=redeemed_at))
        if claimed.rowcount != 1:
            raise ValidationError("Launch token is invalid or was already used")
        result = {"launch_id": row.id, "username": row.mailbox, "password": credential}
    _audit_event("webmail.launch.redeem", result["username"], source_ip, "ok",
                 launch_id=result["launch_id"])
    return result


def revoke(launch_id: int, source_ip: str | None = None) -> None:
    if isinstance(launch_id, bool) or not isinstance(launch_id, int) or launch_id <= 0:
        raise ValidationError("Invalid webmail session")
    source_ip = _source_ip(source_ip)
    mailbox = None
    with write_session() as db:
        row = db.get(WebmailLaunch, launch_id)
        if row and row.revoked_at is None:
            row.revoked_at = _now()
        if row:
            mailbox = row.mailbox
    ensure_schema()
    connection = mail._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE webmail_session SET revoked=1 WHERE launch_id=%s", (launch_id,))
    finally:
        connection.close()
    _audit_event("webmail.launch.revoke", mailbox, source_ip, "ok", launch_id=launch_id)


def revoke_mailbox(mailbox: str) -> None:
    mailbox = validate_email_address(mailbox).lower()
    with write_session() as db:
        rows = db.scalars(select(WebmailLaunch).where(
            WebmailLaunch.mailbox == mailbox, WebmailLaunch.revoked_at.is_(None),
        )).all()
        for row in rows:
            row.revoked_at = _now()
    ensure_schema()
    connection = mail._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE webmail_session SET revoked=1 WHERE mailbox=%s", (mailbox,))
    finally:
        connection.close()


def revoke_domain(domain: str) -> None:
    suffix = "%@" + domain.lower()
    with write_session() as db:
        rows = db.scalars(select(WebmailLaunch).where(
            WebmailLaunch.mailbox.like(suffix), WebmailLaunch.revoked_at.is_(None),
        )).all()
        ids = [row.id for row in rows]
        for row in rows:
            row.revoked_at = _now()
    if not ids:
        return
    ensure_schema()
    connection = mail._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE webmail_session SET revoked=1 WHERE mailbox LIKE %s", (suffix,))
    finally:
        connection.close()


def revoke_account(account_id: int) -> None:
    with write_session() as db:
        rows = db.scalars(select(WebmailLaunch).where(
            WebmailLaunch.account_id == account_id, WebmailLaunch.revoked_at.is_(None),
        )).all()
        ids = [row.id for row in rows]
        for row in rows:
            row.revoked_at = _now()
    if not ids:
        return
    ensure_schema()
    connection = mail._connect()
    try:
        with connection.cursor() as cursor:
            cursor.executemany("UPDATE webmail_session SET revoked=1 WHERE launch_id=%s", [(item,) for item in ids])
    finally:
        connection.close()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    action = None
    source_ip = None
    try:
        if sock is None or not hasattr(socket, "SO_PEERCRED"):
            raise PermissionError("peer credentials unavailable")
        import struct
        _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != pwd.getpwnam("www-data").pw_uid:
            raise PermissionError("untrusted peer")
        payload = await asyncio.wait_for(reader.readline(), timeout=3)
        if not payload or len(payload) > 2048:
            raise ValidationError("Invalid request")
        request = json.loads(payload)
        if not isinstance(request, dict) or set(request) - {"action", "token", "launch_id", "source_ip"}:
            raise ValidationError("Invalid request")
        action = request.get("action")
        source_ip = _source_ip(request.get("source_ip"))
        if action == "redeem":
            result = redeem(request.get("token"), source_ip)
        elif action == "revoke":
            revoke(request.get("launch_id"), source_ip)
            result = {"revoked": True}
        else:
            raise ValidationError("Invalid action")
        response = {"ok": True, "result": result}
    except Exception as exc:
        if action in ("redeem", "revoke"):
            _audit_event(f"webmail.launch.{action}", None, source_ip, "failed",
                         detail=type(exc).__name__)
        if isinstance(exc, (ValidationError, PermissionError)):
            message = str(exc)[:200]
        else:
            logger.exception("webmail exchange failed")
            message = "Webmail launch exchange failed"
        response = {"ok": False, "error": message}
    writer.write((json.dumps(response, separators=(",", ":")) + "\n").encode())
    await writer.drain()
    writer.close()


async def start_server() -> asyncio.AbstractServer:
    path = Path(settings.webmail_launch_socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    server = await asyncio.start_unix_server(handle_client, path=str(path), limit=4096)
    gid = pwd.getpwnam("www-data").pw_gid
    os.chown(path.parent, 0, gid)
    os.chmod(path.parent, stat.S_IRWXU | stat.S_IXGRP)
    os.chown(path, 0, gid)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    return server
