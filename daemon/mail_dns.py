"""Provider-neutral mail DNS template, preview and repair operations."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from daemon import dkim, dnsprovider
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone
from shared.config import settings
from shared.db import write_session
from shared.models import DkimKey, MailDomain
from shared.validation import ValidationError, validate_domain, validate_email_address


def canonical_mail_hostname() -> str:
    value = settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname
    if not value:
        raise ValidationError("Configure a canonical mail hostname in Server Setup first")
    return validate_domain(value)


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


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
        DesiredRecord("spf", "@", "TXT", (_quote("v=spf1 mx a ~all"),)),
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
                (_quote(dkim_txt_value(zone, key.selector)),),
            ))
    return records


def _canonical_values(rtype: str, values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result = []
    for value in values:
        clean = value.strip()
        if rtype == "TXT":
            clean = _unquote(clean)
        elif rtype in {"MX", "CNAME", "NS"}:
            clean = clean.lower().rstrip(".")
        result.append(clean)
    return tuple(sorted(result))


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


def preview(domain: str) -> dict:
    domain = validate_domain(domain)
    zone = find_managed_zone(domain)
    if not zone:
        return {"domain": domain, "managed": False, "zone": None, "provider": None, "records": []}
    label_prefix = label_within_zone(domain, zone)
    current = _record_map(zone)
    rows = []
    for desired in default_records(domain):
        label = desired.label if label_prefix == "@" else (
            label_prefix if desired.label == "@" else f"{desired.label}.{label_prefix}"
        )
        existing = current.get((label, desired.rtype))
        status = "missing"
        actual: list[str] = []
        if existing:
            actual = list(existing.get("values") or [])
            status = "present" if _canonical_values(desired.rtype, actual) == _canonical_values(
                desired.rtype, desired.values
            ) else "conflicting"
        rows.append({
            "key": desired.key,
            "name": label,
            "type": desired.rtype,
            "expected": list(desired.values),
            "actual": actual,
            "status": status,
        })

    # A mail domain must always expose DKIM in readiness, even when an older
    # installation has a missing/inactive key. Hiding the row made the UI say
    # nothing about DKIM and left "Add missing records" unable to repair it.
    if not any(row["key"] == "dkim" for row in rows):
        with write_session() as db:
            mail_domain = db.scalar(select(MailDomain).where(MailDomain.domain == domain))
            key = db.scalar(select(DkimKey).where(DkimKey.domain == domain))
        if mail_domain is not None:
            selector = key.selector if key else dkim.DEFAULT_SELECTOR
            label = f"{selector}._domainkey"
            if label_prefix != "@":
                label = f"{label}.{label_prefix}"
            existing = current.get((label, "TXT"))
            rows.append({
                "key": "dkim", "name": label, "type": "TXT", "expected": [],
                "actual": list(existing.get("values") or []) if existing else [],
                "status": "inactive",
                "detail": (key.last_error if key and key.last_error else
                           "DKIM signing needs activation"),
            })

    dmarc_label = "_dmarc" if label_prefix == "@" else f"_dmarc.{label_prefix}"
    dmarc = current.get((dmarc_label, "TXT"))
    rows.append({
        "key": "dmarc", "name": dmarc_label, "type": "TXT", "expected": [],
        "actual": list(dmarc.get("values") or []) if dmarc else [],
        "status": "present" if dmarc else "optional",
    })
    return {
        "domain": domain,
        "zone": zone,
        "managed": True,
        "provider": dnsprovider.provider_for_zone(zone),
        "mail_hostname": canonical_mail_hostname(),
        "records": rows,
    }


def repair(domain: str, replace_conflicts: list[str] | None = None) -> dict:
    state = preview(domain)
    if not state["managed"]:
        raise ValidationError("This domain is not covered by a Boron-managed DNS zone")
    changed: list[str] = []
    dkim_row = next((row for row in state["records"] if row["key"] == "dkim"), None)
    if dkim_row and dkim_row["status"] == "inactive":
        activated = dkim.setup_dns_signing(domain)
        if not activated.get("signing_active"):
            raise ValidationError(activated.get("last_error") or "DKIM signing could not be activated")
        changed.append("dkim")
        state = preview(domain)

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
            dnsprovider.upsert_record(state["zone"], row["name"], row["type"], row["expected"])
            changed.append(row["key"])
    return {"changed": changed, "preview": preview(domain)}


def set_dmarc(domain: str, policy: str, rua: str | None = None, subdomain_policy: str | None = None) -> dict:
    domain = validate_domain(domain)
    if policy not in {"none", "quarantine", "reject"}:
        raise ValidationError("DMARC policy must be none, quarantine, or reject")
    if subdomain_policy is not None and subdomain_policy not in {"none", "quarantine", "reject"}:
        raise ValidationError("DMARC subdomain policy must be none, quarantine, or reject")
    state = preview(domain)
    if not state["managed"]:
        raise ValidationError("This domain is not covered by a Boron-managed DNS zone")
    address = validate_email_address(rua) if rua else f"postmaster@{domain}"
    tags = ["v=DMARC1", f"p={policy}", f"rua=mailto:{address}"]
    if subdomain_policy:
        tags.append(f"sp={subdomain_policy}")
    record = "; ".join(tags)
    label = next(row["name"] for row in state["records"] if row["key"] == "dmarc")
    dnsprovider.upsert_record(state["zone"], label, "TXT", [_quote(record)])
    return {"domain": domain, "record": record, "preview": preview(domain)}
