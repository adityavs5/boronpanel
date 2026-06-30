"""Mailbox/mail-domain CRUD (Phase e). Webmail itself is explicitly out of
scope (project goal: "link out to Roundcube, don't build webmail") -- these
ops only provision mail routing/storage; `webmail_url` in forgehost.toml is
what the admin UI links out to.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, MailDomain, MailUser
from shared.validation import validate_domain, validate_mailbox_local_part, validate_username

from daemon import mail


def create_mail_domain(params: dict) -> dict:
    username = params.get("username")
    domain_name = validate_domain(params["domain"])

    with write_session() as session:
        account = None
        if username:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise RuntimeError(f"account '{username}' not found")
        existing = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
        if existing is not None:
            raise RuntimeError(f"mail domain '{domain_name}' already provisioned")

    if mail.domain_exists(domain_name):
        raise RuntimeError(f"mail domain '{domain_name}' already exists")

    mail.create_mail_domain(domain_name)

    with write_session() as session:
        row = MailDomain(account_id=account.id if account else None, domain=domain_name)
        session.add(row)
        session.flush()
        return {"id": row.id, "domain": row.domain, "account_id": row.account_id}


def _delete_mail_domain_cache(domain_name: str) -> None:
    """forgehost_mail's own schema cascades mail_user on mail_domain delete
    (ON DELETE CASCADE), but the SQLite cache mirror has no such cascade --
    child MailUser rows must be deleted and flushed before the MailDomain
    delete or the FK constraint (PRAGMA foreign_keys=ON) rejects it. A bare
    session.delete() on both rows without an intermediate flush() isn't
    enough: with no ORM-level `relationship()` between the two models (just
    raw ForeignKey columns), SQLAlchemy's unit-of-work has no dependency
    information to order the two deletes within one flush, so both can be
    emitted in the same batch regardless of the FK. Caught by a real test
    run, not assumed."""
    with write_session() as session:
        row = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
        if row is None:
            return
        for mailbox in session.scalars(select(MailUser).where(MailUser.domain == domain_name)).all():
            session.delete(mailbox)
        session.flush()
        session.delete(row)


def delete_mail_domain(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    mail.delete_mail_domain(domain_name)
    _delete_mail_domain_cache(domain_name)
    return {"domain": domain_name, "status": "deleted"}


def create_mailbox(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    password = params["password"]
    quota_mb = int(params.get("quota_mb", 1024))

    with write_session() as session:
        mail_domain = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
        if mail_domain is None:
            raise RuntimeError(f"mail domain '{domain_name}' not provisioned")
        existing = session.scalar(
            select(MailUser).where(MailUser.domain == domain_name, MailUser.local_part == local_part)
        )
        if existing is not None:
            raise RuntimeError(f"mailbox '{local_part}@{domain_name}' already exists")
        mail_domain_id = mail_domain.id

    result = mail.create_mailbox(domain_name, local_part, password, quota_mb=quota_mb)

    with write_session() as session:
        row = MailUser(mail_domain_id=mail_domain_id, local_part=local_part, domain=domain_name, quota_mb=quota_mb)
        session.add(row)

    return result


def delete_mailbox(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    mail.delete_mailbox(domain_name, local_part)
    with write_session() as session:
        row = session.scalar(
            select(MailUser).where(MailUser.domain == domain_name, MailUser.local_part == local_part)
        )
        if row is not None:
            session.delete(row)
    return {"domain": domain_name, "local_part": local_part, "status": "deleted"}


def list_mailboxes(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    return {"domain": domain_name, "mailboxes": mail.list_mailboxes(domain_name)}


def change_mailbox_password(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    new_password = params["password"]
    mail.change_mailbox_password(domain_name, local_part, new_password)
    return {"domain": domain_name, "local_part": local_part, "status": "password_changed"}


def terminate_account_mail(account: Account) -> None:
    """TERMINATE_HOOKS entry: delete every mail domain (and therefore every
    mailbox in it, via ON DELETE CASCADE in forgehost_mail itself) this
    account owns."""
    with write_session() as session:
        domains = session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all()
    for domain_name in domains:
        mail.delete_mail_domain(domain_name)
        _delete_mail_domain_cache(domain_name)
