"""Provider-neutral mail DNS template, preview and repair operations.

The preview reads this server's zone. When the public delegation of the
domain's zone does not point at this server's nameservers, that zone is not
what other mail servers see: the preview then reports MX, SPF, DKIM and
DMARC as public resolvers return them (short timeouts, briefly cached by
daemon/mail_local_delivery.public_lookup) and says the zone is not
authoritative. Repair always writes this server's zone.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import time

from sqlalchemy import select

from daemon import dkim, dnsprovider, mail_local_delivery
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone
from shared.config import settings
from shared.db import write_session
from shared.models import DkimKey, Domain, MailDomain
from shared.validation import ValidationError, validate_domain, validate_email_address

# Upper bound for the public lookups of one preview (each lookup times out
# after a few seconds on its own); later lookups report "unknown".
PUBLIC_CHECK_SECONDS = 8.0
PUBLIC_KEYS = ("mx", "spf", "dkim", "dmarc")


def canonical_mail_hostname() -> str:
    value = settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname
    if not value:
        raise ValidationError("Configure a canonical mail hostname in Server Setup first")
    return validate_domain(value)


def _txt(value: str) -> str:
    """TXT presentation in strings of at most 255 octets (RFC 1035 SS3.3),
    the form every provider write is normalized to anyway."""
    return dnsprovider.txt_presentation(value.encode("utf-8"))


@dataclass(frozen=True)
class DesiredRecord:
    key: str
    label: str
    rtype: str
    values: tuple[str, ...]
    required: bool = True


def default_records(zone: str, *, include_dkim: bool = True) -> list[DesiredRecord]:
    zone = validate_domain(zone)
    mail_host = canonical_mail_hostname()
    records: list[DesiredRecord] = [
        DesiredRecord("mx", "@", "MX", (f"10 {mail_host}.",)),
        DesiredRecord("spf", "@", "TXT", (_txt("v=spf1 mx a ~all"),)),
        DesiredRecord("ftp", "ftp", "CNAME", (f"{zone}.",)),
    ]
    if settings.server_public_ip:
        address_type = "AAAA" if ":" in settings.server_public_ip else "A"
        if mail_host == zone:
            # The apex address is already part of the web template.
            pass
        elif mail_host.endswith(f".{zone}"):
            records.append(DesiredRecord("mail", label_within_zone(mail_host, zone), address_type,
                                         (settings.server_public_ip,)))
        else:
            # A familiar mail.<zone> alias remains useful for clients even
            # when MX points at the server-wide canonical TLS hostname.
            records.append(DesiredRecord("mail", "mail", "CNAME", (f"{mail_host}.",)))

    if include_dkim:
        with write_session() as db:
            key = db.scalar(select(DkimKey).where(DkimKey.domain == zone))
        if key is not None and key.signing_active:
            from daemon.dkim import dkim_txt_value
            records.append(DesiredRecord(
                "dkim", f"{key.selector}._domainkey", "TXT",
                (_txt(dkim_txt_value(zone, key.selector)),),
            ))
    return records


def _canonical_values(rtype: str, values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result = []
    for value in values:
        clean = value.strip()
        if rtype == "TXT":
            clean = dnsprovider.txt_text(clean)
        elif rtype in {"MX", "CNAME", "NS"}:
            clean = clean.lower().rstrip(".")
        result.append(clean)
    return tuple(sorted(result))


def _is_spf(value: str) -> bool:
    return dnsprovider.txt_text(value).strip().lower().startswith("v=spf1")


def _record_map(zone: str) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for record in dnsprovider.list_records(zone):
        name = str(record.get("name", "")).lower().rstrip(".")
        if name == zone:
            label = "@"
        elif name.endswith(f".{zone}"):
            label = name[: -(len(zone) + 1)]
        else:
            label = name
        result[(label, str(record.get("type", "")).upper())] = record
    return result


def _template_rows(domain: str, label_prefix: str) -> list[dict]:
    """The mail template of ``domain``, named relative to its zone."""
    def name(label: str) -> str:
        if label_prefix == "@":
            return label
        return label_prefix if label == "@" else f"{label}.{label_prefix}"

    rows = [{"key": item.key, "name": name(item.label), "type": item.rtype, "expected": list(item.values)}
            for item in default_records(domain)]
    # A hosted domain must always expose DKIM in readiness, even before its
    # first mailbox exists. DKIM is domain-level signing: requiring a
    # MailDomain row made the UI hide the record and left "Add missing
    # records" unable to activate it for a newly hosted domain.
    if not any(row["key"] == "dkim" for row in rows):
        with write_session() as db:
            mail_domain = db.scalar(select(MailDomain).where(MailDomain.domain == domain))
            hosted_domain = db.scalar(select(Domain).where(Domain.domain == domain))
            key = db.scalar(select(DkimKey).where(DkimKey.domain == domain))
        if mail_domain is not None or hosted_domain is not None:
            selector = key.selector if key else dkim.DEFAULT_SELECTOR
            rows.append({
                "key": "dkim", "name": name(f"{selector}._domainkey"), "type": "TXT", "expected": [],
                "inactive": True,
                "detail": key.last_error if key and key.last_error else "DKIM signing needs activation",
            })
    rows.append({"key": "dmarc", "name": name("_dmarc"), "type": "TXT", "expected": []})
    return rows


def _local_rows(template: list[dict], current: dict) -> list[dict]:
    rows = []
    for row in template:
        existing = current.get((row["name"], row["type"]))
        actual = list(existing.get("values") or []) if existing else []
        if row["key"] == "dmarc":
            status = "present" if existing else "optional"
        elif row.get("inactive"):
            status = "inactive"
        else:
            # The apex TXT set is shared with unrelated values (site
            # verifications), so SPF is judged by its v=spf1 string only.
            compared = [value for value in actual if _is_spf(value)] if row["key"] == "spf" else actual
            if not compared:
                status = "missing"
            else:
                status = "present" if _canonical_values(row["type"], compared) == _canonical_values(
                    row["type"], row["expected"]) else "conflicting"
        rows.append({**{key: value for key, value in row.items() if key != "inactive"},
                     "actual": actual, "status": status})
    return rows


def _local_preview(domain: str, zone: str) -> tuple[dict, dict, list[dict]]:
    current = _record_map(zone)
    template = _template_rows(domain, label_within_zone(domain, zone))
    return {
        "domain": domain,
        "zone": zone,
        "managed": True,
        "provider": dnsprovider.provider_for_zone(zone),
        "mail_hostname": canonical_mail_hostname(),
        "records": _local_rows(template, current),
    }, current, template


def _resolves_here(name: str, local: set[str], deadline: float) -> bool:
    for rtype in ("A", "AAAA"):
        status, values = mail_local_delivery.public_lookup(name, rtype, deadline)
        for value in values if status == "ok" else []:
            try:
                if ipaddress.ip_address(value).compressed in local:
                    return True
            except ValueError:
                continue
    return False


def _authority(zone: str, current: dict, deadline: float) -> dict:
    """Does the public delegation of ``zone`` point at this server?"""
    status, values = mail_local_delivery.public_lookup(zone, "NS", deadline)
    public = sorted({value.lower().rstrip(".") for value in values})
    result = {"zone": zone, "public_nameservers": public}
    if status == "error":
        return {**result, "status": "unknown"}
    if status != "ok" or not public:
        return {**result, "status": "not_delegated" if status == "nxdomain" else "no_delegation"}
    from daemon import dnssetup
    ours = {value.lower().rstrip(".") for value in (current.get(("@", "NS"), {}).get("values") or [])}
    ours.update(name.lower().rstrip(".") for name in dnssetup.local_nameservers(zone))
    # A name inside the zone itself (ns1.<zone>, the default when no server
    # nameservers are configured) proves nothing by its name: a domain moved
    # from another host often keeps vanity nameservers with glue there. Only
    # configured server nameservers and Cloudflare's pair count by name.
    configured = {name.lower().rstrip(".") for name in dnssetup.local_nameservers()}
    ours = {name for name in ours if name in configured or not (name == zone or name.endswith(f".{zone}"))}
    cloudflare_row = dnsprovider.cloudflare_zone_row(zone)
    if cloudflare_row is not None and cloudflare_row.status == "active":
        ours.update(str(name).lower().rstrip(".") for name in (cloudflare_row.name_servers or []))
    foreign = [name for name in public if name not in ours]
    if foreign:
        # Nameservers named differently but answering from this server.
        local = mail_local_delivery.server_addresses()
        foreign = [name for name in foreign if not _resolves_here(name, local, deadline)]
    return {**result, "status": "elsewhere" if foreign else "here"}


def _not_authoritative_reason(authority: dict) -> str:
    zone = authority["zone"]
    if authority["status"] == "not_delegated":
        return f"{zone} does not exist in public DNS (it is not registered or not delegated)"
    if authority["status"] == "no_delegation":
        return f"public DNS has no nameservers for {zone}, so it is served as part of its parent domain"
    return f"public DNS delegates {zone} to {', '.join(authority['public_nameservers'])}"


def _public_status(row: dict, fqdn: str, domain: str, deadline: float, local) -> dict:
    """``local`` returns this server's public addresses (computed once)."""
    status, values = mail_local_delivery.public_lookup(fqdn, row["type"], deadline)
    result = {**{key: value for key, value in row.items() if key != "inactive"},
              "fqdn": fqdn, "actual": values, "source": "public"}
    if status == "error":
        return {**result, "status": "unknown", "detail": "Public DNS did not answer in time"}
    if row["key"] == "dmarc":
        found = any(dnsprovider.txt_text(value).strip().lower().startswith("v=dmarc1") for value in values)
        return {**result, "status": "present" if found else "optional"}
    if row.get("inactive"):
        return {**result, "status": "inactive"}
    if row["key"] == "mx":
        exchanges = []
        for value in values:
            preference, _, host = value.partition(" ")
            if preference.isdigit():
                exchanges.append((int(preference), host.strip().lower().rstrip(".")))
        if not exchanges:
            return {**result, "status": "missing"}
        best = min(preference for preference, _host in exchanges)
        hosts = sorted({host for preference, host in exchanges if preference == best and host})
        if hosts and any(_resolves_here(host, local(), deadline) for host in hosts):
            return {**result, "status": "present"}
        return {**result, "status": "conflicting",
                "detail": f"Mail for {domain} goes to {', '.join(hosts) or 'nowhere (null MX)'}, not to this server"}
    if row["key"] == "spf":
        values = [value for value in values if _is_spf(value)]
        if not values:
            return {**result, "status": "missing"}
        if len(values) > 1:
            return {**result, "status": "conflicting", "detail": "More than one SPF record is published"}
        if dnsprovider.txt_text(values[0]).lower().split() == dnsprovider.txt_text(row["expected"][0]).lower().split():
            return {**result, "status": "present"}
        return {**result, "status": "conflicting",
                "detail": "The published SPF record differs; make sure it allows this server (for example with mx)"}
    # DKIM: the public key must be the one this server signs with.
    def public_key(value: str) -> str | None:
        tags = dict(part.strip().split("=", 1) for part in dnsprovider.txt_text(value).split(";") if "=" in part)
        return "".join(tags.get("p", "").split()) or None

    expected = {public_key(value) for value in row["expected"]}
    published = {public_key(value) for value in values}
    if not values:
        return {**result, "status": "missing"}
    if expected & published - {None}:
        return {**result, "status": "present"}
    return {**result, "status": "conflicting",
            "detail": "The published DKIM key is not the key this server signs with"}


