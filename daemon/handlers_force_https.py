"""Per-domain "Force HTTPS": plain-HTTP requests get a permanent (301)
redirect to the same URL over HTTPS.

It can only be switched on while the domain's own installed certificate is
valid and covers the domain: a redirect to a name without a certificate
turns a working http:// site into a browser warning. The redirect lives in
the domain's vhost (daemon/ols.py _force_https_context, rendered by
templates/vhost.conf.j2), which checks the certificate again on every
render, so a certificate that is later removed or expires stops the
redirect at the next vhost refresh instead of breaking the site. Names the
certificate does not cover (www.<domain>, wildcard aliases) stay on HTTP.
Domain forwarding, maintenance mode and suspension take precedence over it
in the vhost, so the status explains when it has no effect.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, DomainForwarding, MaintenanceMode
from shared.validation import NotFoundError, ValidationError, validate_domain, validate_username

from daemon import account_mutation, audit, ols

logger = logging.getLogger("borond.force_https")


def _domain_and_account(session, username: str, domain_name: str) -> tuple[Domain, Account]:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise NotFoundError(f"account '{username}' not found")
    row = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
    if row is None:
        raise NotFoundError(f"domain '{domain_name}' not found for account '{username}'")
    return row, account


def _not_in_effect(session, row: Domain, account: Account, hosts: dict) -> str | None:
    """Why an enabled Force HTTPS does not redirect right now, or None."""
    if not hosts["covered_hosts"]:
        return (f"No valid SSL certificate for {row.domain} is installed, so the site is served over "
                "HTTP without a redirect until a certificate is issued.")
    if session.scalar(select(DomainForwarding.id).where(DomainForwarding.domain == row.domain)) is not None:
        return "Domain forwarding redirects every request for this domain, so Force HTTPS has no effect."
    if session.scalar(select(MaintenanceMode.id).where(
            MaintenanceMode.domain == row.domain, MaintenanceMode.enabled.is_(True))) is not None:
        return "Maintenance mode is on. Force HTTPS applies again when maintenance mode is switched off."
    if row.suspended or account.status == "suspended":
        return "The site is suspended, so it serves the suspended page instead."
    return None


def _status(session, row: Domain, account: Account) -> dict:
    status = ols.force_https_status(session, row)
    hosts = {"covered_hosts": status["redirected_hosts"], "uncovered_hosts": status["unredirected_hosts"]}
    reason = _not_in_effect(session, row, account, hosts) if row.force_https else None
    return {
        "domain": row.domain,
        "enabled": bool(row.force_https),
        "active": bool(row.force_https) and reason is None,
        "certificate_valid": status["certificate_valid"],
        # Names this site answers for that the certificate covers (they are
        # redirected while Force HTTPS is active) and those it does not.
        **hosts,
        "reason": reason,
    }


def get_force_https(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        row, account = _domain_and_account(session, username, domain_name)
        return _status(session, row, account)


@account_mutation.locked
def set_force_https(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    enabled = params.get("enabled")
    if not isinstance(enabled, bool):
        raise ValidationError("enabled must be true or false")
    with write_session() as session:
        row, account = _domain_and_account(session, username, domain_name)
        previous = bool(row.force_https)
        if enabled and not previous and domain_name not in ols.force_https_status(session, row)["redirected_hosts"]:
            raise ValidationError(
                f"Force HTTPS needs a valid SSL certificate for {domain_name}. "
                "Issue the certificate first, then turn Force HTTPS on."
            )
        row.force_https = enabled
        account_snapshot = account
    audit.change("force_https", previous, enabled)
    if enabled != previous:
        try:
            ols.refresh_vhost(account_snapshot)
        except Exception:
            with write_session() as session:
                row = session.scalar(select(Domain).where(Domain.domain == domain_name))
                if row is not None:
                    row.force_https = previous
            try:
                ols.refresh_vhost(account_snapshot)
            except Exception:
                # The original failure is the one to report; the stored value
                # is already back, so the next refresh renders it.
                logger.exception("could not re-render %s after restoring Force HTTPS", domain_name)
            raise
    with write_session() as session:
        row, account = _domain_and_account(session, username, domain_name)
        return _status(session, row, account)
