"""Cloudflare API v4 client (docs/PLAN-cloudflare.md, Phase 0).

Mirrors daemon/powerdns.py's primitive surface (zone_exists / create_zone /
delete_zone / get_zone / upsert_record with REPLACE semantics /
delete_record / list_records) so daemon/dnsprovider.py can route each call
to either backend without call sites caring which one serves a zone. All
PowerDNS-shape <-> Cloudflare-shape value translation is owned HERE
(trailing dots, TXT quoting, MX priority field, SRV/CAA data objects):
callers keep speaking the one record dialect the whole project already
uses -- {name: <fqdn, no trailing dot>, type, ttl, values: [<PowerDNS-style
content>]} -- plus an optional `proxied` flag, meaningful only for
A/AAAA/CNAME (forced false everywhere else).

Unlike PowerDNS (one atomic rrset PATCH), Cloudflare models each value as
its own record object, so upsert_record here is a small reconcile loop
(create missing / patch changed / delete extra). Each individual call is
idempotent and retryable -- re-running a half-applied upsert converges.
"""
from __future__ import annotations

import contextlib
import contextvars
import time

import httpx

from shared.config import settings

API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_TTL = 3600

# Phase 2+3 multi-account: each zone is served by one CloudflareAccount pool
# row with its own token. Rather than thread a token argument through every
# primitive, callers set the active token for the duration of a block with
# use_token(); _client() reads it here. Default None -> the legacy single
# token in secrets.env (settings.cloudflare_api_token), so pre-pool zones and
# every existing test keep working unchanged. ContextVar is task/thread-safe,
# which matters because the daemon executes ops concurrently.
_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("cf_token", default=None)


@contextlib.contextmanager
def use_token(token: str | None):
    """Run the enclosed Cloudflare calls with `token` (a specific pool
    account's API token). None restores the settings.cloudflare_api_token
    default. Nestable and exception-safe."""
    reset = _token_var.set(token)
    try:
        yield
    finally:
        _token_var.reset(reset)


def _active_token() -> str:
    return _token_var.get() or settings.cloudflare_api_token

# Cloudflare stores "auto" TTL as the literal value 1 (and forces it for
# proxied records). Reads translate 1 back to DEFAULT_TTL so every consumer
# of the PowerDNS-shape dict (UI forms, backup manifests replayed through
# handlers_dns.set_record's ttl >= 60 validation) keeps working unchanged.
AUTO_TTL = 1

# Statuses worth an automatic retry: rate limiting + transient API/edge
# errors. Other 4xx (auth, validation, zone-conflict 1061/1097) are never
# retried -- they would fail identically every time.
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5

PROXYABLE_TYPES = {"A", "AAAA", "CNAME"}

# Test hook: tests assign an httpx.MockTransport here so no test ever
# talks to the real API (mirrors how CI treats PowerDNS -- no live calls).
_transport: httpx.BaseTransport | None = None


class CloudflareError(Exception):
    def __init__(self, status_code: int, detail: str, cf_code: int | None = None):
        self.status_code = status_code
        self.detail = detail
        self.cf_code = cf_code
        code_part = f" (code {cf_code})" if cf_code else ""
        super().__init__(f"Cloudflare API error {status_code}{code_part}: {detail}")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        headers={
            "Authorization": f"Bearer {_active_token()}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
        transport=_transport,
    )


def _error_from_response(resp: httpx.Response) -> CloudflareError:
    detail = resp.text
    cf_code = None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        errors = body.get("errors") or []
        if errors and isinstance(errors[0], dict):
            cf_code = errors[0].get("code")
            detail = "; ".join(str(e.get("message", "")) for e in errors if isinstance(e, dict))
    return CloudflareError(resp.status_code, detail, cf_code)


def _request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
    last_error: CloudflareError | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        try:
            with _client() as client:
                resp = client.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            last_error = CloudflareError(0, f"transport error: {exc}")
            continue
        if resp.status_code in RETRY_STATUSES:
            last_error = _error_from_response(resp)
            continue
        try:
            body = resp.json()
        except ValueError:
            raise _error_from_response(resp)
        if resp.status_code >= 400 or not body.get("success", False):
            raise _error_from_response(resp)
        return body
    assert last_error is not None
    raise last_error