def _public_rows(domain: str, base: str, template: list[dict], deadline: float) -> list[dict]:
    addresses: list[set[str]] = []

    def local() -> set[str]:
        if not addresses:
            addresses.append(mail_local_delivery.server_addresses())
        return addresses[0]

    return [_public_status(row, base if row["name"] == "@" else f"{row['name']}.{base}", domain, deadline, local)
            for row in template if row["key"] in PUBLIC_KEYS]


def preview(domain: str, *, public_check: bool = True) -> dict:
    """Mail DNS readiness. ``public_check=False`` returns this server's zone
    only, which is what repair and the DMARC editor write to."""
    domain = validate_domain(domain)
    zone = find_managed_zone(domain)
    if not zone:
        state = {"domain": domain, "managed": False, "zone": None, "provider": None, "records": []}
        if not public_check:
            return state
        try:
            template = _template_rows(domain, "@")
            mail_hostname = canonical_mail_hostname()
        except ValidationError:
            return {**state, "authoritative": False, "source": "public"}
        deadline = time.monotonic() + PUBLIC_CHECK_SECONDS
        return {**state, "mail_hostname": mail_hostname, "authoritative": False, "source": "public",
                "detail": f"This server has no DNS zone for {domain}; the records below are what public DNS "
                          "returns. Publish them where the domain's DNS is hosted.",
                "records": _public_rows(domain, domain, template, deadline), "local_records": []}
    state, current, template = _local_preview(domain, zone)
    if not public_check:
        return state
    deadline = time.monotonic() + PUBLIC_CHECK_SECONDS
    authority = _authority(zone, current, deadline)
    if authority["status"] == "here":
        return {**state, "authoritative": True, "source": "local", "authority": authority}
    if authority["status"] == "unknown":
        return {**state, "authoritative": None, "source": "local", "authority": authority,
                "detail": "Public DNS could not be checked, so these are the records in this server's zone."}
    return {
        **state, "authoritative": False, "source": "public", "authority": authority,
        "detail": (f"This server's zone is not authoritative: {_not_authoritative_reason(authority)}. "
                   "The records below are what public DNS returns; change them where the domain's DNS is "
                   "hosted, or point the domain at this server's nameservers."),
        "records": _public_rows(domain, zone, template, deadline),
        "local_records": state["records"],
    }


