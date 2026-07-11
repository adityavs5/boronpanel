"""QA round 2, item 9: admin-only PHP `disable_functions` overrides,
per-account or per-domain, layered on top of the system-wide hardened
default (scripts/install.sh writes daemon/phpdirectives.DEFAULT_DISABLE_FUNCTIONS
directly into the real lsphp php.ini files). Deliberately a SEPARATE
module/table/router from the customer-facing php-ini surface
(daemon/handlers_php_ini.py, api/routers/php_ini.py) -- letting a customer
self-service re-enable exec/shell_exec/etc. would defeat the entire point.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, PhpFunctionOverride
from shared.validation import validate_domain, validate_username

from daemon import phpdirectives

# daemon.ols imports this module (for effective_disable_functions, called
# from its own vhost-render path) -- importing it back at module level here
# would be circular. Imported lazily inside the two functions that need it
# instead, the same pattern daemon/pma.py's bootstrap_pma() already uses
# for the identical reason.


def _get_account(session, username: str) -> Account:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    return account


def _row_to_dict(row: PhpFunctionOverride) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "disable_functions": row.disable_functions.split(",") if row.disable_functions else [],
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_overrides(params: dict) -> dict:
    """The default, the account-wide override (if any), and every
    per-domain override for this account -- enough for the admin UI to
    show the whole effective picture in one call."""
    username = validate_username(params["username"])
    with write_session() as session:
        account = _get_account(session, username)
        rows = session.scalars(
            select(PhpFunctionOverride).where(PhpFunctionOverride.account_id == account.id)
        ).all()
        account_wide = next((r for r in rows if r.domain is None), None)
        per_domain = [r for r in rows if r.domain is not None]
        return {
            "default_disable_functions": list(phpdirectives.DEFAULT_DISABLE_FUNCTIONS),
            "account_override": _row_to_dict(account_wide) if account_wide else None,
            "domain_overrides": [_row_to_dict(r) for r in per_domain],
        }


def set_override(params: dict) -> dict:
    """Upsert semantics, not a bare INSERT -- SQL's UNIQUE constraint
    doesn't treat two NULLs as equal (the account-wide scope uses
    domain=NULL), so the "at most one row per (account, scope)" invariant
    for that scope has to be enforced here, not just relied on from the
    DB constraint (which still catches the per-domain, non-NULL case)."""
    username = validate_username(params["username"])
    domain = params.get("domain")
    if domain:
        domain = validate_domain(domain)
    else:
        domain = None
    disable_functions = phpdirectives.validate_disable_functions(params.get("disable_functions") or [])

    with write_session() as session:
        account = _get_account(session, username)
        if domain is not None:
            domain_row = session.scalar(select(Domain).where(Domain.domain == domain, Domain.account_id == account.id))
            if domain_row is None:
                raise RuntimeError(f"domain '{domain}' not found for account '{username}'")

        query = select(PhpFunctionOverride).where(PhpFunctionOverride.account_id == account.id)
        query = query.where(PhpFunctionOverride.domain.is_(None)) if domain is None else query.where(PhpFunctionOverride.domain == domain)
        row = session.scalar(query)
        if row is None:
            row = PhpFunctionOverride(account_id=account.id, domain=domain, disable_functions=disable_functions)
            session.add(row)
        else:
            row.disable_functions = disable_functions
        session.flush()
        result = _row_to_dict(row)
        account_snapshot = account

    from daemon import ols

    ols.refresh_vhost(account_snapshot)
    return result


def delete_override(params: dict) -> dict:
    username = validate_username(params["username"])
    domain = validate_domain(params["domain"]) if params.get("domain") else None

    with write_session() as session:
        account = _get_account(session, username)
        query = select(PhpFunctionOverride).where(PhpFunctionOverride.account_id == account.id)
        query = query.where(PhpFunctionOverride.domain.is_(None)) if domain is None else query.where(PhpFunctionOverride.domain == domain)
        row = session.scalar(query)
        if row is None:
            raise RuntimeError(f"no PHP function override at scope domain={domain!r} for account '{username}'")
        session.delete(row)
        account_snapshot = account

    from daemon import ols

    ols.refresh_vhost(account_snapshot)
    return {"domain": domain, "status": "deleted"}


def effective_disable_functions(session, account_id: int, domain_name: str) -> str | None:
    """Render-time lookup (daemon/ols.py): the per-domain override if one
    exists, else the account-wide override, else None (meaning "render no
    phpIniOverride disable_functions line at all -- the system php.ini's
    own hardened default already applies, nothing to override"). Reuses
    the caller's already-open session (called from inside ols._apply_targets'
    own write_session block, the same pattern every other *_for_domain
    helper in that module already follows)."""
    domain_row = session.scalar(
        select(PhpFunctionOverride).where(
            PhpFunctionOverride.account_id == account_id, PhpFunctionOverride.domain == domain_name
        )
    )
    if domain_row is not None:
        return domain_row.disable_functions
    account_row = session.scalar(
        select(PhpFunctionOverride).where(
            PhpFunctionOverride.account_id == account_id, PhpFunctionOverride.domain.is_(None)
        )
    )
    return account_row.disable_functions if account_row is not None else None
