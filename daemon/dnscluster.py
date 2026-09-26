"""Durable full-zone replication to Boron, DirectAdmin and cPanel peers."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select

from daemon import appcrypto, powerdns
from shared.db import write_session
from shared.models import (
    CloudflareZone,
    DnsClusterOutbox,
    DnsClusterPeer,
    DnsClusterReceipt,
    DnsClusterZoneState,
    DnsZone,
)
from shared.validation import ValidationError, validate_domain

logger = logging.getLogger("borond.dnscluster")
PEER_TYPES = {"boron", "directadmin", "cpanel"}
DIRECTIONS = {"push", "receive", "bidirectional"}
RECORD_TYPES = {"A", "AAAA", "CAA", "CNAME", "MX", "NS", "PTR", "SOA", "SRV", "TXT"}
_transport: httpx.BaseTransport | None = None
_worker_started = False


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


def _peer_dict(peer: DnsClusterPeer) -> dict:
    return {
        "id": peer.id, "name": peer.name, "peer_type": peer.peer_type,
        "endpoint": peer.endpoint, "username": peer.username,
        "verify_tls": peer.verify_tls, "enabled": peer.enabled,
        "direction": peer.direction, "zones": list(peer.zones or []),
        "conflict_policy": peer.conflict_policy,
        "status": peer.status, "last_success_at": _iso(peer.last_success_at),
        "last_error": peer.last_error, "created_at": _iso(peer.created_at),
    }


def _clean_endpoint(value: object, peer_type: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError("Endpoint must be an HTTPS URL without embedded credentials")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValidationError("Endpoint must contain only the server URL and optional port")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("Endpoint port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValidationError("Endpoint port is invalid")
    defaults = {"directadmin": 2222, "cpanel": 2087}
    if port is None and peer_type in defaults:
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        raw = f"https://{host}:{defaults[peer_type]}"
    return raw


def _validate_peer(params: dict, *, existing: DnsClusterPeer | None = None) -> dict:
    peer_type = str(params.get("peer_type", existing.peer_type if existing else "")).lower()
    if peer_type not in PEER_TYPES:
        raise ValidationError("Peer type must be Boron, DirectAdmin, or cPanel/WHM")
    name = str(params.get("name", existing.name if existing else "")).strip()
    if not name or len(name) > 100:
        raise ValidationError("Peer name is required and must be at most 100 characters")
    endpoint = _clean_endpoint(params.get("endpoint", existing.endpoint if existing else ""), peer_type)
    username = str(params.get("username", existing.username if existing else "") or "").strip() or None
    if peer_type in {"directadmin", "cpanel"} and not username:
        raise ValidationError("An API username is required for this peer type")
    credential = params.get("credential")
    if credential is not None and (not isinstance(credential, str) or not credential or len(credential) > 4096):
        raise ValidationError("Credential must be a non-empty token of at most 4096 characters")
    if existing is None and credential is None:
        credential = "bdc_" + secrets.token_urlsafe(32) if peer_type == "boron" else None
    if existing is None and credential is None:
        raise ValidationError("An API token or login key is required")
    direction = str(params.get("direction", existing.direction if existing else "push")).lower()
    if direction not in DIRECTIONS:
        raise ValidationError("Direction must be push, receive, or bidirectional")
    if peer_type != "boron" and direction != "push":
        raise ValidationError("DirectAdmin and cPanel peers support push replication only")
    raw_zones = params.get("zones", existing.zones if existing else [])
    if not isinstance(raw_zones, list) or len(raw_zones) > 10000:
        raise ValidationError("Zones must be a list of at most 10,000 domain names")
    zones = sorted({validate_domain(value) for value in raw_zones})
    if direction in {"receive", "bidirectional"} and not zones:
        raise ValidationError("Receiving peers require an explicit zone allowlist")
    return {
        "name": name, "peer_type": peer_type, "endpoint": endpoint, "username": username,
        "credential": credential, "verify_tls": bool(params.get("verify_tls", existing.verify_tls if existing else True)),
        "enabled": bool(params.get("enabled", existing.enabled if existing else True)),
        "direction": direction, "zones": zones, "conflict_policy": "reject_stale",
    }


def list_peers(_params: dict) -> dict:
    with write_session() as db:
        peers = db.scalars(select(DnsClusterPeer).order_by(DnsClusterPeer.name)).all()
        jobs = db.scalars(select(DnsClusterOutbox).order_by(DnsClusterOutbox.updated_at.desc()).limit(100)).all()
        states = db.scalars(select(DnsClusterZoneState).order_by(DnsClusterZoneState.updated_at.desc()).limit(250)).all()
        return {
            "peers": [_peer_dict(p) for p in peers],
            "jobs": [{
                "id": j.id, "peer_id": j.peer_id, "zone": j.zone, "action": j.action,
                "status": j.status, "attempts": j.attempts, "next_attempt_at": _iso(j.next_attempt_at),
                "last_error": j.last_error, "updated_at": _iso(j.updated_at),
            } for j in jobs],
            "convergence": [{
                "peer_id": row.peer_id, "zone": row.zone, "serial": row.serial,
                "content_sha256": row.content_sha256, "direction": row.direction,
                "updated_at": _iso(row.updated_at),
            } for row in states],
        }


def create_peer(params: dict) -> dict:
    data = _validate_peer(params)
    credential = data.pop("credential")
    with write_session() as db:
        if db.scalar(select(DnsClusterPeer.id).where(DnsClusterPeer.name == data["name"])) is not None:
            raise ValidationError("A DNS cluster peer with that name already exists")
        peer = DnsClusterPeer(
            **data, credential_enc=appcrypto.encrypt_secret(credential),
            credential_hash=hashlib.sha256(credential.encode()).hexdigest(),
        )
        db.add(peer)
        db.flush()
        result = _peer_dict(peer)
    # A generated Boron key is shown exactly once so the administrator can
    # put the same shared key on the other Boron node.
    if params.get("credential") is None:
        result["generated_credential"] = credential
    return result


def update_peer(params: dict) -> dict:
    with write_session() as db:
        peer = db.get(DnsClusterPeer, int(params.get("peer_id", 0)))
        if peer is None:
            raise ValidationError("DNS cluster peer not found")
        data = _validate_peer(params, existing=peer)
        credential = data.pop("credential")
        for key, value in data.items():
            setattr(peer, key, value)
        if credential is not None:
            peer.credential_enc = appcrypto.encrypt_secret(credential)
            peer.credential_hash = hashlib.sha256(credential.encode()).hexdigest()
        peer.status = "pending"
        peer.last_error = None
        db.flush()
        return _peer_dict(peer)


def delete_peer(params: dict) -> dict:
    with write_session() as db:
        peer = db.get(DnsClusterPeer, int(params.get("peer_id", 0)))
        if peer is None:
            return {"deleted": False}
        for job in db.scalars(select(DnsClusterOutbox).where(DnsClusterOutbox.peer_id == peer.id)).all():
            db.delete(job)
        for state in db.scalars(select(DnsClusterZoneState).where(DnsClusterZoneState.peer_id == peer.id)).all():
            db.delete(state)
        for receipt in db.scalars(select(DnsClusterReceipt).where(DnsClusterReceipt.peer_id == peer.id)).all():
            # Keep idempotency receipts after peer deletion without retaining
            # a dangling foreign-key reference.
            receipt.peer_id = None
        db.delete(peer)
    return {"deleted": True}


def _canonical_payload(zone: str) -> dict:
    data = powerdns.get_zone(zone)
    rrsets = []
    for rrset in data.get("rrsets", []):
        if rrset.get("type") not in RECORD_TYPES:
            continue
        rrsets.append({
            "name": str(rrset["name"]), "type": str(rrset["type"]),
            "ttl": int(rrset.get("ttl") or 3600),
            "values": [str(r["content"]) for r in rrset.get("records", []) if not r.get("disabled")],
        })
    serial = 0
    for rrset in rrsets:
        if rrset["type"] == "SOA" and rrset["values"]:
            parts = rrset["values"][0].split()
            if len(parts) > 2 and parts[2].isdigit():
                serial = int(parts[2])
                break
    canonical = json.dumps(rrsets, sort_keys=True, separators=(",", ":"))
    return {
        "version": 2, "zone": validate_domain(zone), "rrsets": rrsets,
        "serial": serial, "content_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "generated_at": _now().isoformat(),
    }


def _peer_covers(peer: DnsClusterPeer, zone: str) -> bool:
    return not peer.zones or zone in peer.zones


def _enqueue_for_peer(db, peer: DnsClusterPeer, zone: str, action: str, payload: dict | None) -> None:
    job = db.scalar(select(DnsClusterOutbox).where(
        DnsClusterOutbox.peer_id == peer.id, DnsClusterOutbox.zone == zone,
    ))
    event_id = uuid.uuid4().hex
    if job is None:
        db.add(DnsClusterOutbox(peer_id=peer.id, zone=zone, action=action, payload=payload,
                                event_id=event_id, status="pending", next_attempt_at=_now()))
    else:
        job.action, job.payload, job.event_id = action, payload, event_id
        job.status, job.attempts, job.next_attempt_at = "pending", 0, _now()
        job.last_error = None


def enqueue_zone(zone: str, action: str = "upsert") -> dict:
    zone = validate_domain(zone)
    if action not in {"upsert", "delete"}:
        raise ValidationError("Invalid DNS cluster action")
    if action == "upsert":
        with write_session() as db:
            cf = db.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone, CloudflareZone.status == "active"))
        if cf is not None:
            return {"queued": 0, "excluded": "cloudflare"}
        payload = _canonical_payload(zone)
    else:
        payload = None
    with write_session() as db:
        peers = db.scalars(select(DnsClusterPeer).where(DnsClusterPeer.enabled.is_(True))).all()
        peers = [peer for peer in peers if peer.direction in {"push", "bidirectional"} and _peer_covers(peer, zone)]
        for peer in peers:
            _enqueue_for_peer(db, peer, zone, action, payload)
        return {"queued": len(peers)}


def sync_all(_params: dict) -> dict:
    with write_session() as db:
        zones = db.scalars(select(DnsZone.zone).order_by(DnsZone.zone)).all()
    queued = 0
    for zone in zones:
        queued += enqueue_zone(zone)["queued"]
    return {"zones": len(zones), "queued": queued}


def _zone_text(payload: dict) -> str:
    zone = payload["zone"]
    lines = [f"$ORIGIN {zone}.", "$TTL 3600"]
    for rr in payload["rrsets"]:
        name = rr["name"]
        for value in rr["values"]:
            lines.append(f"{name}\t{rr['ttl']}\tIN\t{rr['type']}\t{value}")
    return "\n".join(lines) + "\n"


def _client(peer: DnsClusterPeer) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30, read=60), verify=peer.verify_tls,
                        follow_redirects=False, trust_env=False, transport=_transport)


def _metadata_ok(data: dict, *, allow_absent: bool = False) -> None:
    meta = data.get("metadata") or {}
    if int(meta.get("result", 0)) != 1:
        reason = str(meta.get("reason") or "remote cPanel API rejected the request")
        if allow_absent and any(marker in reason.lower() for marker in ("does not exist", "doesn't exist", "not found", "no zone")):
            return
        raise RuntimeError(reason)


def _cpanel_call(client: httpx.Client, peer: DnsClusterPeer, command: str, params: dict | None = None,
                 *, allow_absent: bool = False) -> dict:
    headers = {"Authorization": f"whm {peer.username}:{appcrypto.decrypt_secret(peer.credential_enc)}"}
    response = client.request("POST", f"{peer.endpoint}/json-api/{command}",
                              params={"api.version": 1}, data=params or {}, headers=headers)
    response.raise_for_status()
    data = response.json()
    _metadata_ok(data, allow_absent=allow_absent)
    return data


def _cpanel_record_params(zone: str, rr: dict, value: str) -> dict:
    params = {"zone": zone, "name": rr["name"], "type": rr["type"], "ttl": rr["ttl"], "class": "IN"}
    rtype = rr["type"]
    if rtype in {"A", "AAAA"}: params["address"] = value
    elif rtype == "CNAME": params["cname"] = value
    elif rtype == "NS": params["nsdname"] = value
    elif rtype == "PTR": params["ptrdname"] = value
    elif rtype == "TXT": params["txtdata"] = value.strip('"')
    elif rtype == "MX":
        preference, exchange = value.split(maxsplit=1)
        params.update(preference=preference, exchange=exchange)
    elif rtype == "SRV":
        priority, weight, port, target = value.split(maxsplit=3)
        params.update(priority=priority, weight=weight, port=port, target=target)
    elif rtype == "CAA":
        flag, tag, target = value.split(maxsplit=2)
        params.update(flag=flag, tag=tag, value=target.strip('"'))
    return params


def _send_cpanel(peer: DnsClusterPeer, action: str, payload: dict | None, zone: str) -> None:
    with _client(peer) as client:
        if action == "delete":
            _cpanel_call(client, peer, "killdns", {"domain": zone}, allow_absent=True)
            return
        assert payload is not None
        apex = next((v for r in payload["rrsets"] if r["type"] == "A" and r["name"].rstrip(".") == zone
                     for v in r["values"]), "127.0.0.1")
        _cpanel_call(client, peer, "adddns", {"domain": zone, "ip": apex, "allowoverwrite": 1})
        dump = _cpanel_call(client, peer, "dumpzone", {"domain": zone})
        rows = (dump.get("data") or {}).get("zone") or []
        removable = [(r, r.get("Line", r.get("line"))) for r in rows
                     if str(r.get("type", "")).upper() != "SOA" and r.get("Line", r.get("line"))]
        for row, line in sorted(removable, key=lambda item: int(item[1]), reverse=True):
            _cpanel_call(client, peer, "removezonerecord", {"zone": zone, "line": int(line)})
        for rr in payload["rrsets"]:
            if rr["type"] == "SOA":
                continue
            for value in rr["values"]:
                _cpanel_call(client, peer, "addzonerecord", _cpanel_record_params(zone, rr, value))


def _directadmin_zone_exists(client: httpx.Client, peer: DnsClusterPeer, auth: tuple[str, str], zone: str) -> bool:
    response = client.get(
        f"{peer.endpoint}/CMD_API_DNS_ADMIN",
        auth=auth,
        params={"action": "exists", "domain": zone},
    )
    response.raise_for_status()
    result = parse_qs(response.text, keep_blank_values=True)
    if result.get("error", ["0"])[0] == "1":
        raise RuntimeError("DirectAdmin rejected the DNS zone existence check")
    exists = result.get("exists", [None])[0]
    if exists not in {"0", "1"}:
        raise RuntimeError("DirectAdmin returned an invalid DNS zone existence result")
    return exists == "1"


def _send_directadmin(
    peer: DnsClusterPeer,
    action: str,
    payload: dict | None,
    zone: str,
    *,
    boron_managed_remote: bool = False,
) -> str:
    auth = (peer.username or "admin", appcrypto.decrypt_secret(peer.credential_enc))
    with _client(peer) as client:
        if action == "delete":
            # No successful Boron send means this same-named remote zone
            # predates our ownership. Never delete it during local cleanup.
            if not boron_managed_remote:
                return "skipped-existing"
            response = client.post(f"{peer.endpoint}/CMD_API_DNS_ADMIN", auth=auth,
                                   data={"action": "delete", "select0": zone})
        else:
            # DirectAdmin rawsave creates an absent zone but overwrites an
            # existing one. On the first delivery, ask DirectAdmin's official
            # action=exists endpoint and preserve any pre-existing zone. A
            # successful first rawsave records direction=sent, which permits
            # later Boron changes to update only that Boron-created copy.
            if not boron_managed_remote and _directadmin_zone_exists(client, peer, auth, zone):
                return "skipped-existing"
            response = client.post(f"{peer.endpoint}/CMD_API_DNS_ADMIN", auth=auth,
                                   params={"action": "rawsave", "domain": zone},
                                   content=_zone_text(payload or {}),
                                   headers={"Content-Type": "text/plain"})
        response.raise_for_status()
        if ("error=1" in response.text or response.text.lstrip().startswith("error=1")) and not (
            action == "delete" and any(marker in response.text.lower() for marker in ("does+not+exist", "not+found", "cannot+find"))
        ):
            raise RuntimeError("DirectAdmin rejected the DNS cluster update")
    # Distinct from the legacy generic "sent" marker: only this value proves
    # the first guarded delivery observed the zone absent before rawsave.
    return "sent-owned"


def _send_boron(peer: DnsClusterPeer, action: str, payload: dict | None, zone: str, event_id: str) -> None:
    with _client(peer) as client:
        response = client.post(f"{peer.endpoint}/api/v1/dns-cluster/apply",
                               headers={"Authorization": f"Bearer {appcrypto.decrypt_secret(peer.credential_enc)}"},
                               json={"event_id": event_id, "action": action, "zone": zone, "payload": payload})
        response.raise_for_status()


def _send(
    peer: DnsClusterPeer,
    action: str,
    payload: dict | None,
    zone: str,
    event_id: str,
    *,
    boron_managed_remote: bool = False,
) -> str:
    if peer.peer_type == "boron":
        _send_boron(peer, action, payload, zone, event_id)
        return "sent"
    if peer.peer_type == "directadmin":
        return _send_directadmin(
            peer, action, payload, zone, boron_managed_remote=boron_managed_remote
        )
    _send_cpanel(peer, action, payload, zone)
    return "sent"


def process_one() -> bool:
    now = _now()
    with write_session() as db:
        job = db.scalar(select(DnsClusterOutbox).where(
            DnsClusterOutbox.status.in_(("pending", "failed")), DnsClusterOutbox.next_attempt_at <= now,
        ).order_by(DnsClusterOutbox.next_attempt_at, DnsClusterOutbox.id))
        if job is None:
            return False
        job.status = "running"
        job.attempts += 1
        db.flush()
        job_id = job.id
    try:
        with write_session() as db:
            job = db.get(DnsClusterOutbox, job_id)
            peer = db.get(DnsClusterPeer, job.peer_id) if job else None
            if job is None or peer is None or not peer.enabled:
                if job: db.delete(job)
                return True
            previous_state = db.scalar(select(DnsClusterZoneState).where(
                DnsClusterZoneState.peer_id == peer.id,
                DnsClusterZoneState.zone == job.zone,
            ))
            if peer.peer_type == "directadmin":
                # Legacy `sent` rows predate the existence guard and cannot
                # prove who created the remote zone. Trust only the marker
                # written after an absent check followed by rawsave.
                boron_managed_remote = (
                    previous_state is not None and previous_state.direction == "sent-owned"
                )
            else:
                boron_managed_remote = previous_state is not None and previous_state.direction == "sent"
            db.expunge(job); db.expunge(peer)
        delivery = _send(
            peer,
            job.action,
            job.payload,
            job.zone,
            job.event_id,
            boron_managed_remote=boron_managed_remote,
        )
        with write_session() as db:
            current = db.get(DnsClusterOutbox, job_id)
            target = db.get(DnsClusterPeer, peer.id)
            if current is not None and current.event_id == job.event_id:
                db.delete(current)
            if target is not None:
                target.status, target.last_success_at, target.last_error = "healthy", _now(), None
                if job.action == "upsert" and job.payload:
                    state = db.scalar(select(DnsClusterZoneState).where(
                        DnsClusterZoneState.peer_id == peer.id, DnsClusterZoneState.zone == job.zone,
                    ))
                    if state is None:
                        state = DnsClusterZoneState(peer_id=peer.id, zone=job.zone, direction=delivery)
                        db.add(state)
                    state.serial = int(job.payload.get("serial") or 0)
                    state.content_sha256 = job.payload.get("content_sha256")
                    state.direction = delivery
                elif job.action == "delete":
                    state = db.scalar(select(DnsClusterZoneState).where(
                        DnsClusterZoneState.peer_id == peer.id, DnsClusterZoneState.zone == job.zone,
                    ))
                    if state is not None:
                        db.delete(state)
    except Exception as exc:
        logger.warning("DNS cluster delivery failed for job %s: %s", job_id, type(exc).__name__)
        with write_session() as db:
            current = db.get(DnsClusterOutbox, job_id)
            if current is not None:
                current.status = "failed"
                current.last_error = str(exc)[:2000]
                current.next_attempt_at = _now() + dt.timedelta(seconds=min(3600, 5 * 2 ** min(current.attempts, 9)))
                target = db.get(DnsClusterPeer, current.peer_id)
                if target is not None:
                    target.status, target.last_error = "error", current.last_error
    return True


def _validate_payload(zone: str, payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2} or payload.get("zone") != zone:
        raise ValidationError("Invalid DNS cluster zone payload")
    clean = []
    fqdn = zone + "."
    rrsets = payload.get("rrsets")
    if not isinstance(rrsets, list) or len(rrsets) > 10000:
        raise ValidationError("Invalid DNS cluster record list")
    for rr in rrsets:
        if not isinstance(rr, dict) or rr.get("type") not in RECORD_TYPES:
            raise ValidationError("Invalid DNS cluster record")
        name = str(rr.get("name", ""))
        if not name.endswith("."): name += "."
        if name != fqdn and not name.endswith("." + fqdn):
            raise ValidationError("DNS cluster record is outside its zone")
        ttl = int(rr.get("ttl", 0))
        values = rr.get("values")
        if not 60 <= ttl <= 2147483647 or not isinstance(values, list) or not values or len(values) > 100:
            raise ValidationError("Invalid DNS cluster record values")
        if any(not isinstance(v, str) or not v or len(v) > 8192 for v in values):
            raise ValidationError("Invalid DNS cluster record value")
        clean.append({"name": name, "type": rr["type"], "ttl": ttl, "values": values})
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    calculated = hashlib.sha256(canonical.encode()).hexdigest()
    supplied = payload.get("content_sha256")
    if supplied is not None and supplied != calculated:
        raise ValidationError("DNS cluster payload checksum does not match its records")
    return {
        "version": int(payload.get("version")), "zone": zone, "rrsets": clean,
        "serial": int(payload.get("serial") or 0), "content_sha256": calculated,
    }


def apply_incoming(params: dict) -> dict:
    event_id = str(params.get("event_id", ""))
    if len(event_id) != 32 or any(c not in "0123456789abcdef" for c in event_id):
        raise ValidationError("Invalid DNS cluster event ID")
    zone = validate_domain(params.get("zone"))
    action = params.get("action")
    if action not in {"upsert", "delete"}:
        raise ValidationError("Invalid DNS cluster action")
    with write_session() as db:
        peer = db.scalar(select(DnsClusterPeer).where(DnsClusterPeer.name == params.get("_peer_name")))
        if peer is None or peer.direction not in {"receive", "bidirectional"}:
            raise ValidationError("This peer is not permitted to send DNS changes")
        if not _peer_covers(peer, zone):
            raise ValidationError("This zone is outside the peer's ownership allowlist")
        peer_id = peer.id
        if db.scalar(select(DnsClusterReceipt.id).where(DnsClusterReceipt.event_id == event_id)) is not None:
            return {"applied": False, "duplicate": True}
    if action == "delete":
        powerdns.delete_zone(zone)
    else:
        payload = _validate_payload(zone, params.get("payload"))
        with write_session() as db:
            state = db.scalar(select(DnsClusterZoneState).where(
                DnsClusterZoneState.peer_id == peer_id, DnsClusterZoneState.zone == zone,
            ))
            if state is not None:
                if payload["serial"] < state.serial:
                    raise ValidationError("Stale DNS cluster serial was rejected")
                if payload["serial"] == state.serial and state.content_sha256 not in {None, payload["content_sha256"]}:
                    raise ValidationError("Conflicting DNS data has the same serial; resolve it manually")
        if not powerdns.zone_exists(zone):
            ns_values = [v for rr in payload["rrsets"] if rr["type"] == "NS" and rr["name"].rstrip(".") == zone for v in rr["values"]]
            if not ns_values:
                raise ValidationError("Incoming zone has no apex nameservers")
            powerdns.create_zone(zone, ns_values)
        existing = powerdns.get_zone(zone).get("rrsets", [])
        desired = {(r["name"], r["type"]): r for r in payload["rrsets"] if r["type"] != "SOA"}
        changes = [{"name": r["name"], "type": r["type"], "changetype": "DELETE"}
                   for r in existing if r.get("type") != "SOA" and (r.get("name"), r.get("type")) not in desired]
        changes += [{"name": r["name"], "type": r["type"], "ttl": r["ttl"], "changetype": "REPLACE",
                     "records": [{"content": v, "disabled": False} for v in r["values"]]} for r in desired.values()]
        powerdns.apply_rrset_changes(zone, changes)
    with write_session() as db:
        db.add(DnsClusterReceipt(event_id=event_id, peer_id=peer_id, zone=zone))
        if action == "upsert":
            state = db.scalar(select(DnsClusterZoneState).where(
                DnsClusterZoneState.peer_id == peer_id, DnsClusterZoneState.zone == zone,
            ))
            if state is None:
                state = DnsClusterZoneState(peer_id=peer_id, zone=zone, direction="received")
                db.add(state)
            state.serial = payload["serial"]
            state.content_sha256 = payload["content_sha256"]
            state.direction = "received"
    return {"applied": True, "duplicate": False}


def test_peer(params: dict) -> dict:
    with write_session() as db:
        peer = db.get(DnsClusterPeer, int(params.get("peer_id", 0)))
        if peer is None: raise ValidationError("DNS cluster peer not found")
        db.expunge(peer)
    try:
        with _client(peer) as client:
            if peer.peer_type == "boron":
                response = client.get(f"{peer.endpoint}/api/v1/dns-cluster/ping",
                                      headers={"Authorization": f"Bearer {appcrypto.decrypt_secret(peer.credential_enc)}"})
                response.raise_for_status()
            elif peer.peer_type == "directadmin":
                response = client.get(f"{peer.endpoint}/CMD_API_LOGIN_TEST", auth=(peer.username or "admin", appcrypto.decrypt_secret(peer.credential_enc)))
                response.raise_for_status()
                if "error=1" in response.text: raise RuntimeError("DirectAdmin rejected the login key")
            else:
                _cpanel_call(client, peer, "version")
        with write_session() as db:
            row = db.get(DnsClusterPeer, peer.id)
            row.status, row.last_success_at, row.last_error = "healthy", _now(), None
        return {"ok": True}
    except Exception as exc:
        with write_session() as db:
            row = db.get(DnsClusterPeer, peer.id)
            row.status, row.last_error = "error", str(exc)[:2000]
        raise ValidationError("Peer connection failed; check its URL, credentials, TLS certificate, and API permissions") from None


def recover_and_start_worker() -> None:
    global _worker_started
    with write_session() as db:
        for job in db.scalars(select(DnsClusterOutbox).where(DnsClusterOutbox.status == "running")).all():
            job.status, job.next_attempt_at = "pending", _now()
    if _worker_started:
        return
    _worker_started = True
    def run():
        while True:
            try:
                if not process_one(): time.sleep(5)
            except Exception:
                logger.exception("DNS cluster worker failed")
                time.sleep(5)
    threading.Thread(target=run, name="dns-cluster", daemon=True).start()
