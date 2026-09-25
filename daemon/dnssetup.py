"""Explicit DNS operating mode, staged zone moves, and authority diagnostics."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from daemon import cloudflare, cloudflare_ops, dnscluster, dnsprovider, powerdns
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import CloudflareAccount, CloudflareZone, DnsClusterOutbox, DnsClusterPeer, DnsServerSettings, DnsZone, DnsZoneMigration
from shared.validation import ValidationError, validate_domain

MODES = {"cloudflare", "local", "cluster"}


def current_mode() -> str:
    with write_session() as db:
        row = db.get(DnsServerSettings, 1)
        if row is not None:
            return row.mode
        peers = db.scalar(select(func.count()).select_from(DnsClusterPeer).where(DnsClusterPeer.enabled.is_(True))) or 0
    if settings.default_dns_provider == "cloudflare":
        return "cloudflare"
    return "cluster" if peers else "local"


def local_nameservers(zone: str | None = None) -> list[str]:
    with write_session() as db:
        row = db.get(DnsServerSettings, 1)
        configured = list(row.local_nameservers or []) if row else []
    if configured:
        return configured
    if zone:
        zone = validate_domain(zone)
        return [f"ns1.{zone}", f"ns2.{zone}"]
    return []


def _validate_nameservers(values: object) -> list[str]:
    if values in (None, []):
        return []
    if not isinstance(values, list) or not 2 <= len(values) <= 8:
        raise ValidationError("local DNS needs between two and eight nameserver hostnames")
    result = []
    for value in values:
        name = validate_domain(str(value).rstrip("."))
        if name not in result:
            result.append(name)
    if len(result) < 2:
        raise ValidationError("local DNS needs at least two distinct nameserver hostnames")
    return result


def _provider_counts() -> dict:
    with write_session() as db:
        total = db.scalar(select(func.count()).select_from(DnsZone)) or 0
        active_cf = db.scalar(select(func.count()).select_from(CloudflareZone).where(CloudflareZone.status == "active")) or 0
        pending_cf = db.scalar(select(func.count()).select_from(CloudflareZone).where(CloudflareZone.status == "pending")) or 0
        peers = db.scalar(select(func.count()).select_from(DnsClusterPeer).where(DnsClusterPeer.enabled.is_(True))) or 0
        queued = db.scalar(select(func.count()).select_from(DnsClusterOutbox)) or 0
    return {
        "zones": total, "local_zones": max(0, total - active_cf),
        "cloudflare_active": active_cf, "cloudflare_pending": pending_cf,
        "cluster_peers": peers, "cluster_queue": queued,
    }


def get_settings(_params: dict | None = None) -> dict:
    with write_session() as db:
        row = db.get(DnsServerSettings, 1)
        diagnostics = dict(row.last_diagnostics or {}) if row else {}
    with write_session() as db:
        zones = db.scalars(select(DnsZone.zone).order_by(DnsZone.zone)).all()
        cloudflare = {
            row.zone: {"status": row.status, "nameservers": list(row.name_servers or [])}
            for row in db.scalars(select(CloudflareZone)).all()
        }
        migrations = {
            row.zone: {
                "source": row.source, "target": row.target, "state": row.state,
                "expected_nameservers": list(row.expected_nameservers or []),
                "last_verification": dict(row.last_verification or {}),
            }
            for row in db.scalars(select(DnsZoneMigration)).all()
        }
    return {
        "mode": current_mode(), "local_nameservers": local_nameservers(),
        "counts": _provider_counts(), "last_diagnostics": diagnostics,
        "zones": [{
            "zone": zone,
            "provider": "cloudflare" if cloudflare.get(zone, {}).get("status") == "active" else (
                "cluster" if migrations.get(zone, {}).get("target") == "cluster" and migrations[zone].get("state") == "completed" else "local"
            ),
            "cloudflare_status": cloudflare.get(zone, {}).get("status"),
            "nameservers": cloudflare.get(zone, {}).get("nameservers") or local_nameservers(zone),
            "migration": migrations.get(zone),
        } for zone in zones],
    }


def preview_mode(params: dict) -> dict:
    mode = str(params.get("mode") or "").lower()
    if mode not in MODES:
        raise ValidationError("DNS mode must be Cloudflare, local, or cluster")
    nameservers = _validate_nameservers(params.get("local_nameservers")) if mode in {"local", "cluster"} else local_nameservers()
    counts = _provider_counts()
    blockers = []
    if mode == "cloudflare":
        with write_session() as db:
            accounts = db.scalar(select(func.count()).select_from(CloudflareAccount).where(CloudflareAccount.active.is_(True))) or 0
        if not accounts and not settings.cloudflare_api_token:
            blockers.append("Connect and verify a Cloudflare account before selecting Cloudflare mode.")
    if mode == "cluster" and counts["cluster_peers"] == 0:
        blockers.append("Add and test at least one DNS cluster peer before selecting cluster mode.")
    return {
        "current_mode": current_mode(), "requested_mode": mode,
        "local_nameservers": nameservers, "blockers": blockers,
        "existing_zones_unchanged": counts["zones"],
        "message": "This changes the default for new zones. Existing zones move only through an explicit staged migration.",
    }


def set_mode(params: dict) -> dict:
    preview = preview_mode(params)
    if preview["blockers"]:
        raise ValidationError(" ".join(preview["blockers"]))
    with write_session() as db:
        row = db.get(DnsServerSettings, 1)
        if row is None:
            row = DnsServerSettings(id=1)
            db.add(row)
        row.mode = preview["requested_mode"]
        if preview["requested_mode"] in {"local", "cluster"}:
            row.local_nameservers = preview["local_nameservers"]
    return get_settings({})


def _rrset_map(records: list[dict]) -> dict[tuple[str, str], tuple[int, tuple[str, ...]]]:
    return {
        (str(row.get("name", "")).rstrip("."), str(row.get("type", ""))):
        (int(row.get("ttl") or 3600), tuple(sorted(str(v) for v in row.get("values", []))))
        for row in records if row.get("type") != "SOA"
    }


def preview_zone_migration(params: dict) -> dict:
    zone = validate_domain(params.get("zone"))
    target = str(params.get("target") or "").lower()
    if target not in MODES:
        raise ValidationError("target must be Cloudflare, local, or cluster")
    with write_session() as db:
        if db.scalar(select(DnsZone.id).where(DnsZone.zone == zone)) is None:
            raise ValidationError("DNS zone is not managed by this server")
        cf = db.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone))
        if cf is not None:
            db.expunge(cf)
    with write_session() as db:
        prior = db.scalar(select(DnsZoneMigration).where(DnsZoneMigration.zone == zone))
        prior_target = prior.target if prior is not None and prior.state == "completed" else None
    source = "cloudflare" if cf is not None and cf.status == "active" else ("cluster" if prior_target == "cluster" else "local")
    current_records = dnsprovider.list_records(zone)
    target_records = []
    if target in {"local", "cluster"} and powerdns.zone_exists(zone):
        target_records = powerdns.list_records(zone)
    current_map, target_map = _rrset_map(current_records), _rrset_map(target_records)
    diff = {
        "create": sorted([f"{name} {rtype}" for name, rtype in current_map.keys() - target_map.keys()]),
        "update": sorted([f"{name} {rtype}" for name, rtype in current_map.keys() & target_map.keys() if current_map[(name, rtype)] != target_map[(name, rtype)]]),
        "remove": sorted([f"{name} {rtype}" for name, rtype in target_map.keys() - current_map.keys()]),
    }
    warnings = ["Registrar nameserver or DS changes are external and are never made automatically."]
    if any(row.get("type") == "CAA" for row in current_records):
        warnings.append("CAA records are included and must continue to authorize the chosen certificate authority.")
    warnings.append("DNSSEC signing and parent DS changes require an explicit provider/registrar workflow.")
    return {
        "zone": zone, "source": source, "target": target,
        "already_on_target": source == target,
        "record_count": len(current_records), "diff": diff, "warnings": warnings,
        "switch_condition": "Effective writes switch only after the destination is staged and authoritative delegation is verified.",
    }


def migrate_zone(params: dict) -> dict:
    preview = preview_zone_migration(params)
    if not bool(params.get("confirm")):
        raise ValidationError("zone migration requires confirm=true after reviewing the preview")
    zone, target = preview["zone"], preview["target"]
    if preview["already_on_target"]:
        raise ValidationError("zone already uses the requested DNS provider")
    with write_session() as db:
        migration = db.scalar(select(DnsZoneMigration).where(DnsZoneMigration.zone == zone))
        if migration is not None and migration.state not in {"completed", "cancelled", "failed"}:
            if migration.target == target:
                return {"preview": preview, "state": migration.state, "migration_id": migration.id}
            raise ValidationError("another staged migration already exists for this zone")
    if target == "cloudflare":
        result = cloudflare_ops.zone_enable({"domain": zone})
        state = "waiting_for_delegation"
        expected = list(result.get("name_servers") or [])
    else:
        with write_session() as db:
            cf = db.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone))
            if cf is not None:
                db.expunge(cf)
        # Stage the local copy while Cloudflare stays effective. Deleting the
        # Cloudflare zone before registrar delegation changes would cause an
        # outage, so final cleanup happens only in verify_zone_migration.
        if cf is not None and cf.status == "active":
            with cloudflare.use_token(cloudflare_ops._zone_token(cf)):
                cloudflare_ops._resync_cloudflare_to_powerdns(zone, cf.cf_zone_id)
            result = {"domain": zone, "status": "local_copy_staged"}
        else:
            result = {"domain": zone, "status": "already_local"}
        expected = local_nameservers(zone)
        state = "waiting_for_delegation" if preview["source"] == "cloudflare" else (
            "waiting_for_cluster" if target == "cluster" else "staged"
        )
    if target == "cluster":
        queued = dnscluster.enqueue_zone(zone)
        result = {**result, "cluster": queued}
    with write_session() as db:
        migration = db.scalar(select(DnsZoneMigration).where(DnsZoneMigration.zone == zone))
        if migration is None:
            migration = DnsZoneMigration(zone=zone)
            db.add(migration)
        migration.source = preview["source"]
        migration.target = target
        migration.state = state
        migration.expected_nameservers = expected
        migration.preview = preview
        migration.last_verification = {}
        db.flush()
        migration_id = migration.id
    return {"preview": preview, "result": result, "state": state, "migration_id": migration_id}


def verify_zone_migration(params: dict) -> dict:
    zone = validate_domain(params.get("zone"))
    with write_session() as db:
        migration = db.scalar(select(DnsZoneMigration).where(DnsZoneMigration.zone == zone))
        if migration is None:
            raise ValidationError("no staged DNS migration exists for this zone")
        db.expunge(migration)
    if migration.state == "completed":
        return {"zone": zone, "target": migration.target, "state": "completed", "ready": True}
    observed_result = run(["dig", "+short", "NS", zone], timeout=20)
    observed = sorted(line.rstrip(".").lower() for line in observed_result.stdout.splitlines() if line.strip())
    expected = sorted(str(item).rstrip(".").lower() for item in migration.expected_nameservers or [])
    delegation_ready = bool(expected) and set(expected).issubset(observed)
    cluster_ready = True
    if migration.target == "cluster":
        with write_session() as db:
            pending = db.scalar(select(func.count()).select_from(DnsClusterOutbox).where(DnsClusterOutbox.zone == zone)) or 0
        cluster_ready = pending == 0
    provider_ready = delegation_ready
    detail = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "observed_nameservers": observed,
              "expected_nameservers": expected, "delegation_ready": delegation_ready, "cluster_ready": cluster_ready}
    if migration.target == "cloudflare":
        cf = cloudflare_ops.zone_status({"domain": zone, "check_now": True})
        provider_ready = cf.get("status") == "active"
        detail["cloudflare_status"] = cf.get("status")
    elif migration.source != "cloudflare":
        provider_ready = True  # local -> cluster retains the same delegation
    ready = provider_ready and cluster_ready
    if ready and migration.source == "cloudflare" and migration.target in {"local", "cluster"}:
        detail["cloudflare_cleanup"] = cloudflare_ops.zone_disable({"domain": zone, "force": False})
    with write_session() as db:
        row = db.scalar(select(DnsZoneMigration).where(DnsZoneMigration.zone == zone))
        row.last_verification = detail
        row.state = "completed" if ready else ("waiting_for_cluster" if provider_ready and not cluster_ready else "waiting_for_delegation")
    return {"zone": zone, "target": migration.target, "state": "completed" if ready else row.state,
            "ready": ready, "verification": detail}


def diagnostics(params: dict | None = None) -> dict:
    params = params or {}
    zone = validate_domain(params["zone"]) if params.get("zone") else None
    service = run(["systemctl", "is-active", "pdns"], timeout=10)
    sockets = run(["ss", "-H", "-lntu"], timeout=10)
    listening_53 = [line for line in sockets.stdout.splitlines() if ":53 " in line or line.rstrip().endswith(":53")]
    result = {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": current_mode(), "powerdns_active": service.stdout.strip() == "active",
        "authoritative_tcp_udp_53": bool(listening_53), "listeners": listening_53[:20],
        "counts": _provider_counts(), "zone": zone,
    }
    if zone:
        ns = run(["dig", "+short", "NS", zone], timeout=20)
        soa = run(["dig", "+short", "SOA", zone], timeout=20)
        result["delegation"] = {
            "nameservers": sorted(line.rstrip(".") for line in ns.stdout.splitlines() if line),
            "soa": soa.stdout.strip() or None,
            "resolved": bool(ns.stdout.strip() and soa.stdout.strip()),
        }
    mode = result["mode"]
    with write_session() as db:
        row = db.get(DnsServerSettings, 1)
        if row is None:
            row = DnsServerSettings(id=1, mode=mode)
            db.add(row)
        row.last_diagnostics = result
    return result
