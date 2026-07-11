"""Phase 8 feature 6: per-domain email routing mode (Local / Remote / Backup).

  - local  : Postfix accepts + delivers locally (mail_domain.active = 1).
  - remote : Postfix STOPS accepting for the domain (mail_domain.active = 0), so
             mail flows to the external MX per DNS -- the goal's Done-When.
  - backup : this server queues+relays for the domain to its primary (external)
             MX. Local acceptance-as-a-mailbox-domain is turned off
             (active = 0) and the domain is written into a Postfix relay_domains
             map. Full backup-MX delivery additionally requires main.cf to
             reference that map (see the checkpoint) -- a one-time structural
             mail config, like the rest of Phase e's hand-applied Postfix setup.

The authoritative acceptance switch is mail_domain.active, which Postfix's
virtual_mailbox_domains SQL map reads live (no reload needed for local/remote).
The mode itself is stored in the SQLite control plane (EmailRouting).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, EmailRouting
from shared.validation import ValidationError, validate_domain, validate_username

from daemon import mail
from daemon.procutil import run

logger = logging.getLogger("borond.email_routing")

MODES = ("local", "remote", "backup")
# local accepts as a local mailbox domain; remote/backup do not (backup
# relays instead of delivering locally).
_ACTIVE_FOR_MODE = {"local": True, "remote": False, "backup": False}


def _owned_domain(session, username: str, domain_name: str) -> Domain:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    domain = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
    if domain is None:
        raise RuntimeError(f"domain '{domain_name}' not found for account '{username}'")
    return domain


def get_routing(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _owned_domain(session, username, domain_name)
        row = session.scalar(select(EmailRouting).where(EmailRouting.domain == domain_name))
        mode = row.mode if row is not None else "local"
    return {"domain": domain_name, "mode": mode}


def set_routing(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    mode = params["mode"]
    if mode not in MODES:
        raise ValidationError(f"mode must be one of {MODES}")

    with write_session() as session:
        _owned_domain(session, username, domain_name)
        row = session.scalar(select(EmailRouting).where(EmailRouting.domain == domain_name))
        if row is None:
            row = EmailRouting(domain=domain_name)
            session.add(row)
        row.mode = mode

    # Toggle Postfix acceptance for the domain (no reload -- Postfix reads the
    # virtual_mailbox_domains SQL map live). Only meaningful if the domain has
    # mail provisioned; otherwise remote/backup are already the effective state.
    mail_domain_affected = False
    try:
        if mail.domain_exists(domain_name):
            mail_domain_affected = mail.set_domain_active(domain_name, _ACTIVE_FOR_MODE[mode])
    except Exception:
        # A MariaDB error here must not lose the recorded mode -- surface it,
        # but the SQLite mode change already committed above.
        logger.exception("failed to toggle mail_domain.active for %s (mode=%s)", domain_name, mode)
        raise

    # Regenerate the relay_domains map from ALL backup-mode domains (idempotent).
    regenerate_relay_domains_map()

    return {"domain": domain_name, "mode": mode, "accepting_locally": mode == "local", "mail_domain_affected": mail_domain_affected}


def regenerate_relay_domains_map() -> list[str]:
    """Rewrite the Postfix relay_domains map from every backup-mode domain,
    postmap it and reload Postfix. Harmless if main.cf doesn't reference the
    map yet (writing an unreferenced map does nothing) -- returns the domains
    written so callers/tests can assert on it."""
    with write_session() as session:
        backup_domains = list(
            session.scalars(select(EmailRouting.domain).where(EmailRouting.mode == "backup")).all()
        )
    path = settings.postfix_relay_domains_map
    body = "".join(f"{d} OK\n" for d in sorted(backup_domains))
    try:
        with open(path, "w") as f:
            f.write(body)
        run(["postmap", path], timeout=20)
        run(["postfix", "reload"], timeout=20)
    except OSError:
        logger.exception("failed writing/reloading relay_domains map at %s", path)
    return sorted(backup_domains)


def delete_routing_for_domain(domain_name: str) -> None:
    """Cleanup on domain removal: drop the EmailRouting row and refresh the
    relay map so a removed backup domain leaves it."""
    with write_session() as session:
        row = session.scalar(select(EmailRouting).where(EmailRouting.domain == domain_name))
        if row is not None:
            session.delete(row)
    regenerate_relay_domains_map()
