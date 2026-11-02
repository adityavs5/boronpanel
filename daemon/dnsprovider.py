"""DNS provider dispatch layer (docs/PLAN-cloudflare.md SS1.2).

Exposes the exact powerdns.py primitive surface plus provider_for_zone();
each call routes to daemon/powerdns.py or daemon/cloudflare.py based on
the zone's CloudflareZone row AND its status:

  - no row, or status='pending'  -> PowerDNS ("local"). While pending the
    internet still resolves via ns1/ns2, so PowerDNS stays authoritative
    and every panel write lands there; the pending->active transition does
    a full one-shot resync PowerDNS->Cloudflare (cloudflare_ops.py), so
    edits made during the pending window converge at that moment.
  - status='active'              -> Cloudflare.

Caller modules (handlers_dns, nameservers, dkim, handlers_domain, parked,
backup) import THIS module instead of powerdns; call sites are otherwise
unchanged. Cleanup paths that used to tolerate powerdns.PowerDnsError
catch DnsError here -- a tuple covering both backends' error types, so
best-effort deletes stay best-effort whichever backend serves the zone.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
import threading

from daemon import dns_operations

import dns.exception
import dns.rdataclass
import dns.rdatatype
import dns.tokenizer
from dns.rdtypes.ANY.TXT import TXT
from sqlalchemy import select

from shared.db import write_session
from shared.models import CloudflareZone
from shared.validation import ValidationError

from daemon import cloudflare, cloudflare_accounts, powerdns
from daemon.cloudflare import CloudflareError
from daemon.powerdns import DEFAULT_TTL, PowerDnsError  # re-exported for callers

DnsError = (PowerDnsError, CloudflareError)
logger = logging.getLogger("borond.dnsprovider")
_batch_state = threading.local()

# RFC 1035 SS3.3: a TXT character-string carries at most 255 octets. A
# longer value (a 2048-bit DKIM key is ~410) must be published as several
# strings, which resolvers concatenate. PowerDNS accepts one oversized
# string, but zone-file parsers (dnspython in the advanced editor and the
# snapshot restore validator) reject it, so every write is split here.
TXT_STRING_OCTETS = 255
# Placeholder MNAME from the installer's default-soa-content. Zones created
# by Boron replace it with their primary nameserver (set_soa_primary).
PLACEHOLDER_SOA_MNAME = "ns1.boron.invalid."


def _lenient_unescape(inner: str) -> bytes:
    out = bytearray()
    raw = inner.encode("utf-8", "surrogateescape")
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == 0x5C and i + 1 < len(raw):  # backslash
            digits = raw[i + 1:i + 4]
            if len(digits) == 3 and digits.isdigit() and int(digits) <= 255:
                out.append(int(digits))
                i += 4
                continue
            out.append(raw[i + 1])
            i += 2
            continue
        out.append(char)
        i += 1
    return bytes(out)


def txt_strings(value: str) -> list[bytes]:
    """The character-strings of one TXT value in presentation format.

    Quoted and bare words are each one string (zone-file grammar). Content
    that grammar cannot read -- legacy rows stored before validation, such
    as a raw newline inside quotes -- is read leniently as one string so it
    can still be displayed, compared and rewritten instead of failing.
    """
    if not isinstance(value, str):
        raise ValidationError("TXT values must be text")
    try:
        tokenizer = dns.tokenizer.Tokenizer(value)
        strings = []
        while True:
            token = tokenizer.get()
            if token.is_eof():
                break
            if not (token.is_quoted_string() or token.is_identifier()):
                raise dns.exception.SyntaxError("unexpected token")
            strings.append(token.unescape_to_bytes().value)
        return strings
    except dns.exception.DNSException:
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
            return [_lenient_unescape(stripped[1:-1])]
        return [stripped.encode("utf-8", "surrogateescape")]


def txt_bytes(value: str) -> bytes:
    """The logical TXT data: its character-strings concatenated."""
    return b"".join(txt_strings(value))


def txt_text(value: str) -> str:
    """The logical TXT data as text, for comparisons and display."""
    return txt_bytes(value).decode("utf-8", "replace")


def txt_presentation(data: bytes) -> str:
    """Canonical PowerDNS content: escaped strings of at most 255 octets."""
    chunks = [data[i:i + TXT_STRING_OCTETS] for i in range(0, len(data), TXT_STRING_OCTETS)] or [b""]
    return TXT(dns.rdataclass.IN, dns.rdatatype.TXT, chunks).to_text()


def normalize_txt(value: str) -> str:
    return txt_presentation(txt_bytes(value))


def provider_values(rtype: str, values: list[str], *, cloudflare_zone: bool) -> list[str]:
    if rtype != "TXT":
        return list(values)
    if not cloudflare_zone:
        return [normalize_txt(value) for value in values]
    # Cloudflare stores TXT as one unquoted string and splits it itself.
    result = []
    for value in values:
        try:
            result.append(cloudflare._quote_txt(txt_bytes(value).decode("utf-8")))
        except UnicodeDecodeError:
            raise ValidationError("Cloudflare TXT records must contain UTF-8 text") from None
    return result


def soa_with_primary(content: str, primary: str) -> str | None:
    """SOA content with its MNAME replaced, or None when it is malformed."""
    fields = str(content).split()
    if len(fields) != 7:
        return None
    fields[0] = primary if primary.endswith(".") else f"{primary}."
    return " ".join(fields)


@dns_operations.serialized
def set_soa_primary(zone: str, primary: str, *, only_placeholder: bool = False) -> bool:
    """Point a local zone's SOA MNAME at its primary nameserver.

    PowerDNS fills a new zone's SOA from default-soa-content, which cannot
    name a per-zone nameserver. Returns whether the SOA was rewritten.
    """
    data = powerdns.get_zone(zone)
    soa = next((row for row in data.get("rrsets", []) if row.get("type") == "SOA"), None)
    if soa is None or not soa.get("records"):
        return False
    current = soa["records"][0]["content"]
    if only_placeholder and current.split()[:1] != [PLACEHOLDER_SOA_MNAME]:
        return False
    updated = soa_with_primary(current, primary)
    if updated is None or updated == current:
        return False
    powerdns.apply_rrset_changes(zone, [{
        "name": soa["name"], "type": "SOA", "ttl": soa.get("ttl", DEFAULT_TTL), "changetype": "REPLACE",
        "records": [{"content": updated, "disabled": False}],
    }])
    _cluster_notify(zone)
    return True


def _cluster_notify(zone: str, action: str = "upsert") -> None:
    """Queue a convergent full-zone update after the authoritative write.

    A queue-storage problem cannot roll PowerDNS back, so returning a false
    mutation failure would invite a duplicate user retry. Log it and let the
    explicit cluster Sync all repair the gap.
    """
    if getattr(_batch_state, "depth", 0):
        pending = getattr(_batch_state, "pending", {})
        # Delete wins over an earlier upsert for the same logical batch.
        pending[zone] = "delete" if action == "delete" else pending.get(zone, action)
        _batch_state.pending = pending
        return
    try:
        from daemon import dnscluster, dnssetup
        if dnssetup.current_mode() != "cluster":
            return
        dnscluster.enqueue_zone(zone, action)
    except Exception:
        logger.exception("Could not queue DNS cluster update for %s", zone)


@contextmanager
def batch_cluster_notifications():
    """Coalesce a logical multi-record mutation into one cluster sync.

    Authoritative provider writes still happen immediately.  Only the
    downstream full-zone notification is deferred, preventing a peer from
    observing a half-repaired mail template.
    """
    outer = getattr(_batch_state, "depth", 0) == 0
    if outer:
        _batch_state.pending = {}
    _batch_state.depth = getattr(_batch_state, "depth", 0) + 1
    try:
        yield
    finally:
        _batch_state.depth -= 1
        if outer:
            pending = _batch_state.pending
            _batch_state.pending = {}
            for zone, action in pending.items():
                _cluster_notify(zone, action)


def cloudflare_zone_row(zone: str) -> CloudflareZone | None:
    """The zone's CloudflareZone row as a detached snapshot, or None."""
    with write_session() as session:
        row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone))
        if row is not None:
            session.expunge(row)
        return row


