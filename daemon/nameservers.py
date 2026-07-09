"""Custom nameserver + glue record management (Phase 4 feature 11).

Uses the same PowerDNS REST API every other DNS feature in this project
already goes through -- no new DB table, since PowerDNS itself is already
the source of truth for zone/record content
(daemon/handlers_dns.py's own docstring). The NS rrset at the zone apex
and any glue A/AAAA records this writes are just more PowerDNS records,
the same primitive upsert_record()/list_records() already provide.

This module's own job is the validation layer the general DNS editor
deliberately doesn't have -- NS is excluded from validate_record_type's
allowed set on purpose, since NS at the zone apex is normally
auto-managed (daemon/handlers_dns.py's create_zone seeds ns1/ns2 defaults
pointing at this server's own IP). The one thing that actually needs new
logic here: detecting when a chosen NS hostname is itself a subdomain of
(or equal to) the zone being delegated -- a circular-resolution problem
(you can't look up ns1.example.com's own address without first being able
to resolve *something* in example.com) -- and requiring a glue A/AAAA
record for exactly those, matching the goal's own "glue form if NS is
subdomain of managed domain" instruction.
"""
from __future__ import annotations

import ipaddress

from shared.config import settings
from shared.validation import ValidationError, validate_domain

from daemon import dnsprovider


class NameserverError(Exception):
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
            "cloudflare_status": cf_row.status,
            "nameservers": [ns.rstrip(".") for ns in (cf_row.name_servers or [])],
            "glue": {},
        }

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
    return {"domain": domain, "provider": "local", "nameservers": nameservers, "glue": glue}


def set_nameservers(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    raw_nameservers = params["nameservers"]
    if not isinstance(raw_nameservers, list) or not raw_nameservers:
        raise ValidationError("nameservers must be a non-empty list")
    if len(raw_nameservers) > 8:
        raise ValidationError("at most 8 nameservers are supported")

    nameservers = [validate_domain(ns) for ns in raw_nameservers]
    glue_in = params.get("glue") or {}
    if not isinstance(glue_in, dict):
        raise ValidationError("glue must be a mapping of nameserver hostname -> IP address")

    _require_local_zone(domain, "set custom nameservers")

    resolved_glue: dict[str, str] = {}
    for ns in nameservers:
        if _needs_glue(domain, ns):
            ip = glue_in.get(ns)
            if not ip:
                raise NameserverError(
                    f"'{ns}' is a subdomain of '{domain}' -- a glue A/AAAA record (its own IP address) is required"
                )
            resolved_glue[ns] = _validate_glue_ip(ip)

    if not dnsprovider.zone_exists(domain):
        raise NameserverError(f"no Forgehost-managed DNS zone for '{domain}' -- create one first")

    for ns, ip in resolved_glue.items():
        sub = _relative_subdomain(domain, ns)
        rtype = "AAAA" if ":" in ip else "A"
        dnsprovider.upsert_record(domain, sub, rtype, [ip])

    dnsprovider.upsert_record(domain, "@", "NS", [f"{ns}." for ns in nameservers])

    return {"domain": domain, "nameservers": nameservers, "glue": resolved_glue}


def reset_nameservers(params: dict) -> dict:
    """Reverts to exactly what daemon/handlers_dns.py's create_zone
    originally seeded for a brand-new zone -- ns1/ns2 pointed at this
    server's own IP -- rather than merely deleting the NS rrset (PowerDNS
    zones must always have at least one NS record; leaving the zone with
    none would break resolution entirely, not just "reset" it)."""
    domain = validate_domain(params["domain"])
    _require_local_zone(domain, "reset nameservers")
    if not dnsprovider.zone_exists(domain):
        raise NameserverError(f"no Forgehost-managed DNS zone for '{domain}'")

    ip = settings.server_public_ip
    default_nameservers = [f"ns1.{domain}", f"ns2.{domain}"]
    if ip:
        dnsprovider.upsert_record(domain, "ns1", "A", [ip])
        dnsprovider.upsert_record(domain, "ns2", "A", [ip])
    dnsprovider.upsert_record(domain, "@", "NS", [f"{ns}." for ns in default_nameservers])
    return {"domain": domain, "nameservers": default_nameservers, "status": "reset_to_defaults"}
