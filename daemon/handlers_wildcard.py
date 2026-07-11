"""Per-domain wildcard subdomains (missing-features batch, goal feature 3):
*.domain.com routed to the exact same vhost (and therefore docroot) as the
base domain.

Three independent pieces, each already built by an earlier phase and reused
here rather than rebuilt:

- **Routing**: OLS's own wildcard listener-map syntax
  (`templates/httpd_config.conf.j2`, `daemon/ols.py`'s `_wildcard_map`) --
  confirmed against this server's installed OLS docs
  (Listeners_General_Help.html: "'*.mydomain.com' will match all subdomains
  of mydomain.com"). Any request for an undefined subdomain lands on the
  SAME vhost as the base domain, i.e. the same docroot -- no new serving
  logic needed at all.
- **DNS**: one wildcard A record via `daemon/dnsprovider.py` (already
  provider-agnostic across PowerDNS/Cloudflare, `upsert_record(zone, "*",
  "A", [ip])` -- the goal's own literal "PowerDNS/Cloudflare API").
- **SSL**: the existing `ssl.issue_wildcard` op (Phase 7a feature 5,
  `daemon/ssl.py`) -- deliberately NOT auto-triggered here (issuing a
  certificate is its own explicit, potentially rate-limited action; this
  feature only wires routing + DNS, and surfaces whether a wildcard-covering
  cert is already active so the UI can prompt for it separately).

"Validate no conflict with existing explicit subdomains" (the goal's own
requirement): OLS's listener-map lookup is documented as most-specific-match-
first (Listeners_General_Help.html's wildcard-domain description; the
catchall/wildcard entry is only used "when the server cannot find a matching
virtual host"), so an explicit subdomain's own map entry always wins over
the wildcard one -- there is no real routing conflict to prevent. What this
function actually validates is informational: it reports which explicit
subdomains already exist under this domain, so an admin enabling wildcard
routing can see up front that they'll keep working unchanged, rather than
silently discovering it later.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, WildcardDomain
from shared.validation import validate_domain

from daemon import dnsprovider, ols

WILDCARD_RECORD_LABEL = "*"


def _domain_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise RuntimeError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise RuntimeError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def _existing_subdomains(session, domain_name: str) -> list[str]:
    suffix = f".{domain_name}"
    rows = session.scalars(select(Domain.domain).where(Domain.domain.like(f"%{suffix}"))).all()
    return sorted(d for d in rows if d.endswith(suffix) and d != domain_name)


def _to_dict(row: WildcardDomain | None, domain_name: str, zone_managed: bool, existing_subdomains: list[str], ssl_is_wildcard: bool) -> dict:
    return {
        "domain": domain_name,
        "enabled": bool(row and row.enabled),
        "dns_record_created": bool(row and row.dns_record_created),
        "zone_managed": zone_managed,
        "ssl_is_wildcard": ssl_is_wildcard,
        "existing_subdomains": existing_subdomains,
    }


def get_wildcard(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        domain_row, _account = _domain_account(session, domain_name)
        row = session.scalar(select(WildcardDomain).where(WildcardDomain.domain == domain_name))
        existing = _existing_subdomains(session, domain_name)
        zone_managed = dnsprovider.zone_exists(domain_name)
        return _to_dict(row, domain_name, zone_managed, existing, domain_row.ssl_is_wildcard)


def set_wildcard(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    enabled = bool(params.get("enabled", True))

    with write_session() as session:
        domain_row, account = _domain_account(session, domain_name)
        zone_managed = dnsprovider.zone_exists(domain_name)
        if enabled and not zone_managed:
            raise RuntimeError(
                f"domain '{domain_name}' has no Forgehost-managed DNS zone -- a wildcard A record "
                "cannot be created until this domain's own zone is managed here (same precondition "
                "wildcard SSL issuance already requires)"
            )
        existing_subdomains = _existing_subdomains(session, domain_name)

        row = session.scalar(select(WildcardDomain).where(WildcardDomain.domain == domain_name))
        if row is None:
            row = WildcardDomain(domain=domain_name)
            session.add(row)

        account_snapshot = account
        ssl_is_wildcard = domain_row.ssl_is_wildcard

    dns_record_created = row.dns_record_created
    if enabled and zone_managed and not dns_record_created and settings.server_public_ip:
        dnsprovider.upsert_record(domain_name, WILDCARD_RECORD_LABEL, "A", [settings.server_public_ip])
        dns_record_created = True
    elif not enabled and dns_record_created:
        try:
            dnsprovider.delete_record(domain_name, WILDCARD_RECORD_LABEL, "A")
        except dnsprovider.DnsError:
            pass  # best-effort -- the routing change (below) is what actually matters
        dns_record_created = False

    with write_session() as session:
        row = session.scalar(select(WildcardDomain).where(WildcardDomain.domain == domain_name))
        row.enabled = enabled
        row.dns_record_created = dns_record_created
        session.flush()
        result = _to_dict(row, domain_name, zone_managed, existing_subdomains, ssl_is_wildcard)

    ols.refresh_vhost(account_snapshot)
    return result


def delete_wildcard_for_domain(domain_name: str) -> None:
    """Called from handlers_domain.remove_domain -- this project's manual-
    cascade convention. Best-effort DNS cleanup (the domain's own zone is
    likely being torn down around the same time by the caller anyway)."""
    with write_session() as session:
        row = session.scalar(select(WildcardDomain).where(WildcardDomain.domain == domain_name))
        if row is None:
            return
        had_record = row.dns_record_created
        session.delete(row)
    if had_record:
        try:
            dnsprovider.delete_record(domain_name, WILDCARD_RECORD_LABEL, "A")
        except dnsprovider.DnsError:
            pass