def provider_for_zone(zone: str) -> str:
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        return "cloudflare"
    return "local"


def proxied_allowed() -> bool:
    """Gate for the record-level `proxied` flag (plan SS1.7, Phase 2 feature
    2): True only once the real-IP rails (OLS trusted-header config + fresh
    Cloudflare ranges + fail2ban ignoreip) all report configured -- i.e.
    cf.rails_status is ready. Enforced at write time in upsert_record, so a
    proxied record can never be written before real visitor IPs are restored
    and CF edges are protected from fail2ban. The foolproof gate, not
    documentation."""
    from daemon import cloudflare_ops  # lazy: avoids an import cycle

    try:
        return bool(cloudflare_ops.rails_status({}).get("ready"))
    except Exception:
        return False


def zone_exists(zone: str) -> bool:
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        return True  # our own row is authoritative; no need for a live CF call
    return powerdns.zone_exists(zone)


@dns_operations.serialized
def create_zone(zone: str, ns_records: list[str]) -> dict:
    """New zones are always created in PowerDNS: it is the local provider,
    the fallback, and the revert target (plan SS0/SS1.9). Moving a zone to
    Cloudflare is a separate, explicit lifecycle op (cf.zone_enable)."""
    result = powerdns.create_zone(zone, ns_records)
    if ns_records:
        # The zone already serves; an SOA rewrite failure must not report
        # a failed creation (the upgrade reconcile repairs it later).
        try:
            set_soa_primary(zone, ns_records[0])
        except Exception:
            logger.exception("Could not set the SOA primary nameserver for %s", zone)
    _cluster_notify(zone)
    return result


