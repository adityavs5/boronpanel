"""Custom nameserver + glue record management (Phase 4 feature 11).

Uses the same PowerDNS REST API every other DNS feature in this project
already goes through -- no new DB table, since PowerDNS itself is already
the source of truth for zone/record content
(daemon/handlers_dns.py's own docstring). The NS rrset at the zone apex
and any glue A/AAAA records this writes are just more PowerDNS records,
the same primitive upsert_record()/list_records() already provide.

This module's own job is the validation layer the general DNS editor
deliberately doesn't have -- both record editors refuse the zone-apex NS
rrset (handlers_dns.validate_rrset / APEX_NS_MESSAGE) and send the user
here, since NS at the zone apex is normally auto-managed
(daemon/handlers_dns.py's create_zone seeds the default delegation from
default_delegation() below). The one thing that actually needs new
logic here: detecting when a chosen NS hostname is itself a subdomain of
(or equal to) the zone being delegated -- a circular-resolution problem
(you can't look up ns1.example.com's own address without first being able
to resolve *something* in example.com) -- and requiring a glue A/AAAA
record for exactly those, matching the goal's own "glue form if NS is
subdomain of managed domain" instruction.
"""
from __future__ import annotations

import ipaddress
import logging

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import DnsZone, Domain
from shared.validation import ValidationError, validate_domain

from daemon import dns_operations, dnsprovider, dnssetup, powerdns
from daemon.dns_zone_lookup import find_managed_zone

logger = logging.getLogger("borond.nameservers")
# Rows one cross-zone reference search may return. A full page cannot prove
# that no other zone uses a host, so its address is then kept.
REFERENCE_SEARCH_LIMIT = 1000


class NameserverError(ValidationError):
    pass


def _require_local_zone(domain: str, action: str) -> None:
    """Custom NS / glue management only applies to local (PowerDNS) zones:
    a Cloudflare zone's nameservers ARE the CF-assigned pair -- delegating
    anywhere else simply takes the zone off Cloudflare (plan SS1.8)."""
    if dnsprovider.cloudflare_zone_row(domain) is not None:
        raise NameserverError(
            f"'{domain}' is on Cloudflare -- its nameservers are assigned by Cloudflare. "
            f"To {action}, revert the zone to local DNS first."
        )


def _needs_glue(domain: str, ns_hostname: str) -> bool:
    return ns_hostname == domain or ns_hostname.endswith(f".{domain}")


def _relative_subdomain(domain: str, ns_hostname: str) -> str:
    if ns_hostname == domain:
        return "@"
    return ns_hostname[: -(len(domain) + 1)]  # strip the trailing ".<domain>"


def _validate_glue_ip(value: str) -> str:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise ValidationError(f"'{value}' is not a valid IPv4/IPv6 address for a glue record")
    return value


def list_nameservers(params: dict) -> dict:
    domain = validate_domain(params["domain"])

    cf_row = dnsprovider.cloudflare_zone_row(domain)
    if cf_row is not None:
        # A Cloudflare zone's NS pair is assigned by Cloudflare, not edited
        # here -- report it (plus activation status) instead of the local
        # apex NS rrset, which is only the stale revert target.
        return {
            "domain": domain,
            "provider": "cloudflare",
            "managed": True,
            "cloudflare_status": cf_row.status,
            "nameservers": [ns.rstrip(".") for ns in (cf_row.name_servers or [])],
            "glue": {},
        }
    if not dnsprovider.zone_exists(domain):
        # A hosted name inside another zone (an addon or subdomain) has no
        # delegation of its own; reading one used to turn PowerDNS's 404
        # into an internal error. Same shape as dns.list_records.
        return {"domain": domain, "provider": None, "managed": False,
                "parent_zone": find_managed_zone(domain), "nameservers": [], "glue": {}}

    records = dnsprovider.list_records(domain)
    ns_rrset = next((r for r in records if r["type"] == "NS" and r["name"] == domain), None)
    nameservers = [v.rstrip(".") for v in (ns_rrset["values"] if ns_rrset else [])]

    glue = {}
    for ns in nameservers:
        if _needs_glue(domain, ns):
            sub = _relative_subdomain(domain, ns)
            a_rrset = next((r for r in records if r["type"] == "A" and r["name"] == ns), None)
            aaaa_rrset = next((r for r in records if r["type"] == "AAAA" and r["name"] == ns), None)
            glue[ns] = {
                "a": a_rrset["values"] if a_rrset else [],
                "aaaa": aaaa_rrset["values"] if aaaa_rrset else [],
            }
    return {"domain": domain, "provider": "local", "managed": True, "nameservers": nameservers, "glue": glue}


