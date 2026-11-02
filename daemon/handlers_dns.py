"""DNS zone + record CRUD (Phase c). RESEARCH.md SS6 / ARCHITECTURE.md SS6:
PowerDNS is driven exclusively through its REST API; the DnsZone table here
is a thin existence cache (account ownership + "does Boron manage this
zone") -- PowerDNS itself remains the source of truth for zone/record
content.
"""
from __future__ import annotations

import logging

from daemon import dns_operations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, DnsZone
from shared.validation import ConflictError, NotFoundError, ValidationError, validate_domain, validate_record_type

from daemon import audit, cloudflare_ops, dkim, dnsprovider, mail_dns
from daemon import nameservers as nameserver_policy
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone

RECORD_VALUE_VALIDATORS = {
    "A": lambda v: _validate_ipv4(v),
    "AAAA": lambda v: _validate_ipv6(v),
    "CNAME": lambda v: _validate_host(v, "CNAME target"),
    "MX": lambda v: _validate_mx(v),
    "TXT": lambda v: _validate_txt(v),
    "PTR": lambda v: validate_domain(_text_value(v).rstrip(".")) + ".",
    "NS": lambda v: _validate_host(v, "NS target"),
    "SRV": lambda v: _validate_srv(v),
    "CAA": lambda v: _validate_caa(v),
}

# Limits shared by the simple record editor and the advanced zone editor
# (daemon/dns_editor.py), so a record one accepts never makes the other
# reject the zone. RFC 2181 SS8 caps TTLs at 2^31 - 1.
MIN_TTL = 60
MAX_TTL = 2147483647
MAX_RRSET_VALUES = 100
TXT_MAX_OCTETS = 2048
APEX_NS_MESSAGE = (
    "The zone's own NS records are managed on the Nameservers page, which also "
    "checks the glue records in-zone nameservers need."
)

logger = logging.getLogger("borond.dns")


def _text_value(value) -> str:
    if not isinstance(value, str):
        raise ValidationError("DNS record values must be text")
    return value.strip()


def _uint16(value: str, field: str) -> int:
    if not value.isdigit() or not 0 <= int(value) <= 65535:
        raise ValidationError(f"{field} '{value}' must be a whole number from 0 to 65535")
    return int(value)


def _validate_host(value, field: str, *, allow_root: bool = False) -> str:
    """A target hostname (absolute, trailing dot). IP literals are rejected:
    MX/SRV/NS/CNAME targets are names, and '1.2.3.4' would be looked up as
    a host inside the '4' top-level domain."""
    import ipaddress

    host = _text_value(value)
    if allow_root and host == ".":
        return "."
    bare = host.rstrip(".")
    try:
        ipaddress.ip_address(bare)
    except ValueError:
        pass
    else:
        raise ValidationError(f"{field} must be a hostname, not the IP address '{bare}'")
    return validate_domain(bare) + "."


def _validate_ipv4(value: str) -> str:
    import ipaddress

    value = _text_value(value)
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError as exc:
        raise ValidationError(f"'{value}' is not a valid IPv4 address") from exc


def _validate_ipv6(value: str) -> str:
    import ipaddress

    value = _text_value(value)
    try:
        return str(ipaddress.IPv6Address(value))
    except ValueError as exc:
        raise ValidationError(f"'{value}' is not a valid IPv6 address") from exc


def _validate_mx(value: str) -> str:
    parts = _text_value(value).split()
    if len(parts) != 2 or not parts[0].isdigit():
        raise ValidationError(f"MX value '{value}' must be '<priority> <host>'")
    priority = _uint16(parts[0], "MX priority")
    # RFC 7505 null MX ("0 .") declares that the domain accepts no mail.
    host = _validate_host(parts[1], "MX host", allow_root=True)
    if host == "." and priority != 0:
        raise ValidationError("A null MX record must be written as '0 .'")
    return f"{priority} {host}"