def _paged(path: str, params: dict | None = None) -> list[dict]:
    results: list[dict] = []
    page = 1
    while True:
        body = _request("GET", path, params={**(params or {}), "page": page, "per_page": 100})
        results.extend(body.get("result") or [])
        info = body.get("result_info") or {}
        total_pages = info.get("total_pages") or 1
        if page >= total_pages:
            return results
        page += 1


# --- zone operations --------------------------------------------------------


def get_zone_id(zone: str) -> str | None:
    result = _request("GET", "/zones", params={"name": zone, "per_page": 1}).get("result") or []
    return result[0]["id"] if result else None


def _resolve_zone_id(zone: str, zone_id: str | None) -> str:
    """Callers that hold a CloudflareZone row pass its cf_zone_id to skip
    the lookup roundtrip; everything still works from the bare zone name."""
    zid = zone_id or get_zone_id(zone)
    if not zid:
        raise CloudflareError(404, f"zone '{zone}' not found on Cloudflare", None)
    return zid


def zone_exists(zone: str) -> bool:
    return get_zone_id(zone) is not None


def create_zone(zone: str, account_id: str | None = None) -> dict:
    """POST /zones. Cloudflare assigns the zone its nameserver pair itself
    (unlike powerdns.create_zone, which takes ours as an argument) and the
    zone starts 'pending' until the registrar delegates to that pair.
    Returns {id, status, name_servers}. `account_id` is the Cloudflare
    account to create the zone under -- the pool account's id (Phase 2+3);
    falls back to the legacy single-token settings.cloudflare_account_id."""
    payload: dict = {"name": zone, "type": "full"}
    acct = account_id or settings.cloudflare_account_id
    if acct:
        payload["account"] = {"id": acct}
    result = _request("POST", "/zones", json_body=payload)["result"]
    return {
        "id": result["id"],
        "status": result.get("status", "pending"),
        "name_servers": result.get("name_servers") or [],
    }


def delete_zone(zone: str, zone_id: str | None = None) -> None:
    zid = zone_id or get_zone_id(zone)
    if zid is None:
        return  # already gone -- matches powerdns.delete_zone's 404 tolerance
    try:
        _request("DELETE", f"/zones/{zid}")
    except CloudflareError as exc:
        if exc.status_code != 404:
            raise


def get_zone(zone: str, zone_id: str | None = None) -> dict:
    zid = _resolve_zone_id(zone, zone_id)
    return _request("GET", f"/zones/{zid}")["result"]


def get_zone_status(zone: str, zone_id: str | None = None) -> dict:
    result = get_zone(zone, zone_id)
    return {
        "id": result["id"],
        "status": result.get("status", "pending"),
        "name_servers": result.get("name_servers") or [],
    }


def activation_check(zone: str, zone_id: str | None = None) -> None:
    """Ask Cloudflare to re-check registrar NS delegation now instead of on
    its own poll schedule -- the panel's "Check activation" button."""
    zid = _resolve_zone_id(zone, zone_id)
    _request("PUT", f"/zones/{zid}/activation_check")


def set_ssl_mode(zone: str, mode: str, zone_id: str | None = None) -> None:
    """mode: 'full' at activation (works against the self-signed bootstrap
    cert), 'strict' once an LE cert is live (plan SS1.6)."""
    zid = _resolve_zone_id(zone, zone_id)
    _request("PATCH", f"/zones/{zid}/settings/ssl", json_body={"value": mode})


def get_ssl_mode(zone: str, zone_id: str | None = None) -> str | None:
    """Current edge SSL mode ('off'|'flexible'|'full'|'strict'). Lives at the
    zone-settings endpoint, NOT in the zone object -- used by the admin zone
    overview (Phase 2+3 feature 8)."""
    zid = _resolve_zone_id(zone, zone_id)
    result = _request("GET", f"/zones/{zid}/settings/ssl").get("result") or {}
    return result.get("value")


