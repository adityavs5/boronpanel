"""Phase 8 feature 3: parked (alias) domains.

A parked domain serves the SAME docroot + PHP context as an existing target
domain on the account. It's implemented as an ordinary `Domain` row with
kind='parked' whose docroot points at the target's docroot, so it reuses the
whole one-vhost-per-domain + shared-extProcessor machinery (daemon/ols.py) --
which is also why SSL is issuable per parked domain for free (ssl.issue keys on
the Domain row) and why it shares the account's single LSAPI backend. A
`ParkedDomain` bookkeeping row records which target each alias points at.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, ParkedDomain
from shared.validation import validate_domain, validate_username

from daemon import ols, powerdns
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone
from daemon.handlers_domain import ensure_docroot

logger = logging.getLogger("forgehostd.parked")


def _row_to_dict(parked: ParkedDomain, domain: Domain | None) -> dict:
    return {
        "parked_domain": parked.parked_domain,
        "target_domain": parked.target_domain,
        "docroot": domain.docroot if domain else None,
        "ssl_status": domain.ssl_status if domain else "none",
        "created_at": parked.created_at.isoformat() if parked.created_at else None,
    }


def add_parked_domain(params: dict) -> dict:
    username = validate_username(params["username"])
    parked_domain = validate_domain(params["parked_domain"])
    target_domain = params.get("target_domain")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot add a parked domain to an account in status '{account.status}'")

        if session.scalar(select(Domain).where(Domain.domain == parked_domain)) is not None:
            raise RuntimeError(f"domain '{parked_domain}' is already in use")

        # Resolve the target domain (defaults to the account's primary domain).
        if target_domain:
            target_domain = validate_domain(target_domain)
            target = session.scalar(
                select(Domain).where(Domain.domain == target_domain, Domain.account_id == account.id)
            )
        else:
            target = session.scalar(
                select(Domain).where(Domain.account_id == account.id, Domain.kind == "primary")
            )
        if target is None:
            raise RuntimeError("target domain not found for this account (add it first, or set a primary domain)")
        if target.kind == "parked":
            raise RuntimeError("cannot park onto another parked domain")
        target_domain = target.domain
        docroot = target.docroot

        parked_row = Domain(account_id=account.id, domain=parked_domain, kind="parked", docroot=docroot)
        session.add(parked_row)
        session.add(ParkedDomain(account_id=account.id, parked_domain=parked_domain, target_domain=target_domain))
        session.flush()
        account_snapshot = account

    # DNS + vhost, with compensation on failure (same shape as
    # handlers_domain.add_domain -- the Domain row must be committed before the
    # OLS apply, so a failed apply has to delete it back out).
    parent_zone = find_managed_zone(parked_domain)
    dns_label = label_within_zone(parked_domain, parent_zone) if parent_zone else None
    dns_created = False
    try:
        if parent_zone and settings.server_public_ip:
            powerdns.upsert_record(parent_zone, dns_label, "A", [settings.server_public_ip])
            dns_created = True
        ols.provision_vhost(account_snapshot)
    except Exception:
        if dns_created:
            try:
                powerdns.delete_record(parent_zone, dns_label, "A")
            except powerdns.PowerDnsError:
                pass
        with write_session() as session:
            for row in session.scalars(select(Domain).where(Domain.domain == parked_domain)).all():
                session.delete(row)
            for row in session.scalars(select(ParkedDomain).where(ParkedDomain.parked_domain == parked_domain)).all():
                session.delete(row)
        raise

    return {
        "parked_domain": parked_domain,
        "target_domain": target_domain,
        "docroot": docroot,
        "dns_record_created": dns_created,
    }


def list_parked_domains(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        rows = session.scalars(
            select(ParkedDomain).where(ParkedDomain.account_id == account.id).order_by(ParkedDomain.parked_domain)
        ).all()
        result = []
        for parked in rows:
            domain = session.scalar(select(Domain).where(Domain.domain == parked.parked_domain))
            result.append(_row_to_dict(parked, domain))
    return {"parked_domains": result}


def remove_parked_domain(params: dict) -> dict:
    username = validate_username(params["username"])
    parked_domain = validate_domain(params["parked_domain"])

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        parked = session.scalar(
            select(ParkedDomain).where(
                ParkedDomain.parked_domain == parked_domain, ParkedDomain.account_id == account.id
            )
        )
        if parked is None:
            raise RuntimeError(f"parked domain '{parked_domain}' not found for account '{username}'")
        domain_row = session.scalar(
            select(Domain).where(Domain.domain == parked_domain, Domain.account_id == account.id)
        )
        if domain_row is not None:
            session.delete(domain_row)
        session.delete(parked)
        account_snapshot = account

    parent_zone = find_managed_zone(parked_domain)
    if parent_zone:
        try:
            powerdns.delete_record(parent_zone, label_within_zone(parked_domain, parent_zone), "A")
        except powerdns.PowerDnsError:
            pass

    ols.remove_domain_vhost(account_snapshot, parked_domain)
    return {"parked_domain": parked_domain, "status": "removed"}


def terminate_account_parked(account: Account) -> None:
    """TERMINATE_HOOKS entry: drop this account's ParkedDomain bookkeeping rows
    (the parked Domain rows themselves + their vhosts are already torn down by
    the ols.terminate_vhost hook, same as every other domain)."""
    with write_session() as session:
        for row in session.scalars(select(ParkedDomain).where(ParkedDomain.account_id == account.id)).all():
            session.delete(row)