def _validate_txt(value: str) -> str:
    """One logical TXT value. Already-quoted input is read as zone-file
    character-strings; anything else is stored literally. The result is
    the canonical form PowerDNS stores: escaped (control characters as
    \\DDD) and split into strings of at most 255 octets."""
    if not isinstance(value, str):
        raise ValidationError("DNS record values must be text")
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        import dns.exception
        import dns.tokenizer

        tokenizer = dns.tokenizer.Tokenizer(stripped)
        chunks = []
        try:
            while True:
                token = tokenizer.get()
                if token.is_eof():
                    break
                if not token.is_quoted_string():
                    raise dns.exception.SyntaxError
                chunks.append(token.unescape_to_bytes().value)
        except dns.exception.DNSException:
            raise ValidationError(
                "TXT value has unbalanced or unescaped quotes; enter the text without "
                "surrounding quotes to store it exactly as typed"
            ) from None
        data = b"".join(chunks)
    else:
        data = value.encode("utf-8")
    if len(data) > TXT_MAX_OCTETS:
        raise ValidationError(f"TXT value too long (max {TXT_MAX_OCTETS} bytes)")
    return dnsprovider.txt_presentation(data)


def _validate_srv(value: str) -> str:
    parts = _text_value(value).split()
    if len(parts) != 4 or not all(p.isdigit() for p in parts[:3]):
        raise ValidationError(f"SRV value '{value}' must be '<priority> <weight> <port> <target>'")
    priority = _uint16(parts[0], "SRV priority")
    weight = _uint16(parts[1], "SRV weight")
    port = _uint16(parts[2], "SRV port")
    # RFC 2782: a target of "." means the service is not available here.
    target = _validate_host(parts[3], "SRV target", allow_root=True)
    return f"{priority} {weight} {port} {target}"


def _validate_caa(value: str) -> str:
    parts = _text_value(value).split(None, 2)
    if len(parts) != 3 or not parts[0].isdigit():
        raise ValidationError(f"CAA value '{value}' must be '<flags> <tag> <value>'")
    flags, tag, tag_value = parts
    if not (0 <= int(flags) <= 255):
        raise ValidationError(f"CAA flags '{flags}' out of range (0-255)")
    # RFC 8659 SS4.1: tags are case-insensitive; store the canonical form.
    tag = tag.lower()
    if tag not in ("issue", "issuewild", "iodef"):
        raise ValidationError(f"CAA tag '{tag}' must be one of issue/issuewild/iodef")
    tag_value = tag_value.strip()
    if len(tag_value) >= 2 and tag_value.startswith('"') and tag_value.endswith('"'):
        inner = dnsprovider.txt_strings(tag_value)
        if len(inner) != 1:
            raise ValidationError("CAA value must be a single quoted string")
        data = inner[0]
    else:
        data = tag_value.encode("utf-8")
    if len(data) > 255:
        raise ValidationError("CAA value too long (max 255 bytes)")
    return f"{int(flags)} {tag} {dnsprovider.txt_presentation(data)}"


def _value_key(rtype: str, value: str):
    if rtype == "TXT":
        return dnsprovider.txt_bytes(value)
    if rtype in ("CNAME", "MX", "NS", "PTR", "SRV"):
        return value.lower()
    return value