def purge_cache(zone: str, zone_id: str | None = None) -> None:
    zid = _resolve_zone_id(zone, zone_id)
    _request("POST", f"/zones/{zid}/purge_cache", json_body={"purge_everything": True})


def list_zones(params: dict | None = None) -> list[dict]:
    return _request("GET", "/zones", params={"per_page": 50, **(params or {})}).get("result") or []


def get_ip_ranges() -> dict:
    """GET /ips -- the edge ranges the real-IP/fail2ban/UFW rails trust."""
    result = _request("GET", "/ips")["result"]
    return {
        "ipv4_cidrs": result.get("ipv4_cidrs") or [],
        "ipv6_cidrs": result.get("ipv6_cidrs") or [],
        "etag": result.get("etag", ""),
    }


def verify_token() -> dict:
    return _request("GET", "/user/tokens/verify")["result"]


# --- record translation -----------------------------------------------------
# PowerDNS-style content strings (what shared.validation / handlers_dns
# produce and what powerdns.list_records returns) <-> Cloudflare record
# fields. The write side and read side MUST stay inverse of each other --
# upsert_record's reconcile matches existing records by round-tripping both
# sides through _canonical_value.


def _record_name(zone: str, subdomain: str) -> str:
    if not subdomain or subdomain == "@":
        return zone
    return f"{subdomain}.{zone}"


def _unescape(inner: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner):
            out.append(inner[i + 1])
            i += 2
        else:
            out.append(inner[i])
            i += 1
    return "".join(out)


