"""Per-domain maintenance mode (missing-features batch, goal feature 2).

Rendered into that domain's own vhost as an OLS rewrite rule that forces
every request to a 503 response (the documented Apache/OLS mod_rewrite
"maintenance mode" idiom: `RewriteRule ^ - [R=503,L]`, target `-` so it's an
in-place status override, not an actual redirect) plus a vhost-level
`errorpage 503 { url ... }` directive pointing at a small per-domain HTML
page rendered from the admin's own title/message/estimated_time -- same
validate-before-apply, rollback-on-failure pipeline every other vhost change
in this project goes through (ols.refresh_vhost -> ConfigWriterMulti).

Certbot's HTTP-01 webroot path and the DNS-01 challenge are both untouched by
this feature: the ACME path is excluded from the maintenance rewrite rule
(same exclusion the suspended-page rewrite already uses), and DNS-01 doesn't
touch HTTP at all -- so "certbot renewal never blocked during maintenance"
(the goal's explicit requirement) holds structurally, not by coincidence.

Bypass is a token embedded in the URL as a query-string parameter
(`?fh_bypass=<token>`), checked on every request via a RewriteCond negation --
deliberately not a cookie-setting flow (simpler to reason about, test, and
reset by regenerating the token; the goal only asks for "bypass via secret
token in URL", not persistent whole-site browsing while in maintenance).
"""
from __future__ import annotations

import datetime as dt
import html
import logging

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, MaintenanceMode, utcnow
from shared.validation import (
    generate_bypass_token,
    validate_domain,
    validate_maintenance_auto_disable_minutes,
    validate_maintenance_estimated_time,
    validate_maintenance_message,
    validate_maintenance_title,
)

from daemon import custom_pages, ols

