"""Per-domain hotlink protection (Phase 4 feature 2). Rendered into that
domain's own vhost as an OLS/mod_rewrite-compatible RewriteCond/RewriteRule
pair (daemon/ols.py) -- validate before apply, rollback on failure comes for
free from the same ConfigWriterMulti pipeline every other vhost change in
this project already goes through (ols.refresh_vhost), same pattern
daemon/handlers_redirect.py already established for the other per-domain
rewrite-rule feature.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import validate_domain, validate_hotlink_allowed_domains

from daemon import ols


def _domain_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise RuntimeError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise RuntimeError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def _to_dict(domain_row: Domain) -> dict:
    return {
        "domain": domain_row.domain,
        "enabled": domain_row.hotlink_protection_enabled,
        "allowed_domains": domain_row.hotlink_allowed_domains or [],
    }


def get_hotlink_protection(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        domain_row, _account = _domain_account(session, domain_name)
        return _to_dict(domain_row)


def set_hotlink_protection(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    enabled = bool(params.get("enabled", True))
    allowed_domains = validate_hotlink_allowed_domains(params.get("allowed_domains") or [])

    with write_session() as session:
        domain_row, account = _domain_account(session, domain_name)
        domain_row.hotlink_protection_enabled = enabled
        domain_row.hotlink_allowed_domains = allowed_domains
        session.flush()
        result = _to_dict(domain_row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return result
