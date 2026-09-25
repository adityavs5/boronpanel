import hashlib
import json

import httpx
import pytest
from sqlalchemy import select

from daemon import dnscluster
from daemon.rpc_authority import AuthenticationError, authorize, resolve_principal
from shared.db import write_session
from shared.models import DnsClusterOutbox, DnsClusterPeer, DnsClusterReceipt
from shared.validation import ValidationError


ZONE_PAYLOAD = {
    "version": 1,
    "zone": "example.test",
    "rrsets": [
        {"name": "example.test.", "type": "SOA", "ttl": 3600,
         "values": ["ns1.example.test. hostmaster.example.test. 1 10800 3600 604800 3600"]},
        {"name": "example.test.", "type": "NS", "ttl": 3600,
         "values": ["ns1.example.test.", "ns2.example.test."]},
        {"name": "example.test.", "type": "A", "ttl": 300, "values": ["192.0.2.10"]},
        {"name": "www.example.test.", "type": "CNAME", "ttl": 300, "values": ["example.test."]},
    ],
}


def _peer(kind="boron", credential="bdc_shared_test_key", **overrides):
    params = {"name": f"{kind}-peer", "peer_type": kind, "endpoint": "https://dns.example.test",
              "credential": credential}
    if kind != "boron": params["username"] = "admin"
    params.update(overrides)
    return dnscluster.create_peer(params)


def test_peer_secret_is_encrypted_and_cluster_auth_is_narrow(isolated_db):
    result = _peer()
    json.dumps(result)
    assert "credential" not in result
    with write_session() as db:
        row = db.get(DnsClusterPeer, result["id"])
        assert "bdc_shared_test_key" not in row.credential_enc
        assert row.credential_hash == hashlib.sha256(b"bdc_shared_test_key").hexdigest()
    principal = resolve_principal({"type": "cluster", "value": "bdc_shared_test_key"})
    assert principal.role == "cluster"
    authorize("dnscluster.apply", {"zone": "example.test"}, principal)
    with pytest.raises(Exception):
        authorize("account.list", {}, principal)
    with pytest.raises(AuthenticationError):
        resolve_principal({"type": "cluster", "value": "wrong"})
    json.dumps(dnscluster.list_peers({}))


def test_outbox_coalesces_to_latest_complete_zone(isolated_db, monkeypatch):
    _peer()
    payloads = [ZONE_PAYLOAD, {**ZONE_PAYLOAD, "rrsets": ZONE_PAYLOAD["rrsets"][:-1]}]
    monkeypatch.setattr(dnscluster, "_canonical_payload", lambda zone: payloads.pop(0))
    assert dnscluster.enqueue_zone("example.test") == {"queued": 1}
    with write_session() as db:
        first = db.scalar(select(DnsClusterOutbox))
        first_event = first.event_id
    assert dnscluster.enqueue_zone("example.test") == {"queued": 1}
    with write_session() as db:
        jobs = db.scalars(select(DnsClusterOutbox)).all()
        assert len(jobs) == 1
        assert jobs[0].event_id != first_event
        assert jobs[0].payload["rrsets"] == ZONE_PAYLOAD["rrsets"][:-1]
    dnscluster.enqueue_zone("example.test", "delete")
    with write_session() as db:
        job = db.scalar(select(DnsClusterOutbox))
        assert job.action == "delete" and job.payload is None