def repair(domain: str, replace_conflicts: list[str] | None = None) -> dict:
    state = preview(domain, public_check=False)
    if not state["managed"]:
        raise ValidationError("This domain is not covered by a Boron-managed DNS zone")
    changed: list[str] = []
    dkim_row = next((row for row in state["records"] if row["key"] == "dkim"), None)
    if dkim_row and dkim_row["status"] == "inactive":
        activated = dkim.setup_dns_signing(domain)
        if not activated.get("signing_active"):
            raise ValidationError(activated.get("last_error") or "DKIM signing could not be activated")
        changed.append("dkim")
        state = preview(domain, public_check=False)

    approved = set(replace_conflicts or [])
    allowed = {row["key"] for row in state["records"]}
    if not approved <= allowed:
        raise ValidationError("Unknown mail DNS repair selection")
    with dnsprovider.batch_cluster_notifications():
        for row in state["records"]:
            if row["key"] == "dmarc" or row["status"] in ("present", "inactive"):
                continue
            if row["status"] == "conflicting" and row["key"] not in approved:
                continue
            values = row["expected"]
            if row["key"] == "spf":
                # Replace only the SPF string; keep the name's other TXT values.
                values = [value for value in row.get("actual", []) if not _is_spf(value)] + list(values)
            dnsprovider.upsert_record(state["zone"], row["name"], row["type"], values)
            changed.append(row["key"])
    return {"changed": changed, "preview": preview(domain)}


