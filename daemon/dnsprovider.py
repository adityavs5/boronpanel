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

from daemon import dns_operations

from sqlalchemy import select

from shared.db import write_session
from shared.models import CloudflareZone

from daemon import cloudflare, cloudflare_accounts, powerdns
from daemon.cloudflare import CloudflareError
from daemon.powerdns import DEFAULT_TTL, PowerDnsError  # re-exported for callers

DnsError = (PowerDnsError, CloudflareError)
logger = logging.getLogger("borond.dnsprovider")


def _cluster_notify(zone: str, action: str = "upsert") -> None:
    """Queue a convergent full-zone update after the authoritative write.

    A queue-storage problem cannot roll PowerDNS back, so returning a false
    mutation failure would invite a duplicate user retry. Log it and let the
    explicit cluster Sync all repair the gap.
    """
    try:
        from daemon import dnscluster
        dnscluster.enqueue_zone(zone, action)
    except Exception:
        logger.exception("Could not queue DNS cluster update for %s", zone)


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
        cloudflare.upsert_record(zone, subdomain, rtype, values, ttl=ttl, proxied=proxied, zone_id=row.cf_zone_id)
    else:
        powerdns.upsert_record(zone, subdomain, rtype, values, ttl=ttl)
        _cluster_notify(zone)


@dns_operations.serialized
def delete_record(zone: str, subdomain: str, rtype: str) -> None:
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        cloudflare.delete_record(zone, subdomain, rtype, zone_id=row.cf_zone_id)
    else:
        powerdns.delete_record(zone, subdomain, rtype)
        _cluster_notify(zone)


def list_records(zone: str) -> list[dict]:
    row = cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        return cloudflare.list_records(zone, zone_id=row.cf_zone_id)
    return powerdns.list_records(zone)