def test_incoming_full_zone_is_idempotent_and_removes_stale_rrsets(isolated_db, monkeypatch):
    _peer(direction="receive", zones=["example.test"])
    existing = [
        {"name": "example.test.", "type": "SOA", "ttl": 3600, "records": []},
        {"name": "old.example.test.", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.99"}]},
    ]
    changes = []
    monkeypatch.setattr(dnscluster.powerdns, "zone_exists", lambda zone: True)
    monkeypatch.setattr(dnscluster.powerdns, "get_zone", lambda zone: {"rrsets": existing})
    monkeypatch.setattr(dnscluster.powerdns, "apply_rrset_changes", lambda zone, rrsets: changes.extend(rrsets))
    params = {"event_id": "a" * 32, "action": "upsert", "zone": "example.test", "payload": ZONE_PAYLOAD,
              "_peer_name": "boron-peer"}
    assert dnscluster.apply_incoming(params) == {"applied": True, "duplicate": False}
    assert any(c["changetype"] == "DELETE" and c["name"] == "old.example.test." for c in changes)
    assert any(c["changetype"] == "REPLACE" and c["type"] == "NS" for c in changes)
    assert dnscluster.apply_incoming(params) == {"applied": False, "duplicate": True}
    with write_session() as db:
        assert db.scalar(select(DnsClusterReceipt).where(DnsClusterReceipt.event_id == "a" * 32))


def test_incoming_rejects_out_of_zone_record(isolated_db):
    bad = {**ZONE_PAYLOAD, "rrsets": [{"name": "attacker.test.", "type": "A", "ttl": 300,
                                        "values": ["192.0.2.1"]}]}
    with pytest.raises(ValidationError, match="outside"):
        dnscluster._validate_payload("example.test", bad)


def test_receive_direction_requires_explicit_zone_ownership(isolated_db):
    with pytest.raises(ValidationError, match="allowlist"):
        _peer(direction="receive")


def test_incoming_rejects_unowned_zone(isolated_db):
    _peer(direction="receive", zones=["owned.test"])
    with pytest.raises(ValidationError, match="ownership"):
        dnscluster.apply_incoming({
            "event_id": "c" * 32, "action": "upsert", "zone": "example.test",
            "payload": ZONE_PAYLOAD, "_peer_name": "boron-peer",
        })


def test_directadmin_adapter_uses_rawsave_and_delete(isolated_db):
    result = _peer("directadmin", "login-key")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="error=0&text=ok")
    dnscluster._transport = httpx.MockTransport(handler)
    try:
        with write_session() as db:
            peer = db.get(DnsClusterPeer, result["id"]); db.expunge(peer)
        dnscluster._send_directadmin(peer, "upsert", ZONE_PAYLOAD, "example.test")
        dnscluster._send_directadmin(peer, "delete", None, "example.test")
    finally:
        dnscluster._transport = None
    assert requests[0].url.params["action"] == "rawsave"
    assert b"www.example.test." in requests[0].content
    assert b"action=delete" in requests[1].content and b"select0=example.test" in requests[1].content


def test_boron_delivery_sends_event_and_bearer(isolated_db):
    result = _peer()
    captured = {}
    def handler(request):
        captured.update(url=str(request.url), auth=request.headers.get("authorization"), body=request.read())
        return httpx.Response(200, json={"applied": True})
    dnscluster._transport = httpx.MockTransport(handler)
    try:
        with write_session() as db:
            peer = db.get(DnsClusterPeer, result["id"]); db.expunge(peer)
        dnscluster._send_boron(peer, "upsert", ZONE_PAYLOAD, "example.test", "b" * 32)
    finally:
        dnscluster._transport = None
    assert captured["url"].endswith("/api/v1/dns-cluster/apply")
    assert captured["auth"] == "Bearer bdc_shared_test_key"
    assert b'"event_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' in captured["body"]


def test_cpanel_adapter_replaces_template_records_and_preserves_source_ns(isolated_db):
    result = _peer("cpanel", "whm-token")
    calls = []
    def handler(request):
        calls.append((request.url.path.rsplit("/", 1)[-1], request.url.params, request.read().decode()))
        command = calls[-1][0]
        data = {"zone": [{"Line": 8, "type": "NS"}, {"Line": 9, "type": "A"}]} if command == "dumpzone" else {}
        return httpx.Response(200, json={"data": data, "metadata": {"result": 1, "reason": "OK"}})
    dnscluster._transport = httpx.MockTransport(handler)
    try:
        with write_session() as db:
            peer = db.get(DnsClusterPeer, result["id"]); db.expunge(peer)
        dnscluster._send_cpanel(peer, "upsert", ZONE_PAYLOAD, "example.test")
    finally:
        dnscluster._transport = None
    commands = [c[0] for c in calls]
    assert commands[:4] == ["adddns", "dumpzone", "removezonerecord", "removezonerecord"]
    assert commands.count("addzonerecord") == 4
    bodies = [body for command, _query, body in calls if command == "addzonerecord"]
    assert any("type=NS" in body and "nsdname=ns1.example.test." in body for body in bodies)