def validate_rrset(label: str, rtype: str, values, ttl) -> list[str]:
    """Validate one (name, type) record set exactly as both editors store it.

    `label` is a zone-relative owner ('@' for the apex). Returns normalized,
    de-duplicated values. daemon/dns_editor.py's advanced editor and
    set_record below share this so neither accepts what the other rejects.
    """
    if rtype not in RECORD_VALUE_VALIDATORS:
        raise ValidationError(f"record type '{rtype}' is not editable here")
    if rtype == "NS" and label == "@":
        raise ValidationError(APEX_NS_MESSAGE)
    if isinstance(ttl, bool):
        raise ValidationError("ttl must be a whole number of seconds")
    try:
        ttl = int(ttl)
    except (TypeError, ValueError):
        raise ValidationError("ttl must be a whole number of seconds") from None
    if not MIN_TTL <= ttl <= MAX_TTL:
        raise ValidationError(f"DNS TTL must be between {MIN_TTL} and {MAX_TTL} seconds")
    if not isinstance(values, (list, tuple)) or not values:
        raise ValidationError("values must be a non-empty list")
    validator = RECORD_VALUE_VALIDATORS[rtype]
    normalized: dict = {}
    for raw in values:
        value = validator(raw)
        normalized.setdefault(_value_key(rtype, value), value)
    result = list(normalized.values())
    if len(result) > MAX_RRSET_VALUES:
        raise ValidationError(f"A DNS record set can hold at most {MAX_RRSET_VALUES} values")
    if rtype == "CNAME":
        if label == "@":
            raise ValidationError('The zone apex needs SOA/NS records and cannot be a CNAME. Use A or AAAA instead.')
        if len(result) != 1:
            raise ValidationError('A CNAME must have exactly one target')
    if rtype == "MX" and "0 ." in result and len(result) > 1:
        raise ValidationError("A null MX record ('0 .') must be the only MX record for its name")
    return result


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
                raise NotFoundError(f"account '{username}' not found")
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
            raise ConflictError(f"zone '{domain_name}' already managed by Boron")
        # A zone answers for every name beneath it that has no more specific
        # zone, and a child zone overrides its parent. Neither may span a
        # name another account holds.
        from daemon import domain_ownership
        domain_ownership.require_no_foreign_overlap(session, domain_name, account.id)

    if dnsprovider.zone_exists(domain_name):
        raise ConflictError(f"zone '{domain_name}' already exists in PowerDNS")

    # The same default delegation Nameservers > Reset restores. Glue points
    # at the server's primary address and belongs only in this zone when the
    # configured nameserver is a child of it. dnsprovider.create_zone also
    # names the first nameserver as the SOA primary (MNAME).
    nameservers, glue = nameserver_policy.default_delegation(domain_name)
    ns_records = [f"{name}." for name in nameservers]
    dnsprovider.create_zone(domain_name, ns_records)

    # The website apex follows this account's assigned shared/dedicated
    # address. A local import avoids an ipmanager -> DNS import cycle.
    from daemon import ipmanager

    site_ip = ipmanager.address_for_account(account.id)
    for hostname, glue_ip in glue.items():
        glue_type = "AAAA" if ":" in glue_ip else "A"
        dnsprovider.upsert_record(
            domain_name, label_within_zone(hostname, domain_name), glue_type, [glue_ip]
        )
    if site_ip:
        rtype = "AAAA" if ":" in site_ip else "A"
        dnsprovider.upsert_record(domain_name, "@", rtype, [site_ip])
        dnsprovider.upsert_record(domain_name, "www", "CNAME", [f"{domain_name}."])

    # A fresh zone starts ready for ordinary hosting and mail.  Existing
    # zones are never modified by this path; their explicit preview/repair
    # workflow lives in daemon.mail_dns.
    try:
        with dnsprovider.batch_cluster_notifications():
            for record in mail_dns.default_records(domain_name, include_dkim=False):
                dnsprovider.upsert_record(
                    domain_name, record.label, record.rtype, list(record.values)
                )
    except ValidationError:
        # Upgraded/bootstrap installations may not have chosen a canonical
        # mail hostname yet.  Zone creation must still succeed; Server Setup
        # and Email DNS explain and repair the missing mail records later.
        pass

    with write_session() as session:
        zone_row = DnsZone(account_id=account.id if account else None, zone=domain_name)
        session.add(zone_row)
        session.flush()
        result = _zone_dict(zone_row)

    # DKIM belongs to a hosted domain, not to an individual mailbox. Generate
    # and publish a unique signing key as soon as the authoritative zone exists
    # so a new domain is mail-ready before its first email account is created.
    # Keep zone creation usable if the local signer needs operator attention;
    # Email DNS surfaces the inactive state and provides a retry action.
    try:
        result["dkim"] = dkim.setup_dns_signing(domain_name)
    except Exception:
        logger.exception("DKIM activation failed after creating zone '%s'", domain_name)

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
    subdomain = _record_label(domain_name, params.get("subdomain", "@") or "@")
    rtype = validate_record_type(params["type"])
    ttl = params.get("ttl", dnsprovider.DEFAULT_TTL)
    # Accepted end-to-end but forced false inside dnsprovider.upsert_record
    # until the Phase 2 real-IP rails report configured (plan SS1.5/SS1.7).
    proxied = bool(params.get("proxied", False))
    mode = params.get('mode', 'replace')
    if mode not in ('add', 'replace'):
        raise ValidationError('DNS save mode must be add or replace')

    values = validate_rrset(subdomain, rtype, params.get("values"), ttl)
    ttl = int(ttl)
    _require_own_zone(domain_name)

    name = domain_name if subdomain == '@' else f'{subdomain}.{domain_name}'
    records = dnsprovider.list_records(domain_name)
    same_name = [row for row in records if row.get('name', '').lower().rstrip('.') == name]
    before_record = next((row for row in same_name if row.get('type') == rtype), None)
    if rtype == 'CNAME':
        if any(row.get('type') != 'CNAME' for row in same_name):
            raise ValidationError('A CNAME cannot share a name with other record types. Remove the conflicting records first.')
    elif any(row.get('type') == 'CNAME' for row in same_name):
        raise ValidationError('This name already has a CNAME. Remove it before adding another record type.')
    if mode == 'add' and before_record is not None:
        if rtype == 'CNAME':
            current = [item if isinstance(item, str) else item['content'] for item in before_record.get('values', [])]
            if any(_value_key(rtype, item) != _value_key(rtype, values[0]) for item in current):
                raise ValidationError('This name already has a different CNAME. Edit the existing record to replace it.')
        previous = before_record.get('values', before_record.get('records', []))
        # Existing values pass the same validation, so the merged set is
        # one the advanced editor can also read back.
        values = validate_rrset(subdomain, rtype, [
            *(item if isinstance(item, str) else item['content'] for item in previous), *values,
        ], ttl)

    after_record = {
        "name": name, "type": rtype, "values": values,
        "ttl": ttl, "proxied": proxied,
    }
    audit.change("dns_record", before_record, after_record, sensitive=rtype == "TXT")
    with audit.step("DNS: write record through provider"):
        dnsprovider.upsert_record(domain_name, subdomain, rtype, values, ttl=ttl, proxied=proxied)
    return {"zone": domain_name, "subdomain": subdomain, "type": rtype, "values": values, "ttl": ttl}


