"""Phase 8 feature 3: parked (alias) domains.

A parked domain serves the SAME docroot + PHP context as an existing target
domain on the account. It's implemented as an ordinary `Domain` row with
kind='parked' whose docroot points at the target's docroot, so it reuses the
whole one-vhost-per-domain + shared-extProcessor machinery (daemon/ols.py) --
which is also why SSL is issuable per parked domain for free (ssl.issue keys on
the Domain row) and why it shares the account's single LSAPI backend. A
`ParkedDomain` bookkeeping row records which target each alias points at.

DNS, removal and settings cleanup follow handlers_domain (a parked name is a
hostname like any addon domain): an address record in a managed parent zone,
otherwise a zone of its own; removal under the DNS lock through
remove_domain_dns and purge_domain_settings.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, ParkedDomain
from shared.validation import ConflictError, NotFoundError, ValidationError, validate_domain, validate_username

from daemon import account_mutation, database_operations, dns_operations, dnsprovider, handlers_domain, ols
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone

logger = logging.getLogger("borond.parked")


def _row_to_dict(parked: ParkedDomain, domain: Domain | None, target: Domain | None = None) -> dict:
    return {
        "parked_domain": parked.parked_domain,
        "target_domain": parked.target_domain,
        "docroot": domain.docroot if domain else None,
        "ssl_status": domain.ssl_status if domain else "none",
        "effective_suspended": bool((domain and domain.suspended) or (target and target.suspended)),
        "suspension_reason": ((domain.suspension_reason if domain and domain.suspended else None) or
                              (target.suspension_reason if target and target.suspended else None)),
        "created_at": parked.created_at.isoformat() if parked.created_at else None,
    }


@database_operations.serialized
@account_mutation.locked
def add_parked_domain(params: dict) -> dict:
    username = validate_username(params["username"])
    parked_domain = validate_domain(params["parked_domain"])
    target_domain = params.get("target_domain")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise ConflictError(f"cannot add a parked domain to an account in status '{account.status}'")
        existing = session.scalar(select(Domain).where(Domain.domain == parked_domain))
        if existing is not None:
            raise handlers_domain.domain_in_use_error(parked_domain, existing.account_id, account.id)
        from daemon import domain_ownership
        domain_ownership.require_available(session, parked_domain, account.id)

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
            raise NotFoundError("target domain not found for this account (add it first, or set a primary domain)")
        if target.kind == "parked":
            raise ValidationError("cannot park onto another parked domain")
        target_domain = target.domain
        docroot = target.docroot

        parked_row = Domain(account_id=account.id, domain=parked_domain, kind="parked", docroot=docroot)
        session.add(parked_row)
        session.add(ParkedDomain(account_id=account.id, parked_domain=parked_domain, target_domain=target_domain))
        session.flush()
        account_snapshot = account

    # The name had no Domain row: settings still keyed by it are leftovers.
    handlers_domain.purge_domain_settings(parked_domain)
    # DNS + vhost, with compensation on failure (same shape as
    # handlers_domain.add_domain -- the Domain row must be committed before the
    # zone is created and the OLS apply runs, so a failure has to delete it).
    parent_zone = None
    dns_label = None
    record_type = None
    dns_record_created = False
    dns_zone_created = False
    try:
        parent_zone = find_managed_zone(parked_domain)
        dns_label = label_within_zone(parked_domain, parent_zone) if parent_zone else None
        from daemon import ipmanager

        site_ip = ipmanager.address_for_account(account_snapshot.id)
        if parent_zone and site_ip:
            record_type = "AAAA" if ":" in site_ip else "A"
            dnsprovider.upsert_record(parent_zone, dns_label, record_type, [site_ip])
            dns_record_created = True
        elif parent_zone is None:
            # Like an addon domain: a name outside every managed zone gets
            # an authoritative zone of its own (apex and www records).
            handlers_domain._create_managed_zone(username, parked_domain)
            dns_zone_created = True
        ols.provision_vhost(account_snapshot)
    except Exception:
        if dns_zone_created:
            try:
                handlers_domain._delete_managed_zone(parked_domain)
            except Exception:  # noqa: BLE001 - the row compensation below stays authoritative
                logger.exception("could not roll back the DNS zone of parked domain '%s'", parked_domain)
        elif dns_record_created:
            try:
                dnsprovider.delete_record(parent_zone, dns_label, record_type)
            except dnsprovider.DnsError:
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
        "dns_record_created": dns_record_created,
        "dns_zone_created": dns_zone_created,
    }


def list_parked_domains(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        rows = session.scalars(
            select(ParkedDomain).where(ParkedDomain.account_id == account.id).order_by(ParkedDomain.parked_domain)
        ).all()
        result = []
        for parked in rows:
            domain = session.scalar(select(Domain).where(Domain.domain == parked.parked_domain))
            target = session.scalar(select(Domain).where(
                Domain.domain == parked.target_domain, Domain.account_id == account.id))
            result.append(_row_to_dict(parked, domain, target))
    return {"parked_domains": result}


@account_mutation.locked
def remove_parked_domain(params: dict) -> dict:
    """Same contract as handlers_domain.remove_domain: the DNS lock is taken
    before anything changes, a rejected OLS apply restores the rows, and
    cleanup failures after the vhost is gone are reported in
    ``cleanup_failed``."""
    username = validate_username(params["username"])
    parked_domain = validate_domain(params["parked_domain"])
    with dns_operations.mutation_lock():
        return _remove_parked_locked(username, parked_domain)


def _remove_parked_locked(username: str, parked_domain: str) -> dict:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        parked = session.scalar(
            select(ParkedDomain).where(
                ParkedDomain.parked_domain == parked_domain, ParkedDomain.account_id == account.id
            )
        )
        if parked is None:
            raise NotFoundError(f"parked domain '{parked_domain}' not found for account '{username}'")
        domain_row = session.scalar(
            select(Domain).where(Domain.domain == parked_domain, Domain.account_id == account.id)
        )
        handlers_domain.require_no_dependents(session, account.id, parked_domain)
        snapshots = [(ParkedDomain, {column.name: getattr(parked, column.name) for column in ParkedDomain.__table__.columns})]
        if domain_row is not None:
            snapshots.append((Domain, {column.name: getattr(domain_row, column.name)
                                       for column in Domain.__table__.columns}))
            session.delete(domain_row)
        session.delete(parked)
        account_snapshot = account

    # OLS renders the remaining vhosts from the committed rows; restore them
    # if it rejects the result, so a retry still finds the parked domain.
    try:
        ols.remove_domain_vhost(account_snapshot, parked_domain)
    except Exception:
        with write_session() as session:
            for model, values in snapshots:
                if session.get(model, values["id"]) is None:
                    session.add(model(**values))
        raise

    failed = handlers_domain.remove_domain_dns(account_snapshot.id, parked_domain)
    failed += handlers_domain.purge_domain_settings(parked_domain, refresh_waf=True)
    result = {"parked_domain": parked_domain, "status": "removed"}
    if failed:
        result["cleanup_failed"] = failed
    return result


def terminate_account_parked(account: Account) -> None:
    """TERMINATE_HOOKS entry: drop this account's ParkedDomain bookkeeping rows
    (the parked Domain rows themselves + their vhosts are already torn down by
    the ols.terminate_vhost hook, same as every other domain)."""
    with write_session() as session:
        for row in session.scalars(select(ParkedDomain).where(ParkedDomain.account_id == account.id)).all():
            session.delete(row)
