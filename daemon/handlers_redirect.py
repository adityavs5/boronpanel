"""Per-domain path redirects (Phase 3 feature 7). Rendered into that
domain's own vhost as an OLS/mod_rewrite-compatible RewriteRule
(daemon/ols.py) -- validate before apply, rollback on failure comes for
free from the same ConfigWriterMulti pipeline every other vhost change
in this project already goes through (ols.refresh_vhost).
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, Redirect
from shared.validation import (
    ConflictError,
    NotFoundError,
    ValidationError,
    validate_domain,
    validate_redirect_path,
    validate_redirect_status_code,
    validate_redirect_target,
)

from daemon import ols


def _row_to_dict(row: Redirect) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "path": row.path,
        "target_url": row.target_url,
        "status_code": row.status_code,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _domain_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise NotFoundError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise RuntimeError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def _set_redirect(params: dict, *, create: bool) -> dict:
    """create_redirect (POST) adds a path that must not exist yet (409
    otherwise, instead of silently overwriting another redirect);
    update_redirect (PUT) changes an existing path (404 when there is none,
    instead of creating one). Keyed by (domain, path)."""
    domain_name = validate_domain(params["domain"])
    path = validate_redirect_path(params["path"])
    target_url = ols.literal_rewrite_target(validate_redirect_target(params["target_url"]))
    status_code = validate_redirect_status_code(params.get("status_code", 301))
    from urllib.parse import urlsplit, unquote
    target = urlsplit(target_url)
    target_path = unquote(target.path or '/').rstrip('/') or '/'
    source_path = unquote(path).rstrip('/') or '/'
    if target.hostname.lower().rstrip('.') == domain_name and target_path == source_path:
        raise ValidationError("The redirect points back to the same domain and path and would create a loop")

    with write_session() as session:
        _domain_row, account = _domain_account(session, domain_name)
        row = session.scalar(select(Redirect).where(Redirect.domain == domain_name, Redirect.path == path))
        if create:
            if row is not None:
                raise ConflictError(f"a redirect for path '{path}' already exists on '{domain_name}'; edit it instead")
            row = Redirect(domain=domain_name, path=path)
            session.add(row)
        elif row is None:
            raise NotFoundError(f"no redirect for path '{path}' on '{domain_name}'")
        row.target_url = target_url
        row.status_code = status_code
        session.flush()
        result = _row_to_dict(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return result


def create_redirect(params: dict) -> dict:
    return _set_redirect(params, create=True)


def update_redirect(params: dict) -> dict:
    return _set_redirect(params, create=False)


def delete_redirect(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    path = validate_redirect_path(params["path"])

    with write_session() as session:
        _domain_row, account = _domain_account(session, domain_name)
        row = session.scalar(select(Redirect).where(Redirect.domain == domain_name, Redirect.path == path))
        if row is None:
            raise NotFoundError(f"no redirect for path '{path}' on '{domain_name}'")
        session.delete(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return {"domain": domain_name, "path": path, "status": "deleted"}


def list_redirects(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        rows = session.scalars(select(Redirect).where(Redirect.domain == domain_name)).all()
        return {"domain": domain_name, "redirects": [_row_to_dict(r) for r in rows]}


def delete_redirects_for_domain(domain_name: str) -> None:
    """Called from handlers_domain.remove_domain -- this project's
    established manual-cascade convention (no DB-level ON DELETE CASCADE
    anywhere in this schema; see shared/models.py's Redirect docstring),
    same pattern handlers_mail.py already uses for MailUser/MailDomain."""
    with write_session() as session:
        rows = session.scalars(select(Redirect).where(Redirect.domain == domain_name)).all()
        for row in rows:
            session.delete(row)


def terminate_account_redirects(account: Account) -> None:
    """TERMINATE_HOOKS entry: an account's PRIMARY domain's Domain row
    (unlike addon/subdomain rows) survives termination -- same
    established pattern as Account itself -- so handlers_domain.
    remove_domain's cleanup above never runs for it. Its redirects are
    otherwise-orphaned config state (the vhost they'd render into is
    already gone, ols.terminate_vhost) with the same "clean up rather
    than let a later reactivate silently reapply stale settings"
    reasoning as handlers_php_ini.terminate_account_php_ini. Idempotent
    -- safe even if the account never had any."""
    with write_session() as session:
        domains = session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all()
        for domain_name in domains:
            rows = session.scalars(select(Redirect).where(Redirect.domain == domain_name)).all()
            for row in rows:
                session.delete(row)