@dns_operations.serialized
def delete_zone(zone: str) -> None:
    """Deletes the zone from every backend that has it: the Cloudflare
    zone + row when one exists (any status), and the PowerDNS zone (which
    is kept around as the revert target even while Cloudflare is active).
    Best-effort on the Cloudflare side -- an unreachable API must not
    block zone deletion/account termination; powerdns.delete_zone already
    tolerates 404s itself."""
    row = cloudflare_zone_row(zone)
    if row is not None:
        cf_account_pk = getattr(row, "cf_account_id", None)
        try:
            with cloudflare.use_token(cloudflare_accounts.token_for_id(cf_account_pk)):
                cloudflare.delete_zone(zone, zone_id=row.cf_zone_id)
        except CloudflareError:
            pass
        with write_session() as session:
            db_row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone))
            if db_row is not None:
                session.delete(db_row)
            if cf_account_pk is not None:
                session.flush()
                cloudflare_accounts._sync_zone_count(session, cf_account_pk)
    powerdns.delete_zone(zone)
    _cluster_notify(zone, "delete")


def get_zone(zone: str) -> dict:
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        with cloudflare.use_token(cloudflare_accounts.token_for_id(getattr(row, "cf_account_id", None))):
            return cloudflare.get_zone(zone, zone_id=row.cf_zone_id)
    return powerdns.get_zone(zone)


@dns_operations.serialized
def upsert_record(
    zone: str,
    subdomain: str,
    rtype: str,
    values: list[str],
    ttl: int = DEFAULT_TTL,
    proxied: bool = False,
) -> None:
    if not proxied_allowed():
        proxied = False  # accepted end-to-end, forced off until the Phase 2 rails exist
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        values = provider_values(rtype, values, cloudflare_zone=True)
        with cloudflare.use_token(cloudflare_accounts.token_for_id(getattr(row, "cf_account_id", None))):
            cloudflare.upsert_record(zone, subdomain, rtype, values, ttl=ttl, proxied=proxied, zone_id=row.cf_zone_id)
    else:
        values = provider_values(rtype, values, cloudflare_zone=False)
        powerdns.upsert_record(zone, subdomain, rtype, values, ttl=ttl)
        _cluster_notify(zone)


@dns_operations.serialized
def delete_record(zone: str, subdomain: str, rtype: str) -> None:
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        with cloudflare.use_token(cloudflare_accounts.token_for_id(getattr(row, "cf_account_id", None))):
            cloudflare.delete_record(zone, subdomain, rtype, zone_id=row.cf_zone_id)
    else:
        powerdns.delete_record(zone, subdomain, rtype)
        _cluster_notify(zone)


def list_records(zone: str) -> list[dict]:
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        with cloudflare.use_token(cloudflare_accounts.token_for_id(getattr(row, "cf_account_id", None))):
            return cloudflare.list_records(zone, zone_id=row.cf_zone_id)
    return powerdns.list_records(zone)