def _require_own_zone(domain_name: str) -> None:
    """Record writes target a zone, never a hosted name inside another zone
    (an addon or subdomain without its own zone); say where it lives."""
    if dnsprovider.zone_exists(domain_name):
        return
    parent = find_managed_zone(domain_name)
    if parent:
        label = label_within_zone(domain_name, parent)
        raise ValidationError(
            f"'{domain_name}' has no DNS zone of its own; its records are in the '{parent}' "
            f"zone. Edit that zone and use the name '{label}'."
        )
    raise NotFoundError(f"No DNS zone is managed here for '{domain_name}'")


def _record_label(zone: str, name: str) -> str:
    import re
    if not isinstance(name, str):
        raise ValidationError('Record name must be @ or a name within this DNS zone')
    name = name.strip().lower()
    absolute = name.endswith('.')
    name = name.rstrip('.')
    if name in ('@', '', zone):
        return '@'
    if name.endswith('.' + zone):
        name = name[:-(len(zone) + 1)]
    elif absolute:
        raise ValidationError('The record name is outside this DNS zone')
    labels = name.split('.')
    if len(f'{name}.{zone}') > 253 or any(not re.fullmatch(r'(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?|\*)', label) for label in labels) or '*' in labels[1:]:
        raise ValidationError('Use a valid DNS name within this zone; spaces, slashes and empty labels are not allowed')
    return name


@dns_operations.serialized
def delete_record(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    subdomain = _record_label(domain_name, params.get("subdomain", "@") or "@")
    rtype = validate_record_type(params["type"])
    if rtype == "NS" and subdomain == "@":
        raise ValidationError(APEX_NS_MESSAGE)
    _require_own_zone(domain_name)
    name = domain_name if subdomain == "@" else f"{subdomain}.{domain_name}"
    before_record = next((row for row in dnsprovider.list_records(domain_name)
                          if row.get("name", "").lower().rstrip(".") == name
                          and row.get("type") == rtype), None)
    audit.change("dns_record", before_record, None, sensitive=rtype == "TXT")
    with audit.step("DNS: delete record through provider"):
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