def default_delegation(domain: str) -> tuple[list[str], dict[str, str]]:
    """The NS set and in-zone glue a brand-new zone gets: the configured
    server nameservers (dnssetup.local_nameservers, ns1/ns2.<zone> when none
    are configured), each in-zone one glued to the server's primary address.
    External nameservers must be resolved by their owner, so get no glue."""
    nameservers = dnssetup.local_nameservers(domain)
    ip = settings.server_public_ip
    glue = {ns: ip for ns in nameservers if ip and _needs_glue(domain, ns) and ns != domain}
    return nameservers, glue


def _current_nameservers(domain: str, records: list[dict]) -> list[str]:
    rrset = next((r for r in records if r["type"] == "NS" and r["name"].rstrip(".") == domain), None)
    return [v.rstrip(".").lower() for v in (rrset["values"] if rrset else [])]


def _glue_still_used(domain: str, ns: str, records: list[dict]) -> str | None:
    """Why the address records of a dropped nameserver are more than glue,
    or None. A host that also carries other records, or that another record
    points at (MX, CNAME, SRV, sub-zone NS -- not the apex NS being
    replaced), keeps its address."""
    for row in records:
        name = row["name"].rstrip(".").lower()
        if name == ns and row["type"] not in ("A", "AAAA"):
            return f"it also has {row['type']} records"
        if row["type"] in ("MX", "CNAME", "SRV", "NS") and name != ns and not (row["type"] == "NS" and name == domain):
            for value in row.get("values", []):
                target = str(value).split()[-1:]  # the hostname is the last field
                if target and target[0].rstrip(".").lower() == ns:
                    return f"{name} {row['type']} points at it"
    return None


def _reference_reason(domain: str, zone: str, name: str, rtype: str) -> str:
    """The kept-glue reason for a record in another zone. Customers read it,
    so a zone is named only when the same account owns it: which domains a
    server hosts, and where their mail points, is not another tenant's to see."""
    with write_session() as session:
        owners = dict(session.execute(
            select(DnsZone.zone, DnsZone.account_id).where(DnsZone.zone.in_([domain, zone]))
        ).all())
    if domain in owners and owners.get(zone) == owners[domain]:
        return f"{name} {rtype} points at it"
    return f"a {rtype} record in another account's zone on this server points at it"


def _used_by_other_zones(domain: str, ns: str) -> str | None:
    """Why another zone on this server still needs the host's address, or
    None. A reseller's vanity nameserver lives in the reseller's own zone,
    yet every customer zone delegated to it (or pointing MX, CNAME or SRV
    at it) depends on that A/AAAA. One PowerDNS search covers every hosted
    zone, the local copies of Cloudflare zones included; an answer that
    cannot be complete keeps the address."""
    unverified = "the other zones on this server could not all be checked"
    try:
        rows = powerdns.search_records(f"*{ns}", REFERENCE_SEARCH_LIMIT)
    except Exception:  # noqa: BLE001 -- keeping the address is the safe outcome
        logger.exception("Could not search the hosted zones for records that use %s", ns)
        return unverified
    found_own = False
    for row in rows:
        zone = str(row.get("zone", "")).rstrip(".").lower()
        name = str(row.get("name", "")).rstrip(".").lower()
        if zone == domain:
            # This zone's own records were checked by _glue_still_used.
            found_own = found_own or name == ns
            continue
        if row.get("type") in ("NS", "MX", "CNAME", "SRV"):
            target = str(row.get("content", "")).split()[-1:]  # the hostname is the last field
            if target and target[0].rstrip(".").lower() == ns:
                return _reference_reason(domain, zone, name, row["type"])
    # A backend without search support returns no rows at all (not even the
    # address records being removed), and a full page may be cut short.
    if not found_own or len(rows) >= REFERENCE_SEARCH_LIMIT:
        return unverified
    return None


