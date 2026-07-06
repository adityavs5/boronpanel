"""Phase 8 feature 4: whole-domain forwarding (301/302 redirect).

Redirects an ENTIRE domain to an external URL via an OLS vhost rewrite rule
(daemon/ols.py renders it from the DomainForwarding row). Distinct from the
per-path `Redirect` feature (Phase 3 f7): this replaces the whole site's
serving with a redirect. `keep_path` controls whether the original request URI
is appended to the target (cPanel's "redirect with/without path"). The
`.well-known/acme-challenge/` path is always excluded from the redirect so SSL
can still be issued/renewed for a forwarded domain.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, DomainForwarding
from shared.validation import (
    validate_domain,
    validate_redirect_status_code,
    validate_redirect_target,
    validate_username,
)

from daemon import ols


def _row_to_dict(row: DomainForwarding | None) -> dict | None:
    if row is None:
        return None
    return {
        "domain": row.domain,
        "target_url": row.target_url,
        "status_code": row.status_code,
        "keep_path": row.keep_path,
    }


def _owned_domain(session, username: str, domain_name: str) -> Domain:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    domain = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
    if domain is None:
        raise RuntimeError(f"domain '{domain_name}' not found for account '{username}'")
    return domain


def set_forwarding(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    # Normalize the target: strip a trailing slash so keep_path's "<target>/<uri>"
    # never produces a double slash.
    target_url = validate_redirect_target(params["target_url"]).rstrip("/")
    status_code = validate_redirect_status_code(params.get("status_code", 301))
    keep_path = bool(params.get("keep_path", True))

    with write_session() as session:
        domain = _owned_domain(session, username, domain_name)
        if domain.kind == "parked":
            raise RuntimeError("cannot forward a parked domain -- remove the park first")
        account = session.scalar(select(Account).where(Account.id == domain.account_id))
        row = session.scalar(select(DomainForwarding).where(DomainForwarding.domain == domain_name))
        if row is None:
            row = DomainForwarding(domain=domain_name)
            session.add(row)
        row.target_url = target_url
        row.status_code = status_code
        row.keep_path = keep_path
        session.flush()
        result = _row_to_dict(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return result


def get_forwarding(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _owned_domain(session, username, domain_name)
        row = session.scalar(select(DomainForwarding).where(DomainForwarding.domain == domain_name))
        return {"forwarding": _row_to_dict(row)}


def delete_forwarding(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        domain = _owned_domain(session, username, domain_name)
        account = session.scalar(select(Account).where(Account.id == domain.account_id))
        row = session.scalar(select(DomainForwarding).where(DomainForwarding.domain == domain_name))
        if row is not None:
            session.delete(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return {"domain": domain_name, "status": "removed"}


def delete_forwarding_for_domain(domain_name: str) -> None:
    """Cleanup helper (mirrors handlers_redirect.delete_redirects_for_domain) --
    called when a domain is removed so no orphan forwarding row survives."""
    with write_session() as session:
        row = session.scalar(select(DomainForwarding).where(DomainForwarding.domain == domain_name))
        if row is not None:
            session.delete(row)
