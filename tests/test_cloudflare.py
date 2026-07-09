"""docs/PLAN-cloudflare.md Phase 0: daemon/cloudflare.py client tests.

Everything runs against an in-memory fake of the Cloudflare v4 API via
httpx.MockTransport (the module's _transport test hook) -- no live calls
in CI, mirroring how the PowerDNS client is treated.
"""
from __future__ import annotations

import json

import httpx
import pytest

from daemon import cloudflare, cloudflare_ops
from shared.config import settings


def _ok(result, **extra) -> httpx.Response:
    body = {"success": True, "errors": [], "messages": [], "result": result}
    body.update(extra)
    return httpx.Response(200, json=body)


def _err(status: int, code: int, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"success": False, "errors": [{"code": code, "message": message}], "result": None},
    )


class FakeCF:
    """Minimal in-memory Cloudflare v4 API: zones + dns_records + ips +
    token verify, enough to exercise every client primitive."""

    def __init__(self):
        self.zones: dict[str, dict] = {}
        self.records: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_next: list[httpx.Response] = []  # queued canned responses
        self._seq = 0

    def add_zone(self, name: str, status: str = "active") -> str:
        zid = self._next_id("z")
        self.zones[zid] = {
            "id": zid,
            "name": name,
            "status": status,
            "name_servers": ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"],
        }
        self.records[zid] = []
        return zid

    def add_record(self, zid: str, **fields) -> dict:
        rec = {"id": self._next_id("r"), "ttl": cloudflare.DEFAULT_TTL, "proxied": False}
        rec.update(fields)
        self.records[zid].append(rec)
        return rec

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:04d}"

    # --- transport handler ---------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/client/v4")
        self.calls.append((request.method, path))
        if self.fail_next:
            return self.fail_next.pop(0)
        params = dict(request.url.params)
        body = json.loads(request.content) if request.content else {}

        if path == "/user/tokens/verify":
            return _ok({"id": "tok", "status": "active"})
        if path == "/ips":
            return _ok({"ipv4_cidrs": ["173.245.48.0/20"], "ipv6_cidrs": ["2400:cb00::/32"], "etag": "abc"})

        if path == "/zones" and request.method == "GET":
            zones = list(self.zones.values())
            if "name" in params:
                zones = [z for z in zones if z["name"] == params["name"]]
            return _ok(zones, result_info={"total_pages": 1})
        if path == "/zones" and request.method == "POST":
            zid = self.add_zone(body["name"], status="pending")
            return _ok(self.zones[zid])

        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "zones":
            zid = parts[1]
            if zid not in self.zones:
                return _err(404, 7003, "zone not found")
            if len(parts) == 2:
                if request.method == "GET":
                    return _ok(self.zones[zid])
                if request.method == "DELETE":
                    del self.zones[zid]
                    self.records.pop(zid, None)
                    return _ok({"id": zid})
            if parts[2:] == ["activation_check"]:
                return _ok({"id": zid})
            if parts[2:] == ["settings", "ssl"]:
                if request.method == "GET":
                    return _ok({"id": "ssl", "value": self.zones[zid].get("ssl_mode", "off")})
                self.zones[zid]["ssl_mode"] = body.get("value")
                return _ok({"id": "ssl", "value": body.get("value")})
            if parts[2:] == ["purge_cache"]:
                self.zones[zid]["purged"] = True
                return _ok({"id": zid})
            if parts[2] == "dns_records":
                return self._dns_records(zid, parts[3:], request.method, params, body)
        return _err(404, 7000, f"no route for {request.method} {path}")

    def _dns_records(self, zid: str, rest: list[str], method: str, params: dict, body: dict) -> httpx.Response:
        recs = self.records[zid]
        if not rest:
            if method == "GET":
                out = recs
                if "name" in params:
                    out = [r for r in out if r.get("name") == params["name"]]
                if "type" in params:
                    out = [r for r in out if r.get("type") == params["type"]]
                return _ok(out, result_info={"total_pages": 1})
            if method == "POST":
                if body.get("type") in ("A", "AAAA", "CNAME") and body.get("proxied"):
                    body["ttl"] = 1  # CF forces auto TTL on proxied records
                rec = self.add_record(zid, **body)
                return _ok(rec)
        else:
            rid = rest[0]
            rec = next((r for r in recs if r["id"] == rid), None)
            if rec is None:
                return _err(404, 81044, "record not found")
            if method == "PATCH":
                rec.update(body)
                return _ok(rec)
            if method == "DELETE":
                recs.remove(rec)
                return _ok({"id": rid})
        return _err(404, 7000, "no route")


