"""DNS zone + record CRUD (Phase c). RESEARCH.md SS6 / ARCHITECTURE.md SS6:
PowerDNS is driven exclusively through its REST API; the DnsZone table here
is a thin existence cache (account ownership + "does Boron manage this
zone") -- PowerDNS itself remains the source of truth for zone/record
content.
"""
from __future__ import annotations

from daemon import dns_operations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DnsZone
from shared.validation import ValidationError, validate_domain, validate_record_type

from daemon import cloudflare_ops, dnsprovider, dnssetup
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone

RECORD_VALUE_VALIDATORS = {
    "A": lambda v: _validate_ipv4(v),
    "AAAA": lambda v: _validate_ipv6(v),
    "CNAME": lambda v: validate_domain(v.rstrip(".")) + ".",
    "MX": lambda v: _validate_mx(v),
    "TXT": lambda v: _validate_txt(v),
    "PTR": lambda v: validate_domain(v.rstrip(".")) + ".",
    "SRV": lambda v: _validate_srv(v),
    "CAA": lambda v: _validate_caa(v),
}


def _validate_ipv4(value: str) -> str:
    import ipaddress

    try:
        ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise ValidationError(f"'{value}' is not a valid IPv4 address") from exc
    return value


def _validate_ipv6(value: str) -> str:
    import ipaddress

    try:
        ipaddress.IPv6Address(value)
    except ValueError as exc:
        raise ValidationError(f"'{value}' is not a valid IPv6 address") from exc
    return value


def _validate_mx(value: str) -> str:
    parts = value.split()
    if len(parts) != 2 or not parts[0].isdigit():
        raise ValidationError(f"MX value '{value}' must be '<priority> <host>'")
    priority, host = parts
    return f"{priority} {validate_domain(host.rstrip('.'))}."


def _validate_txt(value: str) -> str:
    if len(value) > 2048:
        raise ValidationError("TXT value too long (max 2048 chars)")
    if value.startswith('"') and value.endswith('"'):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _validate_srv(value: str) -> str:
    parts = value.split()
    if len(parts) != 4 or not all(p.isdigit() for p in parts[:3]):
        raise ValidationError(f"SRV value '{value}' must be '<priority> <weight> <port> <target>'")
    priority, weight, port, target = parts
    if not (0 <= int(port) <= 65535):
        raise ValidationError(f"SRV port '{port}' out of range")
    return f"{priority} {weight} {port} {validate_domain(target.rstrip('.'))}."


def _validate_caa(value: str) -> str:
    parts = value.split(None, 2)
    if len(parts) != 3 or not parts[0].isdigit():
        raise ValidationError(f"CAA value '{value}' must be '<flags> <tag> <value>'")
    flags, tag, tag_value = parts
    if not (0 <= int(flags) <= 255):
        raise ValidationError(f"CAA flags '{flags}' out of range (0-255)")
    if tag not in ("issue", "issuewild", "iodef"):
        raise ValidationError(f"CAA tag '{tag}' must be one of issue/issuewild/iodef")
    tag_value = tag_value.strip()
    if tag_value.startswith('"') and tag_value.endswith('"'):
        return f"{flags} {tag} {tag_value}"
    escaped = tag_value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{flags} {tag} "{escaped}"'


def _zone_dict(zone: DnsZone) -> dict:
    return {
        "id": zone.id,
        "account_id": zone.account_id,
        "zone": zone.zone,
        "created_at": zone.created_at.isoformat() if zone.created_at else None,
    }


