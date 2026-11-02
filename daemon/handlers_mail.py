"""Mailbox/mail-domain CRUD (Phase e). Webmail itself is explicitly out of
scope (project goal: "link out to Roundcube, don't build webmail") -- these
ops only provision mail routing/storage; `webmail_url` in boron.toml is
what the admin UI links out to.
"""
from __future__ import annotations

import logging
import os
import stat
import threading

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, MailDomain, MailUser
from shared.validation import (
    ConflictError,
    NotFoundError,
    ValidationError,
    validate_domain,
    validate_email_address,
    validate_iso_date,
    validate_mailbox_local_part,
    validate_password_strength,
    validate_spam_threshold,
    validate_username,
)

from daemon import (
    autoresponder, dkim, handlers_email_routing, mail, resource_limits, spamfilter, webmail_sso,
)
from daemon.mail_mutation import serialized

# Bounds for a single mailbox's quota. 100 GB is a generous shared-hosting
# ceiling that still rejects an accidental/absurd value or an overflow, and the
# 1 MB floor rejects 0/negative (which Dovecot would read as "unlimited").
MAILBOX_QUOTA_MIN_MB = 1
MAILBOX_QUOTA_MAX_MB = 100 * 1024
logger = logging.getLogger("borond.mail")


@serialized
def create_mail_domain(params: dict) -> dict:
    username = params.get("username")
    domain_name = validate_domain(params["domain"])
    from daemon import domain_ownership, mail_identity, mail_local_delivery
    reason = domain_ownership.reserved_reason(domain_name, include_infrastructure=False)
    if reason:
        raise ValidationError(reason)
    if domain_name == mail_identity.canonical_hostname():
        # Postfix runs as this name (myhostname), and mail for its own name
        # is delivered to the server's system users, never to mailboxes.
        raise ValidationError(
            f"'{domain_name}' is this server's mail hostname and cannot also be a mail domain"
        )

    with write_session() as session:
        account_id = None
        if username:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise NotFoundError(f"account '{username}' not found")
            account_id = account.id
        else:
            domain = session.scalar(select(Domain).where(Domain.domain == domain_name))
            account = session.get(Account, domain.account_id) if domain else None
            if account is None:
                raise ValidationError(f"mail domain '{domain_name}' requires an owning hosting account")
            account_id = account.id
        account_status = account.status
        hosting_domain = session.scalar(select(Domain).where(Domain.domain == domain_name))
        if hosting_domain is not None and hosting_domain.account_id != account_id:
            raise ValidationError("mail and hosting domain ownership must match")
        if hosting_domain is None:
            # A mail-only domain (admin call or archive restore) never went
            # through domain_ownership.require_available, so apply the same
            # ancestor/descendant rule here.
            domain_ownership.require_no_foreign_overlap(session, domain_name, account_id)
        existing = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
        if existing is not None:
            raise ConflictError(f"mail domain '{domain_name}' already provisioned")

    if mail.domain_exists(domain_name):
        raise ConflictError(f"mail domain '{domain_name}' already exists")

    # Local acceptance starts only once the public MX points here (or the
    # operator trusts the domain), so a claimed external domain never
    # captures mail other senders on this server address to it.
    routing_local = mail_local_delivery.routing_mode(domain_name) == "local"
    accept_locally = routing_local and mail_local_delivery.local_delivery_allowed(domain_name)
    local_delivery = "active" if accept_locally else ("pending_mx" if routing_local else "off")
    # A domain added (or restored) while its account is suspended is on the
    # login deny list before any of its mailboxes exists.
    held = account_status == "suspended" and mail.update_suspended_login_domains(add=[domain_name])
    try:
        mail.create_mail_domain(domain_name, active=accept_locally)
    except Exception:
        _release_login_hold(domain_name, held)
        raise

    try:
        with write_session() as session:
            row = MailDomain(account_id=account_id, domain=domain_name)
            session.add(row)
            session.flush()
            result = {"id": row.id, "domain": row.domain, "account_id": row.account_id,
                      "local_delivery": local_delivery}
    except Exception:
        try:
            mail.delete_mail_domain(domain_name)
        except Exception:
            logger.exception("failed to roll back external mail domain '%s' after cache write failure", domain_name)
        _release_login_hold(domain_name, held)
        raise

    # Phase 3 feature 1: SPF/DKIM/DMARC sane defaults, generated the moment
    # a domain gets real mail routing -- a failure here must not undo the
    # mail domain that was already successfully created above (DNS/keypair
    # generation is a best-effort enhancement, not a precondition of mail
    # working at all: Postfix/Dovecot delivery has zero dependency on these
    # TXT records existing).
    try:
        result["dkim"] = dkim.setup_dns_signing(domain_name)
    except Exception:
        logger.exception(
            "SPF/DKIM/DMARC setup failed for '%s' -- mail domain itself was still created", domain_name
        )
    return result