@pytest.fixture()
def fake_cf(monkeypatch, isolated_db):
    # isolated_db: cf.health now reads the account-pool table (Phase 2+3),
    # so every health/client test needs a real, isolated DB -- never the
    # live one.
    fake = FakeCF()
    monkeypatch.setattr(cloudflare, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "cloudflare_account_id", "acct-123")
    monkeypatch.setattr(cloudflare.time, "sleep", lambda s: None)  # no real backoff waits
    return fake


# --- request plumbing -------------------------------------------------------


def test_verify_token(fake_cf):
    assert cloudflare.verify_token()["status"] == "active"


def test_error_surfaces_cf_code(fake_cf):
    fake_cf.fail_next.append(_err(400, 1061, "zone already exists"))
    with pytest.raises(cloudflare.CloudflareError) as excinfo:
        cloudflare.create_zone("example.com")
    assert excinfo.value.cf_code == 1061
    assert "zone already exists" in excinfo.value.detail


def test_retries_on_429_then_succeeds(fake_cf):
    fake_cf.fail_next.append(_err(429, 971, "rate limited"))
    assert cloudflare.verify_token()["status"] == "active"
    assert len(fake_cf.calls) == 2  # one failed attempt + one retry


def test_gives_up_after_max_attempts(fake_cf):
    fake_cf.fail_next.extend(_err(503, 7000, "unavailable") for _ in range(cloudflare.MAX_ATTEMPTS))
    with pytest.raises(cloudflare.CloudflareError) as excinfo:
        cloudflare.verify_token()
    assert excinfo.value.status_code == 503
    assert len(fake_cf.calls) == cloudflare.MAX_ATTEMPTS


def test_client_errors_are_not_retried(fake_cf):
    fake_cf.fail_next.append(_err(403, 9109, "invalid token"))
    with pytest.raises(cloudflare.CloudflareError):
        cloudflare.verify_token()
    assert len(fake_cf.calls) == 1


# --- zone ops ---------------------------------------------------------------


def test_create_zone_returns_assigned_nameservers(fake_cf):
    created = cloudflare.create_zone("example.com")
    assert created["status"] == "pending"
    assert len(created["name_servers"]) == 2
    assert cloudflare.zone_exists("example.com")


def test_delete_zone_is_idempotent(fake_cf):
    zid = fake_cf.add_zone("example.com")
    cloudflare.delete_zone("example.com", zone_id=zid)
    cloudflare.delete_zone("example.com")  # already gone: no error
    assert not cloudflare.zone_exists("example.com")


def test_get_zone_status(fake_cf):
    fake_cf.add_zone("example.com", status="active")
    status = cloudflare.get_zone_status("example.com")
    assert status["status"] == "active"
    assert status["name_servers"]


def test_set_ssl_mode_and_purge(fake_cf):
    zid = fake_cf.add_zone("example.com")
    cloudflare.set_ssl_mode("example.com", "full", zone_id=zid)
    cloudflare.purge_cache("example.com", zone_id=zid)
    assert fake_cf.zones[zid]["ssl_mode"] == "full"
    assert fake_cf.zones[zid]["purged"] is True


def test_get_ip_ranges(fake_cf):
    ranges = cloudflare.get_ip_ranges()
    assert ranges["ipv4_cidrs"] and ranges["ipv6_cidrs"] and ranges["etag"]


# --- record translation -----------------------------------------------------
# The write side (_to_cf_payload) and read side (_from_cf_record) must be
# exact inverses for every PowerDNS-style value handlers_dns's validators
# produce -- upsert_record's reconcile depends on it.


