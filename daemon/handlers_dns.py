"""DNS zone + record CRUD (Phase c). RESEARCH.md SS6 / ARCHITECTURE.md SS6:
PowerDNS is driven exclusively through its REST API; the DnsZone table here
is a thin existence cache (account ownership + "does Forgehost manage this
zone") -- PowerDNS itself remains the source of truth for zone/record
content.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DnsZone
from shared.validation import ValidationError, validate_domain, validate_record_type

from daemon import powerdns

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
            raise RuntimeError(f"zone '{domain_name}' already managed by Forgehost")

    if powerdns.zone_exists(domain_name):
        raise RuntimeError(f"zone '{domain_name}' already exists in PowerDNS")

    ns_records = [f"ns1.{domain_name}.", f"ns2.{domain_name}."]
    powerdns.create_zone(domain_name, ns_records)

    ip = settings.server_public_ip
    if ip:
        powerdns.upsert_record(domain_name, "ns1", "A", [ip])
        powerdns.upsert_record(domain_name, "ns2", "A", [ip])
        powerdns.upsert_record(domain_name, "@", "A", [ip])
        powerdns.upsert_record(domain_name, "www", "CNAME", [f"{domain_name}."])

    with write_session() as session:
        zone_row = DnsZone(account_id=account.id if account else None, zone=domain_name)
        session.add(zone_row)
        session.flush()
        return _zone_dict(zone_row)


def delete_zone(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    powerdns.delete_zone(domain_name)
    with write_session() as session:
        zone_row = session.scalar(select(DnsZone).where(DnsZone.zone == domain_name))
        if zone_row is not None:
            session.delete(zone_row)
    return {"zone": domain_name, "status": "deleted"}


def list_records(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    return {"zone": domain_name, "records": powerdns.list_records(domain_name)}


def set_record(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    subdomain = params.get("subdomain", "@") or "@"
    rtype = validate_record_type(params["type"])
    raw_values = params["values"]
    if not isinstance(raw_values, list) or not raw_values:
        raise ValidationError("values must be a non-empty list")
    ttl = int(params.get("ttl", powerdns.DEFAULT_TTL))
    if ttl < 60:
        raise ValidationError("ttl must be >= 60 seconds")

    validator = RECORD_VALUE_VALIDATORS[rtype]
    values = [validator(v) for v in raw_values]

    powerdns.upsert_record(domain_name, subdomain, rtype, values, ttl=ttl)
    return {"zone": domain_name, "subdomain": subdomain, "type": rtype, "values": values, "ttl": ttl}


def delete_record(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    subdomain = params.get("subdomain", "@") or "@"
    rtype = validate_record_type(params["type"])
    powerdns.delete_record(domain_name, subdomain, rtype)
    return {"zone": domain_name, "subdomain": subdomain, "type": rtype, "status": "deleted"}


def terminate_account_zones(account: Account) -> None:
    """TERMINATE_HOOKS entry: delete every DNS zone this account owns.
    Idempotent -- safe even if the account never had a zone."""
    with write_session() as session:
        zones = session.scalars(select(DnsZone.zone).where(DnsZone.account_id == account.id)).all()
    for zone in zones:
        try:
            powerdns.delete_zone(zone)
        except powerdns.PowerDnsError:
            pass  # already gone server-side; still drop our cache row below
        with write_session() as session:
            zone_row = session.scalar(select(DnsZone).where(DnsZone.zone == zone))
            if zone_row is not None:
                session.delete(zone_row)
