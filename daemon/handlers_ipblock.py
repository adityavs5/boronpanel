"""Per-domain IP/CIDR deny list (Phase 4 feature 3). Rendered into that
domain's own vhost as OLS's native accessControl block (daemon/ols.py) --
validate before apply, rollback on failure comes for free from the same
ConfigWriterMulti pipeline every other vhost change in this project already
goes through (ols.refresh_vhost), same pattern
daemon/handlers_redirect.py/handlers_hotlink.py already established for the
other per-domain vhost-rendered features.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import MAX_IP_BLOCK_ENTRIES, ValidationError, validate_domain, validate_ip_or_cidr

from daemon import ols


def _domain_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise RuntimeError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise RuntimeError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def list_ip_blocks(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        domain_row, _account = _domain_account(session, domain_name)
        return {"domain": domain_name, "blocked": list(domain_row.ip_block_list or [])}


def add_ip_block(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    entry = validate_ip_or_cidr(params["entry"])

    with write_session() as session:
        domain_row, account = _domain_account(session, domain_name)
        current = list(domain_row.ip_block_list or [])
        if entry in current:
            raise ValidationError(f"'{entry}' is already blocked for '{domain_name}'")
        if len(current) >= MAX_IP_BLOCK_ENTRIES:
            raise ValidationError(f"'{domain_name}' already has the maximum of {MAX_IP_BLOCK_ENTRIES} blocked entries")
        current.append(entry)
        domain_row.ip_block_list = current
        session.flush()
        result = {"domain": domain_name, "blocked": current}
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return result


def remove_ip_block(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    entry = validate_ip_or_cidr(params["entry"])

    with write_session() as session:
        domain_row, account = _domain_account(session, domain_name)
        current = list(domain_row.ip_block_list or [])
        if entry not in current:
            raise ValidationError(f"'{entry}' is not currently blocked for '{domain_name}'")
        current.remove(entry)
        domain_row.ip_block_list = current
        session.flush()
        result = {"domain": domain_name, "blocked": current}
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return result