@pytest.mark.parametrize(
    "rtype,value",
    [
        ("A", "192.0.2.10"),
        ("AAAA", "2001:db8::1"),
        ("CNAME", "target.example.com."),
        ("NS", "ns1.example.com."),
        ("PTR", "host.example.com."),
        ("MX", "10 mail.example.com."),
        ("TXT", '"v=spf1 mx a ~all"'),
        ("TXT", '"say \\"hi\\""'),
        ("SRV", "10 20 5060 sip.example.com."),
        ("CAA", '0 issue "letsencrypt.org"'),
    ],
)
def test_value_round_trip(rtype, value):
    payload = cloudflare._to_cf_payload(rtype, "name.example.com", value, 3600, False)
    assert cloudflare._from_cf_record({"type": rtype, **payload}) == value


def test_mx_payload_splits_priority():
    payload = cloudflare._to_cf_payload("MX", "example.com", "10 mail.example.com.", 3600, False)
    assert payload["priority"] == 10
    assert payload["content"] == "mail.example.com"


def test_txt_payload_is_unquoted():
    payload = cloudflare._to_cf_payload("TXT", "example.com", '"v=spf1 mx a ~all"', 3600, False)
    assert payload["content"] == "v=spf1 mx a ~all"


def test_srv_and_caa_use_data_objects():
    srv = cloudflare._to_cf_payload("SRV", "_sip._tcp.example.com", "10 20 5060 sip.example.com.", 3600, False)
    assert srv["data"] == {"priority": 10, "weight": 20, "port": 5060, "target": "sip.example.com"}
    caa = cloudflare._to_cf_payload("CAA", "example.com", '0 issue "letsencrypt.org"', 3600, False)
    assert caa["data"] == {"flags": 0, "tag": "issue", "value": "letsencrypt.org"}


def test_proxied_only_applies_to_proxyable_types():
    a = cloudflare._to_cf_payload("A", "example.com", "192.0.2.10", 3600, True)
    assert a["proxied"] is True
    assert a["ttl"] == cloudflare.AUTO_TTL  # proxied records get auto TTL
    txt = cloudflare._to_cf_payload("TXT", "example.com", '"x"', 3600, True)
    assert "proxied" not in txt
    assert txt["ttl"] == 3600


# --- record operations ------------------------------------------------------


def test_upsert_creates_records(fake_cf):
    zid = fake_cf.add_zone("example.com")
    cloudflare.upsert_record("example.com", "www", "A", ["192.0.2.10", "192.0.2.11"], ttl=300, zone_id=zid)
    recs = fake_cf.records[zid]
    assert {r["content"] for r in recs} == {"192.0.2.10", "192.0.2.11"}
    assert all(r["name"] == "www.example.com" and r["ttl"] == 300 for r in recs)
    assert all(r["proxied"] is False for r in recs)


def test_upsert_replace_semantics_deletes_extras(fake_cf):
    zid = fake_cf.add_zone("example.com")
    fake_cf.add_record(zid, type="A", name="www.example.com", content="192.0.2.1")
    fake_cf.add_record(zid, type="A", name="www.example.com", content="192.0.2.2")
    cloudflare.upsert_record("example.com", "www", "A", ["192.0.2.2", "192.0.2.3"], zone_id=zid)
    assert {r["content"] for r in fake_cf.records[zid]} == {"192.0.2.2", "192.0.2.3"}


def test_upsert_patches_proxied_flag_in_place(fake_cf):
    zid = fake_cf.add_zone("example.com")
    rec = fake_cf.add_record(zid, type="A", name="www.example.com", content="192.0.2.1", proxied=False)
    cloudflare.upsert_record("example.com", "www", "A", ["192.0.2.1"], proxied=True, zone_id=zid)
    assert len(fake_cf.records[zid]) == 1
    assert fake_cf.records[zid][0]["id"] == rec["id"]  # patched, not recreated
    assert fake_cf.records[zid][0]["proxied"] is True


def test_upsert_is_a_noop_when_already_converged(fake_cf):
    zid = fake_cf.add_zone("example.com")
    fake_cf.add_record(zid, type="A", name="www.example.com", content="192.0.2.1", ttl=3600)
    cloudflare.upsert_record("example.com", "www", "A", ["192.0.2.1"], ttl=3600, zone_id=zid)
    writes = [c for c in fake_cf.calls if c[0] in ("POST", "PATCH", "DELETE")]
    assert writes == []


