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

DNS ownership: the zone, not the stored flag, is the source of truth. The
`dns_record_created` flag only records that this feature wrote the `*`
record. Every enable/disable re-reads the zone: a missing record is
recreated, and a `*` record the user wrote themselves (or changed after the
panel wrote it) is never overwritten or deleted -- it is reported as
`dns_conflict` instead.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, WildcardDomain
from shared.validation import ConflictError, NotFoundError, validate_domain

from daemon import dns_operations, dnsprovider, ols

WILDCARD_RECORD_LABEL = "*"


def _domain_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise NotFoundError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise NotFoundError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def _existing_subdomains(session, domain_name: str) -> list[str]:
    suffix = f".{domain_name}"
    rows = session.scalars(select(Domain.domain).where(Domain.domain.like(f"%{suffix}"))).all()
    return sorted(d for d in rows if d.endswith(suffix) and d != domain_name)


def _wildcard_type(ip: str) -> str:
    return "AAAA" if ":" in ip else "A"


def _server_ip() -> str | None:
    """The server address in the canonical form providers return it in, so
    an expanded IPv6 setting ('2001:0db8::1') still matches our own record."""
    import ipaddress

    raw = settings.server_public_ip
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(str(raw).strip()))
    except ValueError:
        return str(raw).strip()


def _wildcard_rrsets(domain_name: str) -> dict[str, list[str]]:
    """The zone's own `*` rrsets that decide what wildcard names resolve to."""
    name = f"{WILDCARD_RECORD_LABEL}.{domain_name}"
    result = {}
    for row in dnsprovider.list_records(domain_name):
        if str(row.get("name", "")).rstrip(".").lower() == name and row.get("type") in ("A", "AAAA", "CNAME"):
            result[row["type"]] = [str(v).lower() for v in row.get("values", [])]
    return result


def _dns_state(domain_name: str, flag: bool, ip: str | None) -> tuple[bool, str | None]:
    """(panel record present, conflict message) read from the live zone."""
    rrsets = _wildcard_rrsets(domain_name)
    if not rrsets:
        return False, None
    rtype = _wildcard_type(ip) if ip else None
    if ip and list(rrsets) == [rtype] and rrsets[rtype] == [ip.lower()]:
        return flag, None
    shown = "; ".join(f"{kind} {', '.join(values)}" for kind, values in sorted(rrsets.items()))
    return False, (
        f"*.{domain_name} already has its own DNS record ({shown}); it was kept. "
        "Remove or change it on the DNS page if wildcard names should reach this server."
    )


def _to_dict(row: WildcardDomain | None, domain_name: str, zone_managed: bool, existing_subdomains: list[str],
             ssl_is_wildcard: bool, dns_record_created: bool, dns_conflict: str | None) -> dict:
    return {
        "domain": domain_name,
        "enabled": bool(row and row.enabled),
        "dns_record_created": dns_record_created,
        "dns_conflict": dns_conflict,
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
        ssl_is_wildcard = domain_row.ssl_is_wildcard
        flag = bool(row and row.dns_record_created)
        if row is not None:
            session.expunge(row)
    zone_managed = dnsprovider.zone_exists(domain_name)
    created, conflict = False, None
    if zone_managed and row is not None and row.enabled:
        created, conflict = _dns_state(domain_name, flag, _server_ip())
    return _to_dict(row, domain_name, zone_managed, existing, ssl_is_wildcard, created, conflict)


def _reconcile_dns(domain_name: str, enabled: bool, flag: bool) -> tuple[bool, str | None]:
    """Make the zone match the toggle without touching a user's own record.
    Returns (panel record now present, conflict message)."""
    ip = _server_ip()
    with dns_operations.mutation_lock():
        present, conflict = _dns_state(domain_name, True, ip)
        if enabled:
            if conflict or not ip:
                return False, conflict
            if not present:
                dnsprovider.upsert_record(domain_name, WILDCARD_RECORD_LABEL, _wildcard_type(ip), [ip])
                return True, None
            # A record identical to ours: ours if this feature wrote it,
            # otherwise the user's, which disabling must then leave alone.
            return flag, None
        if flag and present:
            try:
                dnsprovider.delete_record(domain_name, WILDCARD_RECORD_LABEL, _wildcard_type(ip))
            except dnsprovider.DnsError:
                pass  # best-effort -- the routing change (below) is what actually matters
        return False, None


def set_wildcard(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    enabled = bool(params.get("enabled", True))

    with write_session() as session:
        domain_row, account = _domain_account(session, domain_name)
        zone_managed = dnsprovider.zone_exists(domain_name)
        if enabled and not zone_managed:
            raise ConflictError(
                f"domain '{domain_name}' has no Boron-managed DNS zone -- a wildcard A record "
                "cannot be created until this domain's own zone is managed here (same precondition "
                "wildcard SSL issuance already requires)"
            )
        existing_subdomains = _existing_subdomains(session, domain_name)

        row = session.scalar(select(WildcardDomain).where(WildcardDomain.domain == domain_name))
        if row is None:
            row = WildcardDomain(domain=domain_name)
            session.add(row)
        flag = bool(row.dns_record_created)

        account_snapshot = account
        ssl_is_wildcard = domain_row.ssl_is_wildcard

    dns_record_created, dns_conflict = False, None
    if zone_managed:
        dns_record_created, dns_conflict = _reconcile_dns(domain_name, enabled, flag)

    with write_session() as session:
        row = session.scalar(select(WildcardDomain).where(WildcardDomain.domain == domain_name))
        row.enabled = enabled
        row.dns_record_created = dns_record_created
        session.flush()
        result = _to_dict(row, domain_name, zone_managed, existing_subdomains, ssl_is_wildcard,
                          dns_record_created, dns_conflict)

    ols.refresh_vhost(account_snapshot)
    return result


def delete_wildcard_for_domain(domain_name: str) -> None:
    """Called from handlers_domain.remove_domain -- this project's manual-
    cascade convention. Best-effort DNS cleanup (the domain's own zone is
    likely being torn down around the same time by the caller anyway).
    Only the panel's own, unchanged `*` record is removed."""
    with write_session() as session:
        row = session.scalar(select(WildcardDomain).where(WildcardDomain.domain == domain_name))
        if row is None:
            return
        had_record = row.dns_record_created
        session.delete(row)
    if had_record:
        try:
            _reconcile_dns(domain_name, False, True)
        except dnsprovider.DnsError:
            pass  # zone already gone; nothing left to own
