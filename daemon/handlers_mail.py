"""Mailbox/mail-domain CRUD (Phase e). Webmail itself is explicitly out of
scope (project goal: "link out to Roundcube, don't build webmail") -- these
ops only provision mail routing/storage; `webmail_url` in boron.toml is
what the admin UI links out to.
"""
from __future__ import annotations

import threading

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, MailDomain, MailUser
from shared.validation import (
    ValidationError,
    validate_domain,
    validate_email_address,
    validate_iso_date,
    validate_mailbox_local_part,
    validate_password_strength,
    validate_spam_threshold,
    validate_username,
)

from daemon import autoresponder, dkim, mail, spamfilter

# Bounds for a single mailbox's quota. 100 GB is a generous shared-hosting
# ceiling that still rejects an accidental/absurd value or an overflow, and the
# 1 MB floor rejects 0/negative (which Dovecot would read as "unlimited").
MAILBOX_QUOTA_MIN_MB = 1
MAILBOX_QUOTA_MAX_MB = 100 * 1024


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
        result = {"id": row.id, "domain": row.domain, "account_id": row.account_id}

    # Phase 3 feature 1: SPF/DKIM/DMARC sane defaults, generated the moment
    # a domain gets real mail routing -- a failure here must not undo the
    # mail domain that was already successfully created above (DNS/keypair
    # generation is a best-effort enhancement, not a precondition of mail
    # working at all: Postfix/Dovecot delivery has zero dependency on these
    # TXT records existing).
    try:
        result["dkim"] = dkim.setup_dns_signing(domain_name)
    except Exception:
        import logging

        logging.getLogger("borond.mail").exception(
            "SPF/DKIM/DMARC setup failed for '%s' -- mail domain itself was still created", domain_name
        )
    return result