def test_upsert_forces_proxied_false_on_unproxyable_types(fake_cf):
    zid = fake_cf.add_zone("example.com")
    cloudflare.upsert_record("example.com", "@", "TXT", ['"v=spf1 mx a ~all"'], proxied=True, zone_id=zid)
    assert "proxied" not in fake_cf.records[zid][0] or not fake_cf.records[zid][0].get("proxied")


def test_upsert_apex_uses_zone_name(fake_cf):
    zid = fake_cf.add_zone("example.com")
    cloudflare.upsert_record("example.com", "@", "A", ["192.0.2.1"], zone_id=zid)
    assert fake_cf.records[zid][0]["name"] == "example.com"


def test_delete_record_removes_all_values(fake_cf):
    zid = fake_cf.add_zone("example.com")
    fake_cf.add_record(zid, type="A", name="www.example.com", content="192.0.2.1")
    fake_cf.add_record(zid, type="A", name="www.example.com", content="192.0.2.2")
    fake_cf.add_record(zid, type="A", name="other.example.com", content="192.0.2.3")
    cloudflare.delete_record("example.com", "www", "A", zone_id=zid)
    assert [r["name"] for r in fake_cf.records[zid]] == ["other.example.com"]


def test_list_records_groups_rrsets_and_maps_auto_ttl(fake_cf):
    zid = fake_cf.add_zone("example.com")
    fake_cf.add_record(zid, type="A", name="example.com", content="192.0.2.1", proxied=True, ttl=1)
    fake_cf.add_record(zid, type="MX", name="example.com", content="mail.example.com", priority=10)
    fake_cf.add_record(zid, type="MX", name="example.com", content="mail2.example.com", priority=20)
    fake_cf.add_record(zid, type="TXT", name="example.com", content="v=spf1 mx a ~all")
    records = cloudflare.list_records("example.com", zone_id=zid)
    by_key = {(r["name"], r["type"]): r for r in records}
    assert by_key[("example.com", "A")]["proxied"] is True
    assert by_key[("example.com", "A")]["ttl"] == cloudflare.DEFAULT_TTL  # auto (1) mapped back
    assert by_key[("example.com", "MX")]["values"] == ["10 mail.example.com.", "20 mail2.example.com."]
    assert by_key[("example.com", "TXT")]["values"] == ['"v=spf1 mx a ~all"']
    assert by_key[("example.com", "TXT")]["proxied"] is False


# --- cf.health op -----------------------------------------------------------


def test_health_unconfigured(isolated_db, monkeypatch):
    monkeypatch.delitem(settings.secrets, "CLOUDFLARE_API_TOKEN", raising=False)
    result = cloudflare_ops.health({})
    assert result["configured"] is False
    assert result["ok"] is False
    assert "CLOUDFLARE_API_TOKEN" in result["error"]


def test_health_green_with_valid_token(fake_cf, tmp_path, monkeypatch):
    ranges = tmp_path / "cloudflare-ranges.json"
    ranges.write_text(json.dumps({"ipv4_cidrs": ["173.245.48.0/20"], "ipv6_cidrs": []}))
    monkeypatch.setattr(settings, "cloudflare_ranges_file", str(ranges))
    result = cloudflare_ops.health({})
    assert result["configured"] is True
    assert result["token_valid"] is True
    assert result["api_ok"] is True
    assert result["ok"] is True
    assert result["ranges_file"]["exists"] is True
    assert result["ranges_file"]["stale"] is False
    assert result["ranges_file"]["ipv4_count"] == 1


def test_health_reports_api_failure(fake_cf, monkeypatch):
    monkeypatch.setattr(settings, "cloudflare_ranges_file", "/nonexistent/ranges.json")
    fake_cf.fail_next.append(_err(403, 9109, "invalid token"))
    result = cloudflare_ops.health({})
    assert result["configured"] is True
    assert result["ok"] is False
    assert result["api_ok"] is False
    assert "9109" in result["error"] or "invalid token" in result["error"]
    assert result["ranges_file"]["exists"] is False