def _remove_stale_glue(domain: str, records: list[dict], previous: list[str], keep: set[str]) -> tuple[list[str], list[dict]]:
    """Delete the glue A/AAAA of in-zone nameservers that are no longer
    delegated to. Glue only exists because the host was a nameserver; left
    behind it keeps answering with an address nobody manages any more.
    The zone apex, hosted website names, the server's own configured
    nameservers (other zones delegate to them) and hosts that records in
    this or any other zone on the server still use are never touched --
    they are reported as kept instead."""
    with write_session() as session:
        hosted = set(session.scalars(select(Domain.domain).where(Domain.domain.in_(previous or [""]))).all())
    server_nameservers = {name.rstrip(".").lower() for name in dnssetup.local_nameservers()}
    removed, kept = [], []
    for ns in previous:
        if ns in keep or ns == domain or not _needs_glue(domain, ns):
            continue
        present = [rtype for rtype in ("A", "AAAA")
                   if any(r["type"] == rtype and r["name"].rstrip(".").lower() == ns for r in records)]
        if not present:
            continue
        reason = ("it is a hosted website name" if ns in hosted
                  else "it is one of this server's nameservers" if ns in server_nameservers
                  else _glue_still_used(domain, ns, records) or _used_by_other_zones(domain, ns))
        if reason:
            kept.append({"name": ns, "types": present, "reason": reason})
            continue
        for rtype in present:
            dnsprovider.delete_record(domain, _relative_subdomain(domain, ns), rtype)
            removed.append(f"{ns} {rtype}")
    return removed, kept


def _apply_delegation(domain: str, nameservers: list[str], glue: dict[str, str]) -> tuple[list[str], list[dict]]:
    records = dnsprovider.list_records(domain)
    for ns in glue:
        # PowerDNS refuses an address beside a CNAME; say so instead of a 502.
        if any(r["type"] == "CNAME" and r["name"].rstrip(".").lower() == ns for r in records):
            raise NameserverError(
                f"'{ns}' is a CNAME in this zone, but a nameserver needs its own A/AAAA glue record. "
                "Remove the CNAME first."
            )
    previous = _current_nameservers(domain, records)
    with dnsprovider.batch_cluster_notifications():
        for ns, ip in glue.items():
            rtype = "AAAA" if ":" in ip else "A"
            dnsprovider.upsert_record(domain, _relative_subdomain(domain, ns), rtype, [ip])
        dnsprovider.upsert_record(domain, "@", "NS", [f"{ns}." for ns in nameservers])
        return _remove_stale_glue(domain, records, previous, set(nameservers))


@dns_operations.serialized
def set_nameservers(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    raw_nameservers = params["nameservers"]
    if not isinstance(raw_nameservers, list) or not raw_nameservers:
        raise ValidationError("nameservers must be a non-empty list")
    if len(raw_nameservers) > 8:
        raise ValidationError("at most 8 nameservers are supported")

    nameservers = list(dict.fromkeys(validate_domain(ns) for ns in raw_nameservers))
    glue_in = params.get("glue") or {}
    if not isinstance(glue_in, dict):
        raise ValidationError("glue must be a mapping of nameserver hostname -> IP address")
    glue_in = {validate_domain(str(host)): ip for host, ip in glue_in.items()}

    _require_local_zone(domain, "set custom nameservers")

    resolved_glue: dict[str, str] = {}
    for ns in nameservers:
        if _needs_glue(domain, ns):
            ip = glue_in.get(ns)
            if not ip:
                raise NameserverError(
                    f"'{ns}' is a subdomain of '{domain}' -- a glue A/AAAA record (its own IP address) is required"
                )
            resolved_glue[ns] = _validate_glue_ip(str(ip).strip())

    if not dnsprovider.zone_exists(domain):
        raise NameserverError(f"no Boron-managed DNS zone for '{domain}' -- create one first")

    removed, kept = _apply_delegation(domain, nameservers, resolved_glue)
    return {"domain": domain, "nameservers": nameservers, "glue": resolved_glue, "removed_glue": removed,
            "kept_glue": kept}


@dns_operations.serialized
def reset_nameservers(params: dict) -> dict:
    """Reverts to exactly the delegation daemon/handlers_dns.py's create_zone
    seeds for a brand-new zone (default_delegation) rather than merely
    deleting the NS rrset (PowerDNS zones must always have at least one NS
    record; leaving the zone with none would break resolution entirely, not
    just "reset" it). Glue written for the replaced custom nameservers is
    removed and reported."""
    domain = validate_domain(params["domain"])
    _require_local_zone(domain, "reset nameservers")
    if not dnsprovider.zone_exists(domain):
        raise NameserverError(f"no Boron-managed DNS zone for '{domain}'")

    default_nameservers, glue = default_delegation(domain)
    removed, kept = _apply_delegation(domain, default_nameservers, glue)
    return {"domain": domain, "nameservers": default_nameservers, "status": "reset_to_defaults",
            "removed_glue": removed, "kept_glue": kept}