BYPASS_QUERY_PARAM = "fh_bypass"
logger = logging.getLogger('borond.maintenance')

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
    background:#0b1220; color:#e6edf3; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
  .card {{ text-align:center; padding:2.5rem; max-width:34rem; }}
  .icon {{ font-size:2.5rem; margin:0 0 0.5rem; }}
  h1 {{ font-size:1.6rem; font-weight:600; margin:0 0 0.75rem; }}
  p {{ color:#8b98a5; line-height:1.6; margin:0 0 0.5rem; }}
  .eta {{ display:inline-block; margin-top:0.75rem; padding:0.35rem 0.9rem; border-radius:999px;
    background:rgba(31,190,214,0.15); color:#1fbed6; font-size:0.9rem; font-weight:600; }}
</style>
</head>
<body>
  <div class="card">
    <p class="icon">&#128736;</p>
    <h1>{title}</h1>
    <p>{message}</p>
    {eta_html}
  </div>
</body>
</html>
"""


def _render_maintenance_html(title: str, message: str, estimated_time: str) -> str:
    """Server-rendered from the admin's own free-text fields -- html.escape,
    not Jinja2 autoescaping, since these strings never pass through the
    Jinja2 Environment daemon/ols.py uses for *config* files (that
    Environment has no autoescape configured at all, deliberately, since it
    renders non-HTML config text everywhere else it's used)."""
    eta_html = f'<span class="eta">Estimated time: {html.escape(estimated_time)}</span>' if estimated_time else ""
    return _PAGE_TEMPLATE.format(title=html.escape(title), message=html.escape(message), eta_html=eta_html)


def _domain_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise RuntimeError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise RuntimeError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def _to_dict(row: MaintenanceMode) -> dict:
    return {
        "domain": row.domain,
        "enabled": row.enabled,
        "title": row.title,
        "message": row.message,
        "estimated_time": row.estimated_time,
        "auto_disable_minutes": row.auto_disable_minutes,
        "auto_disable_at": row.auto_disable_at.isoformat() if row.auto_disable_at else None,
        "bypass_token": row.bypass_token,
        "bypass_query_param": BYPASS_QUERY_PARAM,
    }


def get_maintenance(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _domain_account(session, domain_name)  # 404s if the domain doesn't exist
        row = session.scalar(select(MaintenanceMode).where(MaintenanceMode.domain == domain_name))
        if row is None:
            return {
                "domain": domain_name,
                "enabled": False,
                "title": "We'll be right back",
                "message": "This site is currently undergoing scheduled maintenance. Please check back soon.",
                "estimated_time": "",
                "auto_disable_minutes": None,
                "auto_disable_at": None,
                "bypass_token": None,
                "bypass_query_param": BYPASS_QUERY_PARAM,
            }
        return _to_dict(row)


def set_maintenance(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    enabled = bool(params.get("enabled", True))
    title = validate_maintenance_title(params.get("title") or "We'll be right back")
    message = validate_maintenance_message(
        params.get("message") or "This site is currently undergoing scheduled maintenance. Please check back soon."
    )
    estimated_time = validate_maintenance_estimated_time(params.get("estimated_time") or "")
    auto_disable_minutes = validate_maintenance_auto_disable_minutes(params.get("auto_disable_minutes"))
    regenerate_token = bool(params.get("regenerate_token", False))

    with write_session() as session:
        _domain_row, account = _domain_account(session, domain_name)
        row = session.scalar(select(MaintenanceMode).where(MaintenanceMode.domain == domain_name))
        if row is None:
            row = MaintenanceMode(domain=domain_name, bypass_token=generate_bypass_token())
            session.add(row)
        elif regenerate_token:
            row.bypass_token = generate_bypass_token()

        row.enabled = enabled
        row.title = title
        row.message = message
        row.estimated_time = estimated_time
        row.auto_disable_minutes = auto_disable_minutes
        if enabled and auto_disable_minutes is not None:
            row.auto_disable_at = utcnow() + dt.timedelta(minutes=auto_disable_minutes)
        elif enabled:
            row.auto_disable_at = None  # manual
        # else: leaving auto_disable_at as-is when disabling is harmless --
        # sweep_expired only ever acts on rows where enabled is already True.
        session.flush()
        result = _to_dict(row)
        account_snapshot = account

    if enabled:
        custom_pages.set_maintenance_page(
            account_snapshot.username, domain_name, _render_maintenance_html(title, message, estimated_time)
        )
    ols.refresh_vhost(account_snapshot)
    return result


def quiesce_account(account: Account) -> list[dict]:
    """Pause HTTP writes for a backup while preserving customer maintenance state."""
    with write_session() as session:
        domains=[row.domain for row in session.scalars(select(Domain).where(
            Domain.account_id==account.id,Domain.kind!='alias')).all()]
    changed=[]
    try:
        for domain in domains:
            previous=get_maintenance({'domain':domain})
            if previous['enabled']:continue
            set_maintenance({'domain':domain,'enabled':True,'title':previous['title'],
                'message':'A consistent backup is being captured. Please try again shortly.',
                'estimated_time':'a few minutes','auto_disable_minutes':240})
            changed.append(previous)
        return changed
    except Exception:
        try:restore_quiesced_account(account,changed)
        except Exception:logger.exception('Could not fully roll back backup maintenance state')
        raise


def restore_quiesced_account(account: Account, states: list[dict]) -> None:
    """Restore only maintenance states Boron enabled for a backup."""
    failed=[]
    for previous in states:
        try:
            set_maintenance({'domain':previous['domain'],'enabled':False,'title':previous['title'],
                'message':previous['message'],'estimated_time':previous['estimated_time'],
                'auto_disable_minutes':previous['auto_disable_minutes']})
        except Exception:
            logger.exception('Could not restore maintenance state for %s after backup',previous['domain'])
            failed.append(previous['domain'])
    if failed:raise RuntimeError('Could not restore website availability for: '+', '.join(failed))


def delete_maintenance_for_domain(domain_name: str) -> None:
    """Called from handlers_domain.remove_domain -- this project's
    established manual-cascade convention (no DB-level ON DELETE CASCADE
    anywhere in this schema)."""
    with write_session() as session:
        row = session.scalar(select(MaintenanceMode).where(MaintenanceMode.domain == domain_name))
        if row is not None:
            session.delete(row)


def list_active_maintenance(params: dict) -> dict:
    """Admin overview: every domain currently in maintenance (goal's
    explicit requirement), across every account."""
    with write_session() as session:
        rows = session.scalars(select(MaintenanceMode).where(MaintenanceMode.enabled == True)).all()  # noqa: E712
        domain_names = [r.domain for r in rows]
        accounts_by_domain = {}
        if domain_names:
            domain_rows = session.scalars(select(Domain).where(Domain.domain.in_(domain_names))).all()
            account_ids = {d.account_id for d in domain_rows}
            accounts_by_id = {a.id: a.username for a in session.scalars(select(Account).where(Account.id.in_(account_ids))).all()}
            accounts_by_domain = {d.domain: accounts_by_id.get(d.account_id) for d in domain_rows}
        return {
            "domains": [
                {**_to_dict(r), "username": accounts_by_domain.get(r.domain)}
                for r in rows
            ]
        }


def sweep_expired() -> dict:
    """scripts/maintenance_autodisable.py's entry point (system cron) --
    auto-disables any enabled MaintenanceMode row whose auto_disable_at has
    passed, and refreshes that domain's vhost so the 503 stops immediately
    rather than at next unrelated vhost change. Manual-mode rows
    (auto_disable_at IS NULL) are never touched here."""
    now = utcnow()
    disabled_domains: list[str] = []
    with write_session() as session:
        rows = session.scalars(
            select(MaintenanceMode).where(MaintenanceMode.enabled == True)  # noqa: E712
        ).all()
        accounts_to_refresh: dict[str, Account] = {}
        for row in rows:
            if row.auto_disable_at is None:
                continue
            deadline = row.auto_disable_at
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=dt.timezone.utc)
            if deadline > now:
                continue
            row.enabled = False
            disabled_domains.append(row.domain)
            domain_row = session.scalar(select(Domain).where(Domain.domain == row.domain))
            if domain_row is not None:
                account = session.get(Account, domain_row.account_id)
                if account is not None:
                    accounts_to_refresh[account.username] = account
        session.flush()
        account_snapshots = list(accounts_to_refresh.values())

    for account in account_snapshots:
        ols.refresh_vhost(account)

    return {"disabled": disabled_domains}