def _unquote_txt(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return _unescape(v[1:-1])
    return value


def _quote_txt(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _to_cf_payload(rtype: str, name: str, value: str, ttl: int, proxied: bool) -> dict:
    """One PowerDNS-style value -> one Cloudflare record payload."""
    if rtype not in PROXYABLE_TYPES:
        proxied = False
    payload: dict = {"type": rtype, "name": name, "ttl": AUTO_TTL if proxied else max(int(ttl), 60)}
    if rtype in PROXYABLE_TYPES:
        payload["proxied"] = bool(proxied)
    if rtype in ("CNAME", "NS", "PTR"):
        payload["content"] = value.rstrip(".")
    elif rtype == "TXT":
        payload["content"] = _unquote_txt(value)
    elif rtype == "MX":
        parts = value.split()
        if len(parts) != 2 or not parts[0].isdigit():
            raise CloudflareError(400, f"MX value '{value}' must be '<priority> <host>'", None)
        payload["priority"] = int(parts[0])
        payload["content"] = parts[1].rstrip(".")
    elif rtype == "SRV":
        parts = value.split()
        if len(parts) != 4:
            raise CloudflareError(400, f"SRV value '{value}' must be '<prio> <weight> <port> <target>'", None)
        payload["data"] = {
            "priority": int(parts[0]),
            "weight": int(parts[1]),
            "port": int(parts[2]),
            "target": parts[3].rstrip("."),
        }
    elif rtype == "CAA":
        parts = value.split(None, 2)
        if len(parts) != 3:
            raise CloudflareError(400, f"CAA value '{value}' must be '<flags> <tag> <value>'", None)
        payload["data"] = {"flags": int(parts[0]), "tag": parts[1], "value": _unquote_txt(parts[2])}
    else:
        payload["content"] = value
    return payload


def _from_cf_record(rec: dict) -> str:
    """One Cloudflare record -> the PowerDNS-style content string."""
    rtype = rec.get("type", "")
    content = rec.get("content") or ""
    data = rec.get("data") or {}
    if rtype in ("CNAME", "NS", "PTR"):
        return content.rstrip(".") + "."
    if rtype == "TXT":
        return _quote_txt(_unquote_txt(content))
    if rtype == "MX":
        return f"{rec.get('priority', 0)} {content.rstrip('.')}."
    if rtype == "SRV":
        if data:
            target = str(data.get("target", "")).rstrip(".")
            return f"{data.get('priority', 0)} {data.get('weight', 0)} {data.get('port', 0)} {target}."
        return content
    if rtype == "CAA":
        if data:
            return f"{data.get('flags', 0)} {data.get('tag', '')} {_quote_txt(str(data.get('value', '')))}"
        return content
    return content


def _canonical_value(rtype: str, value: str) -> str:
    """Round-trip a caller-supplied value through both translations so the
    reconcile in upsert_record compares like with like regardless of minor
    formatting differences (trailing dots, TXT quoting)."""
    payload = _to_cf_payload(rtype, "x", value, DEFAULT_TTL, False)
    return _from_cf_record(payload)


# --- record operations ------------------------------------------------------


def upsert_record(
    zone: str,
    subdomain: str,
    rtype: str,
    values: list[str],
    ttl: int = DEFAULT_TTL,
    proxied: bool = False,
    zone_id: str | None = None,
) -> None:
    """REPLACE semantics matching powerdns.upsert_record: after this call
    the (name, type) rrset contains exactly `values`. Implemented as
    create-missing / patch-changed / delete-extra over Cloudflare's
    one-object-per-value model."""
    if rtype not in PROXYABLE_TYPES:
        proxied = False
    zid = _resolve_zone_id(zone, zone_id)
    name = _record_name(zone, subdomain)

    existing = [
        r
        for r in _paged(f"/zones/{zid}/dns_records", params={"name": name, "type": rtype})
        if r.get("name", "").rstrip(".") == name and r.get("type") == rtype
    ]
    existing_by_value: dict[str, list[dict]] = {}
    for rec in existing:
        existing_by_value.setdefault(_from_cf_record(rec), []).append(rec)

    matched_ids: set[str] = set()
    for value in values:
        payload = _to_cf_payload(rtype, name, value, ttl, proxied)
        bucket = existing_by_value.get(_canonical_value(rtype, value)) or []
        rec = bucket.pop(0) if bucket else None
        if rec is None:
            _request("POST", f"/zones/{zid}/dns_records", json_body=payload)
        else:
            matched_ids.add(rec["id"])
            ttl_changed = rec.get("ttl") != payload["ttl"]
            proxied_changed = rtype in PROXYABLE_TYPES and bool(rec.get("proxied")) != payload["proxied"]
            if ttl_changed or proxied_changed:
                _request("PATCH", f"/zones/{zid}/dns_records/{rec['id']}", json_body=payload)

    for rec in existing:
        if rec["id"] not in matched_ids:
            _request("DELETE", f"/zones/{zid}/dns_records/{rec['id']}")


def delete_record(zone: str, subdomain: str, rtype: str, zone_id: str | None = None) -> None:
    zid = _resolve_zone_id(zone, zone_id)
    name = _record_name(zone, subdomain)
    for rec in _paged(f"/zones/{zid}/dns_records", params={"name": name, "type": rtype}):
        if rec.get("name", "").rstrip(".") == name and rec.get("type") == rtype:
            _request("DELETE", f"/zones/{zid}/dns_records/{rec['id']}")


def list_records(zone: str, zone_id: str | None = None) -> list[dict]:
    """Same shape as powerdns.list_records -- {name, type, ttl, values} --
    plus `proxied` (true if any record in the rrset is proxied). Cloudflare
    returns one object per value; group them back into rrsets."""
    zid = _resolve_zone_id(zone, zone_id)
    rrsets: dict[tuple[str, str], dict] = {}
    for rec in _paged(f"/zones/{zid}/dns_records"):
        if rec.get("type") in ("SOA",):
            continue
        key = (rec.get("name", "").rstrip("."), rec.get("type", ""))
        ttl = rec.get("ttl", DEFAULT_TTL)
        entry = rrsets.setdefault(
            key,
            {
                "name": key[0],
                "type": key[1],
                "ttl": DEFAULT_TTL if ttl == AUTO_TTL else ttl,
                "values": [],
                "proxied": False,
            },
        )
        entry["values"].append(_from_cf_record(rec))
        if rec.get("proxied"):
            entry["proxied"] = True
    return list(rrsets.values())