def _delete_mail_domain_cache(domain_name: str) -> None:
    """boron_mail's own schema cascades mail_user on mail_domain delete
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
    try:
        dkim.teardown_dns_signing(domain_name)
    except Exception:
        import logging

        logging.getLogger("borond.mail").exception("DKIM teardown failed for '%s'", domain_name)
    return {"domain": domain_name, "status": "deleted"}


_mail_provision_lock = threading.RLock()


def ensure_mail_domain(domain_name: str) -> None:
    """Provision mail lazily for an existing active hosting domain."""
    with _mail_provision_lock:
        with write_session() as session:
            existing = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
            if existing is not None:
                return
            domain = session.scalar(select(Domain).where(Domain.domain == domain_name))
            account = session.get(Account, domain.account_id) if domain else None
            if account is None or account.status != 'active':
                raise RuntimeError(f"mail domain '{domain_name}' not provisioned for an active hosting account")
            username, account_id = account.username, account.id
        if mail.domain_exists(domain_name):
            # Repair a cache row after an interrupted provisioning operation.
            with write_session() as session:
                session.add(MailDomain(account_id=account_id, domain=domain_name))
        else:
            create_mail_domain({'username': username, 'domain': domain_name})


def create_mailbox(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    password = validate_password_strength(params["password"])
    # Bound the quota like every other numeric resource limit in this codebase:
    # an unbounded int(...) accepts 0/negative (a broken or effectively-unlimited
    # mailbox) or an absurd value with no ceiling on shared storage.
    try:
        quota_mb = int(params.get("quota_mb", 1024))
    except (TypeError, ValueError):
        raise ValidationError("quota_mb must be an integer") from None
    if not (MAILBOX_QUOTA_MIN_MB <= quota_mb <= MAILBOX_QUOTA_MAX_MB):
        raise ValidationError(f"quota_mb must be between {MAILBOX_QUOTA_MIN_MB} and {MAILBOX_QUOTA_MAX_MB}")

    ensure_mail_domain(domain_name)

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
    # Missing-features batch, goal feature 5: drop any per-mailbox spam
    # filter entries (regenerates the global Sieve script if any existed).
    from daemon import spamfilter

    spamfilter.delete_entries_for_mailbox(domain_name, local_part)
    return {"domain": domain_name, "local_part": local_part, "status": "deleted"}


def list_mailboxes(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    return {"domain": domain_name, "mailboxes": mail.list_mailboxes(domain_name)}


def change_mailbox_password(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    new_password = validate_password_strength(params["password"])
    mail.change_mailbox_password(domain_name, local_part, new_password)
    return {"domain": domain_name, "local_part": local_part, "status": "password_changed"}


def terminate_account_mail(account: Account) -> None:
    """TERMINATE_HOOKS entry: delete every mail domain (and therefore every
    mailbox in it, via ON DELETE CASCADE in boron_mail itself) this
    account owns."""
    with write_session() as session:
        domains = session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all()
    for domain_name in domains:
        mail.delete_mail_domain(domain_name)
        _delete_mail_domain_cache(domain_name)
        spamfilter.remove_domain_spam_settings(domain_name)
        try:
            dkim.teardown_dns_signing(domain_name)
        except Exception:
            import logging

            logging.getLogger("borond.mail").exception("DKIM teardown failed for '%s'", domain_name)


# --- Forwarders (Phase 3 feature 4) -----------------------------------


def create_forward(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    destination = validate_email_address(params["destination"])
    return mail.create_forward(domain_name, local_part, destination)


def delete_forward(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    destination = validate_email_address(params["destination"])
    mail.delete_forward(domain_name, local_part, destination)
    return {"domain": domain_name, "local_part": local_part, "destination": destination, "status": "deleted"}


def list_forwards(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    return {"domain": domain_name, "forwards": mail.list_forwards(domain_name)}


# --- Catch-all (Phase 3 feature 4) ------------------------------------


def set_catchall(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    destination = validate_email_address(params["destination"])
    return mail.set_catchall(domain_name, destination)


def get_catchall(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    catchall = mail.get_catchall(domain_name)
    return {"domain": domain_name, "catchall": catchall}


def delete_catchall(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    mail.delete_catchall(domain_name)
    return {"domain": domain_name, "status": "deleted"}


# --- Autoresponders (Phase 3 feature 4) -------------------------------

MAX_AUTORESPONDER_SUBJECT_LEN = 255
MAX_AUTORESPONDER_BODY_LEN = 10_000


def set_autoresponder(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    subject = params["subject"].strip()
    body = params["body"]
    if not subject or len(subject) > MAX_AUTORESPONDER_SUBJECT_LEN:
        raise ValueError(f"subject must be 1-{MAX_AUTORESPONDER_SUBJECT_LEN} characters")
    if not body or len(body) > MAX_AUTORESPONDER_BODY_LEN:
        raise ValueError(f"body must be 1-{MAX_AUTORESPONDER_BODY_LEN} characters")
    start_date = validate_iso_date(params["start_date"]) if params.get("start_date") else None
    end_date = validate_iso_date(params["end_date"]) if params.get("end_date") else None
    if start_date and end_date and end_date < start_date:
        raise ValueError("end_date must not be before start_date")

    # Apply the Sieve script (validated via sievec, see daemon/autoresponder.py)
    # BEFORE recording it as this account's current setting -- a script
    # that fails validation must not be reported as successfully set.
    autoresponder.apply_autoresponder(domain_name, local_part, subject, body, start_date, end_date)
    return mail.set_autoresponder(domain_name, local_part, subject, body, start_date, end_date)


def get_autoresponder(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    current = mail.get_autoresponder(domain_name, local_part)
    return {"domain": domain_name, "local_part": local_part, "autoresponder": current}


def delete_autoresponder(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    autoresponder.remove_autoresponder(domain_name, local_part)
    mail.delete_autoresponder(domain_name, local_part)
    return {"domain": domain_name, "local_part": local_part, "status": "deleted"}


# --- SpamAssassin spam filter (Phase 4 feature 1) --------------------------
#
# Per mail-domain, not per-mailbox (matches this file's existing catchall/
# forwarder/autoresponder API shape). enabled/threshold are stored in the
# SQLite control-plane cache (MailDomain) -- not MariaDB's boron_mail
# schema like catchall/forwarders, since nothing outside Boron's own
# code (no Postfix/Dovecot SQL lookup) ever needs to query these values;
# the actual enforcement is daemon/spamfilter.py's per-domain
# virtual-config-dir prefs file, kept in sync with this row on every
# write.


def _mail_domain_row(session, domain_name: str) -> MailDomain:
    row = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
    if row is None:
        raise ValidationError(f"mail domain '{domain_name}' is not provisioned")
    return row


def get_spam_filter(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        row = _mail_domain_row(session, domain_name)
        enabled = row.spam_filter_enabled
        threshold = row.spam_filter_threshold
    return {
        "domain": domain_name,
        "enabled": enabled,
        "threshold": threshold,
        "effective_threshold": threshold if threshold is not None else spamfilter.get_global_default_threshold(),
    }


def set_spam_filter(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    enabled = bool(params.get("enabled", True))
    raw_threshold = params.get("threshold")
    threshold = validate_spam_threshold(raw_threshold) if raw_threshold is not None else None

    with write_session() as session:
        row = _mail_domain_row(session, domain_name)
        row.spam_filter_enabled = enabled
        row.spam_filter_threshold = threshold
        session.flush()

    # Apply to disk AFTER the DB commit succeeds, mirroring this file's own
    # autoresponder ordering rationale (mail.set_autoresponder is called
    # only after autoresponder.apply_autoresponder succeeds) -- inverted
    # here on purpose: a prefs-file write failing after the DB already
    # says "enabled" would leave a domain silently unprotected rather than
    # silently un-set, and a filesystem write is far less likely to fail
    # than a validated DB update, so recording intent first is the safer
    # order for this specific feature.
    spamfilter.apply_domain_spam_settings(domain_name, enabled, threshold)

    return get_spam_filter({"domain": domain_name})