def _release_login_hold(domain_name: str, held: bool) -> None:
    if held:
        try:
            mail.update_suspended_login_domains(remove=[domain_name])
        except Exception:
            logger.exception("failed to remove '%s' from the suspended-login list", domain_name)


@serialized
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


@serialized
def delete_mail_domain(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    webmail_sso.revoke_domain(domain_name)
    webmail_sso.purge_domain(domain_name)
    mail.delete_mail_domain(domain_name)
    # Keep ownership/cache rows until cleanup succeeds, so a failed delete
    # remains authorized and discoverable for retry (including termination).
    spamfilter.delete_entries_for_domain(domain_name)
    spamfilter.remove_domain_spam_settings(domain_name)
    if domain_name in mail.suspended_login_domains():
        # A later owner of the name must not inherit the suspension.
        mail.update_suspended_login_domains(remove=[domain_name])
    _delete_mail_domain_cache(domain_name)
    try:
        dkim.teardown_dns_signing(domain_name)
    except Exception:
        logger.exception("DKIM teardown failed for '%s'", domain_name)
    return {"domain": domain_name, "status": "deleted"}


_mail_provision_lock = threading.RLock()


@serialized
def ensure_mail_domain(domain_name: str) -> dict | None:
    """Provision mail lazily for an existing active hosting domain.

    Returns create_mail_domain's result (with its DKIM, SPF and DMARC
    outcome) when this call created the mail domain, otherwise None."""
    with _mail_provision_lock:
        with write_session() as session:
            existing = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
            if existing is not None:
                domain = session.scalar(select(Domain).where(Domain.domain == domain_name))
                if domain is not None and domain.account_id != existing.account_id:
                    raise ValidationError("mail and hosting domain ownership must match")
                return None
            domain = session.scalar(select(Domain).where(Domain.domain == domain_name))
            account = session.get(Account, domain.account_id) if domain else None
            if account is None or account.status != 'active':
                raise NotFoundError(f"mail domain '{domain_name}' not provisioned for an active hosting account")
            username, account_id = account.username, account.id
        if mail.domain_exists(domain_name):
            # Missing ownership metadata is not evidence that external mail
            # belongs to the current web owner. Require explicit recovery.
            raise ValidationError("Existing mail domain has no ownership record; administrator recovery required")
        return create_mail_domain({'username': username, 'domain': domain_name})


@serialized
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

    created_domain = ensure_mail_domain(domain_name)

    with write_session() as session:
        mail_domain = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
        if mail_domain is None:
            raise NotFoundError(f"mail domain '{domain_name}' not provisioned")
        existing = session.scalar(
            select(MailUser).where(MailUser.domain == domain_name, MailUser.local_part == local_part)
        )
        if existing is not None:
            raise ConflictError(f"mailbox '{local_part}@{domain_name}' already exists")
        if mail_domain.account_id is not None:
            resource_limits.require_capacity(session, mail_domain.account_id, "mailbox")
        mail_domain_id = mail_domain.id

    result = mail.create_mailbox(domain_name, local_part, password, quota_mb=quota_mb)

    try:
        with write_session() as session:
            row = MailUser(mail_domain_id=mail_domain_id, local_part=local_part, domain=domain_name, quota_mb=quota_mb)
            session.add(row)
    except Exception:
        try:
            mail.delete_mailbox(domain_name, local_part)
        except Exception:
            logger.exception(
                "failed to roll back external mailbox '%s@%s' after cache write failure",
                local_part,
                domain_name,
            )
        raise

    if created_domain is not None:
        # The first mailbox created the mail domain: report its SPF, DKIM
        # and DMARC outcome, as mail.domain.create would have.
        result = {**result, "mail_domain": {"created": True, "dkim": created_domain.get("dkim")}}
    return result


@serialized
def delete_mailbox(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    webmail_sso.revoke_mailbox(f"{local_part}@{domain_name}")
    removed = mail.delete_mailbox(domain_name, local_part)
    spamfilter.delete_entries_for_mailbox(domain_name, local_part)
    with write_session() as session:
        row = session.scalar(
            select(MailUser).where(MailUser.domain == domain_name, MailUser.local_part == local_part)
        )
        cached = row is not None
        if cached:
            session.delete(row)
    # A retry after a partial failure finds only the cache row; a mailbox
    # that exists nowhere was never deleted and must not report success.
    if not removed and not cached:
        raise NotFoundError(f"mailbox '{local_part}@{domain_name}' does not exist")
    return {"domain": domain_name, "local_part": local_part, "status": "deleted"}


def list_mailboxes(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    from daemon import mail_dns
    return {"domain": domain_name, "mailboxes": mail.list_mailboxes(domain_name),
            "client_settings": mail_dns.client_settings()}


@serialized
def set_mailbox_active(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    active = params.get("active")
    if not isinstance(active, bool):
        raise ValidationError("active must be true or false")
    if not active:
        webmail_sso.revoke_mailbox(f"{local_part}@{domain_name}")
    if not mail.set_mailbox_active(domain_name, local_part, active):
        raise ValidationError(f"mailbox '{local_part}@{domain_name}' does not exist")
    return {
        "domain": domain_name, "local_part": local_part,
        "active": active, "status": "active" if active else "suspended",
    }


@serialized
def change_mailbox_password(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    new_password = validate_password_strength(params["password"])
    webmail_sso.revoke_mailbox(f"{local_part}@{domain_name}")
    if not mail.change_mailbox_password(domain_name, local_part, new_password):
        raise NotFoundError(f"mailbox '{local_part}@{domain_name}' does not exist")
    return {"domain": domain_name, "local_part": local_part, "status": "password_changed"}


@serialized
def terminate_account_mail(account: Account) -> None:
    """TERMINATE_HOOKS entry: delete every mail domain (and therefore every
    mailbox in it, via ON DELETE CASCADE in boron_mail itself) this
    account owns."""
    with write_session() as session:
        domains = session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all()
        hosted = session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all()
    for domain_name in domains:
        delete_mail_domain({"domain": domain_name})
    # Per-domain routing outlives a plain mail-domain delete on purpose (the
    # hosting domain keeps its MX choice), but not the account itself.
    handlers_email_routing.delete_routing_for_domains([*domains, *hosted])
    # Launch rows of mail domains deleted before purge_domain existed.
    webmail_sso.purge_account(account.id)


def _account_mail_domains(account_id: int) -> list[str]:
    with write_session() as session:
        return list(session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account_id)).all())


def suspend_account_mail(account: Account) -> None:
    """SUSPEND_HOOKS entry: no mailbox of the account can log in (IMAP, POP3,
    SMTP AUTH, webmail) and open sessions end; incoming mail is still
    accepted and delivered (daemon/mail.py, "Logins of suspended accounts")."""
    domains = _account_mail_domains(account.id)
    if domains:
        mail.update_suspended_login_domains(add=domains)
        # Also when the list already named them (an interrupted earlier
        # suspension, say): sessions opened since must end as well.
        mail.end_login_sessions(domains)


def unsuspend_account_mail(account: Account) -> None:
    """UNSUSPEND_HOOKS entry: the account's mailboxes can log in again."""
    domains = _account_mail_domains(account.id)
    if domains:
        mail.update_suspended_login_domains(remove=domains)


def reconcile_suspended_mail_logins() -> list[str]:
    """Upgrade step: the deny list is exactly the mail domains of suspended
    accounts, including accounts suspended by a release that kept their
    mail logins working."""
    with write_session() as session:
        domains = sorted(session.scalars(
            select(MailDomain.domain).join(Account, Account.id == MailDomain.account_id)
            .where(Account.status == "suspended")
        ).all())
    mail.update_suspended_login_domains(replace=domains)
    return domains


# --- Forwarders (Phase 3 feature 4) -----------------------------------


@serialized
def set_mailbox_quota(params: dict) -> dict:
    domain_name = validate_domain(params['domain'])
    local = validate_mailbox_local_part(params['local_part'])
    try:
        quota = int(params['quota_mb'])
    except (TypeError, ValueError):
        raise ValidationError('Enter a mailbox quota in GB') from None
    if not MAILBOX_QUOTA_MIN_MB <= quota <= MAILBOX_QUOTA_MAX_MB:
        raise ValidationError('Mailbox quota must be between 0.0009765625 and 100 GB')
    with write_session() as session:
        row = session.scalar(select(MailUser).where(MailUser.domain == domain_name, MailUser.local_part == local))
        if row is None:
            raise ValidationError('This mailbox does not exist')
        previous, mailbox_id = row.quota_mb, row.id
    mail.set_mailbox_quota(domain_name, local, quota)
    try:
        with write_session() as session:
            session.get(MailUser, mailbox_id).quota_mb = quota
    except Exception:
        mail.set_mailbox_quota(domain_name, local, previous)
        raise
    return {'domain': domain_name, 'local_part': local, 'quota_mb': quota}


@serialized
def create_forward(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    destination = validate_email_address(params["destination"])
    ensure_mail_domain(domain_name)
    return mail.create_forward(domain_name, local_part, destination)


@serialized
def delete_forward(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    destination = validate_email_address(params["destination"])
    if not mail.delete_forward(domain_name, local_part, destination):
        raise NotFoundError(f"forwarder '{local_part}@{domain_name}' -> '{destination}' does not exist")
    return {"domain": domain_name, "local_part": local_part, "destination": destination, "status": "deleted"}


def list_forwards(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    if not mail.domain_exists(domain_name):
        return {'domain': domain_name, 'forwards': []}
    return {"domain": domain_name, "forwards": mail.list_forwards(domain_name)}


# --- Catch-all (Phase 3 feature 4) ------------------------------------


@serialized
def set_catchall(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    destination = validate_email_address(params["destination"])
    ensure_mail_domain(domain_name)
    return mail.set_catchall(domain_name, destination)


def get_catchall(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    if not mail.domain_exists(domain_name):
        return {'domain': domain_name, 'catchall': None}
    catchall = mail.get_catchall(domain_name)
    return {"domain": domain_name, "catchall": catchall}


@serialized
def delete_catchall(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    mail.delete_catchall(domain_name)
    return {"domain": domain_name, "status": "deleted"}


# --- Autoresponders (Phase 3 feature 4) -------------------------------

MAX_AUTORESPONDER_SUBJECT_LEN = 255
MAX_AUTORESPONDER_BODY_LEN = 10_000


def _autoresponder_file_snapshot(domain_name: str, local_part: str) -> dict:
    path = autoresponder._sieve_path(domain_name, local_part)
    svbin = path.with_suffix(".svbin")
    if not path.exists():
        return {"path": path, "svbin": svbin, "exists": False}
    info = path.stat()
    return {
        "path": path,
        "svbin": svbin,
        "exists": True,
        "content": path.read_bytes(),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def _restore_autoresponder_file(snapshot: dict) -> None:
    path = snapshot["path"]
    snapshot["svbin"].unlink(missing_ok=True)
    if not snapshot["exists"]:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".restore.{os.getpid()}")
    tmp_path.write_bytes(snapshot["content"])
    os.chown(tmp_path, snapshot["uid"], snapshot["gid"])
    os.chmod(tmp_path, snapshot["mode"])
    os.replace(tmp_path, path)


@serialized
def set_autoresponder(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    subject, body = params.get("subject"), params.get("body")
    if not isinstance(subject, str) or not isinstance(body, str):
        raise ValidationError("subject and body must be text")
    subject = subject.strip()
    if not subject or len(subject) > MAX_AUTORESPONDER_SUBJECT_LEN:
        raise ValidationError(f"subject must be 1-{MAX_AUTORESPONDER_SUBJECT_LEN} characters")
    if not body or len(body) > MAX_AUTORESPONDER_BODY_LEN:
        raise ValidationError(f"body must be 1-{MAX_AUTORESPONDER_BODY_LEN} characters")
    start_date = validate_iso_date(params["start_date"]) if params.get("start_date") else None
    end_date = validate_iso_date(params["end_date"]) if params.get("end_date") else None
    if start_date and end_date and end_date < start_date:
        raise ValidationError("end_date must not be before start_date")

    snapshot = _autoresponder_file_snapshot(domain_name, local_part)
    try:
        # Apply the Sieve script (validated via sievec, see daemon/autoresponder.py)
        # BEFORE recording it as this account's current setting -- a script
        # that fails validation must not be reported as successfully set.
        autoresponder.apply_autoresponder(domain_name, local_part, subject, body, start_date, end_date)
        return mail.set_autoresponder(domain_name, local_part, subject, body, start_date, end_date)
    except Exception:
        try:
            _restore_autoresponder_file(snapshot)
        except Exception:
            logger.exception("failed to restore autoresponder Sieve file for '%s@%s'", local_part, domain_name)
        raise


def get_autoresponder(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    current = mail.get_autoresponder(domain_name, local_part)
    return {"domain": domain_name, "local_part": local_part, "autoresponder": current}


@serialized
def delete_autoresponder(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    snapshot = _autoresponder_file_snapshot(domain_name, local_part)
    autoresponder.remove_autoresponder(domain_name, local_part)
    try:
        mail.delete_autoresponder(domain_name, local_part)
    except Exception:
        try:
            _restore_autoresponder_file(snapshot)
        except Exception:
            logger.exception("failed to restore autoresponder Sieve file for '%s@%s'", local_part, domain_name)
        raise
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


@serialized
def set_spam_filter(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    enabled = bool(params.get("enabled", True))
    raw_threshold = params.get("threshold")
    threshold = validate_spam_threshold(raw_threshold) if raw_threshold is not None else None

    with write_session() as session:
        row = _mail_domain_row(session, domain_name)
        old_enabled = row.spam_filter_enabled
        old_threshold = row.spam_filter_threshold

    spamfilter.apply_domain_spam_settings(domain_name, enabled, threshold)
    try:
        with write_session() as session:
            row = _mail_domain_row(session, domain_name)
            row.spam_filter_enabled = enabled
            row.spam_filter_threshold = threshold
            session.flush()
    except Exception:
        try:
            spamfilter.apply_domain_spam_settings(domain_name, old_enabled, old_threshold)
        except Exception:
            logger.exception("failed to restore spam-filter prefs for '%s' after cache write failure", domain_name)
        raise

    return get_spam_filter({"domain": domain_name})