def set_dmarc(domain: str, policy: str, rua: str | None = None, subdomain_policy: str | None = None) -> dict:
    domain = validate_domain(domain)
    if policy not in {"none", "quarantine", "reject"}:
        raise ValidationError("DMARC policy must be none, quarantine, or reject")
    if subdomain_policy is not None and subdomain_policy not in {"none", "quarantine", "reject"}:
        raise ValidationError("DMARC subdomain policy must be none, quarantine, or reject")
    state = preview(domain, public_check=False)
    if not state["managed"]:
        raise ValidationError("This domain is not covered by a Boron-managed DNS zone")
    address = validate_email_address(rua) if rua else f"postmaster@{domain}"
    tags = ["v=DMARC1", f"p={policy}", f"rua=mailto:{address}"]
    if subdomain_policy:
        tags.append(f"sp={subdomain_policy}")
    record = "; ".join(tags)
    label = next(row["name"] for row in state["records"] if row["key"] == "dmarc")
    dnsprovider.upsert_record(state["zone"], label, "TXT", [_txt(record)])
    return {"domain": domain, "record": record, "preview": preview(domain)}


# Mail client settings. Every port requires TLS and the login is the full
# mailbox address; 465 and POP3 are installed by install.sh and the upgrade.
CLIENT_INCOMING = (
    {"protocol": "IMAP", "port": 993, "security": "SSL/TLS"},
    {"protocol": "IMAP", "port": 143, "security": "STARTTLS"},
    {"protocol": "POP3", "port": 995, "security": "SSL/TLS"},
    {"protocol": "POP3", "port": 110, "security": "STARTTLS"},
)
CLIENT_OUTGOING = (
    {"protocol": "SMTP", "port": 465, "security": "SSL/TLS"},
    {"protocol": "SMTP", "port": 587, "security": "STARTTLS"},
)


def client_settings() -> dict:
    try:
        hostname = canonical_mail_hostname()
    except ValidationError:
        hostname = None
    return {
        "hostname": hostname,
        "username": "full_address",
        "incoming": [dict(item) for item in CLIENT_INCOMING],
        "outgoing": [dict(item, authentication="password") for item in CLIENT_OUTGOING],
    }
