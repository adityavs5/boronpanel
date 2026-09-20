"""Enforce account plan counts before provisioning scarce hosted resources.

Call from the root handler while its existing cross-process mutation lock is
held. ``None`` means unlimited; a configured positive value is a hard cap.
"""
from __future__ import annotations

from sqlalchemy import func, select

from shared.models import (
    AccountResourceLimits, DatabaseGrant, Domain, FtpAccount, MailDomain,
    MailUser, NodeApp, PythonApp,
)
from shared.validation import ValidationError


def require_capacity(session, account_id: int, resource: str) -> None:
    limits = session.scalar(select(AccountResourceLimits).where(AccountResourceLimits.account_id == account_id))
    if limits is None:
        return
    field = {
        "database": "database_limit",
        "mailbox": "email_account_limit",
        "subdomain": "subdomain_limit",
        "ftp": "ftp_account_limit",
        "app": "app_limit",
    }.get(resource)
    if field is None:
        raise ValueError(f"unknown resource limit: {resource}")
    limit = getattr(limits, field)
    if limit is None:
        return

    if resource == "database":
        count = session.scalar(select(func.count()).select_from(DatabaseGrant).where(DatabaseGrant.account_id == account_id))
    elif resource == "mailbox":
        count = session.scalar(select(func.count()).select_from(MailUser).join(
            MailDomain, MailUser.mail_domain_id == MailDomain.id).where(MailDomain.account_id == account_id))
    elif resource == "subdomain":
        count = session.scalar(select(func.count()).select_from(Domain).where(
            Domain.account_id == account_id, Domain.kind == "subdomain"))
    elif resource == "ftp":
        count = session.scalar(select(func.count()).select_from(FtpAccount).where(FtpAccount.account_id == account_id))
    else:
        count = (session.scalar(select(func.count()).select_from(NodeApp).where(NodeApp.account_id == account_id))
                 + session.scalar(select(func.count()).select_from(PythonApp).where(PythonApp.account_id == account_id)))
    if count >= limit:
        raise ValidationError(f"{resource} limit reached for this account ({count}/{limit})")