@dns_operations.serialized
def create_zone(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    username = params.get("username")

    with write_session() as session:
        account = None
        if username:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise RuntimeError(f"account '{username}' not found")
        # Security audit finding F13: DnsZone.account_id is a NOT NULL
        # foreign key, but nothing previously stopped this function from
        # reaching the insert with account=None (the API layer only
        # passes `username` when it could resolve an owning Domain row --
        # daemon/api/routers/dns.py's create_zone) -- that insert would
        # hit a raw, unhandled IntegrityError instead of a clean message.
        # Admin-only endpoint, so not customer-reachable, but fail with a
        # clear validation error instead of a raw DB exception.
        if account is None:
            raise ValidationError(
                f"cannot create a zone for '{domain_name}' -- no account owns this domain yet "
                "(add the domain to an account first)"
            )
        existing = session.scalar(select(DnsZone).where(DnsZone.zone == domain_name))
        if existing is not None:
            raise RuntimeError(f"zone '{domain_name}' already managed by Boron")

    if dnsprovider.zone_exists(domain_name):
        raise RuntimeError(f"zone '{domain_name}' already exists in PowerDNS")

    nameservers = dnssetup.local_nameservers(domain_name)
    ns_records = [f"{name}." for name in nameservers]
    dnsprovider.create_zone(domain_name, ns_records)

    # Nameserver glue always points at the server's primary address. The
    # website apex follows this account's assigned shared/dedicated address.
    # A local import avoids an ipmanager -> DNS import cycle.
    from daemon import ipmanager

    ip = settings.server_public_ip
    site_ip = ipmanager.address_for_account(account.id)
    if ip:
        glue_type = "AAAA" if ":" in ip else "A"
        # Glue belongs only in this zone when the configured nameserver is a
        # child of it. External nameservers must be resolved by their owner.
        for hostname in nameservers:
            if hostname.endswith(f".{domain_name}"):
                dnsprovider.upsert_record(
                    domain_name, label_within_zone(hostname, domain_name), glue_type, [ip]
                )
    if site_ip:
        rtype = "AAAA" if ":" in site_ip else "A"
        dnsprovider.upsert_record(domain_name, "@", rtype, [site_ip])
        dnsprovider.upsert_record(domain_name, "www", "CNAME", [f"{domain_name}."])

    with write_session() as session:
        zone_row = DnsZone(account_id=account.id if account else None, zone=domain_name)
        session.add(zone_row)
        session.flush()
        result = _zone_dict(zone_row)

    # Phase 2+3 feature 6: auto-enable Cloudflare for the new zone when the
    # admin toggle / default_dns_provider says so and a pool account has
    # capacity -- so the customer gets the nameserver pair to set at their
    # registrar immediately. Best-effort: never fails the zone creation.
    cf = cloudflare_ops.maybe_auto_enable(domain_name)
    if cf is not None:
        result["cloudflare"] = cf
    return result


@dns_operations.serialized
def delete_zone(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    dnsprovider.delete_zone(domain_name)
    with write_session() as session:
        zone_row = session.scalar(select(DnsZone).where(DnsZone.zone == domain_name))
        if zone_row is not None:
            session.delete(zone_row)
    return {"zone": domain_name, "status": "deleted"}


def list_records(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    if not dnsprovider.zone_exists(domain_name):
        # This domain name isn't itself a Boron-managed DNS zone -- a
        # zone (local or Cloudflare) only ever exists for the domain
        # dns.create_zone was called on, never for a subdomain/addon that
        # merely lives inside another domain's zone (find_managed_zone).
        # Report which one that is (if any) instead of letting a raw 404
        # from the DNS backend bubble up -- the UI needs to tell these two
        # very different situations apart.
        return {
            "zone": domain_name,
            "managed": False,
            "parent_zone": find_managed_zone(domain_name),
            "records": [],
            "provider": None,
            "proxy_available": dnsprovider.proxied_allowed(),
            "cloudflare": None,
        }

    result = {
        "zone": domain_name,
        "managed": True,
        "records": dnsprovider.list_records(domain_name),
        "provider": dnsprovider.provider_for_zone(domain_name),
        "proxy_available": dnsprovider.proxied_allowed(),
    }
    cf_row = dnsprovider.cloudflare_zone_row(domain_name)
    if cf_row is not None:
        result["cloudflare"] = {"status": cf_row.status, "name_servers": cf_row.name_servers}
    return result


@dns_operations.serialized
def set_record(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    subdomain = params.get("subdomain", "@") or "@"
    rtype = validate_record_type(params["type"])
    raw_values = params["values"]
    if not isinstance(raw_values, list) or not raw_values:
        raise ValidationError("values must be a non-empty list")
    ttl = int(params.get("ttl", dnsprovider.DEFAULT_TTL))
    if ttl < 60:
        raise ValidationError("ttl must be >= 60 seconds")
    # Accepted end-to-end but forced false inside dnsprovider.upsert_record
    # until the Phase 2 real-IP rails report configured (plan SS1.5/SS1.7).
    proxied = bool(params.get("proxied", False))

    validator = RECORD_VALUE_VALIDATORS[rtype]
    values = [validator(v) for v in raw_values]

    dnsprovider.upsert_record(domain_name, subdomain, rtype, values, ttl=ttl, proxied=proxied)
    return {"zone": domain_name, "subdomain": subdomain, "type": rtype, "values": values, "ttl": ttl}


@dns_operations.serialized
def delete_record(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    subdomain = params.get("subdomain", "@") or "@"
    rtype = validate_record_type(params["type"])
    dnsprovider.delete_record(domain_name, subdomain, rtype)
    return {"zone": domain_name, "subdomain": subdomain, "type": rtype, "status": "deleted"}


@dns_operations.serialized
def terminate_account_zones(account: Account) -> None:
    """TERMINATE_HOOKS entry: delete every DNS zone this account owns.
    Idempotent -- safe even if the account never had a zone."""
    with write_session() as session:
        zones = session.scalars(select(DnsZone.zone).where(DnsZone.account_id == account.id)).all()
    for zone in zones:
        try:
            dnsprovider.delete_zone(zone)
        except dnsprovider.DnsError:
            pass  # already gone server-side; still drop our cache row below
        with write_session() as session:
            zone_row = session.scalar(select(DnsZone).where(DnsZone.zone == zone))
            if zone_row is not None:
                session.delete(zone_row)
