"""PowerDNS REST API client.

ARCHITECTURE.md SS6/SS7 + RESEARCH.md SS6: PowerDNS's own docs say not to
write its SQL schema directly -- use the REST API or pdnsutil. We use the
REST API exclusively. PowerDNS validates and atomically applies or rejects
each zone/rrset mutation server-side, which is the validate-before-apply
property the rest of Boron gets from daemon/configtx.py -- there's
nothing for configtx to wrap here, PowerDNS already does it.
"""
from __future__ import annotations

import httpx

from shared.config import settings

DEFAULT_TTL = 3600


class PowerDnsError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"PowerDNS API error {status_code}: {detail}")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.powerdns_api_url,
        headers={"X-API-Key": settings.powerdns_api_key, "Content-Type": "application/json"},
        timeout=10.0,
    )


def _zone_id(zone: str) -> str:
    # PowerDNS zone names are canonical (trailing dot); the API's zone_id is
    # the same canonical name.
    return zone if zone.endswith(".") else f"{zone}."


def zone_exists(zone: str) -> bool:
    with _client() as client:
        resp = client.get(f"/servers/{settings.powerdns_server_id}/zones/{_zone_id(zone)}")
        return resp.status_code == 200


def create_zone(zone: str, ns_records: list[str]) -> dict:
    """Create a new authoritative zone with the given NS records. PowerDNS
    auto-generates the SOA from default-soa-content (configured in
    /etc/powerdns/pdns.d/boron.conf) unless we override it."""
    fqdn = _zone_id(zone)
    payload = {
        "name": fqdn,
        "kind": "Native",
        "dnssec": False,
        "nameservers": ns_records,
    }
    with _client() as client:
        resp = client.post(f"/servers/{settings.powerdns_server_id}/zones", json=payload)
    if resp.status_code not in (201, 200):
        raise PowerDnsError(resp.status_code, resp.text)
    return resp.json()


def delete_zone(zone: str) -> None:
    with _client() as client:
        resp = client.delete(f"/servers/{settings.powerdns_server_id}/zones/{_zone_id(zone)}")
    if resp.status_code not in (204, 404):
        raise PowerDnsError(resp.status_code, resp.text)


def get_zone(zone: str) -> dict:
    with _client() as client:
        resp = client.get(f"/servers/{settings.powerdns_server_id}/zones/{_zone_id(zone)}")
    if resp.status_code != 200:
        raise PowerDnsError(resp.status_code, resp.text)
    return resp.json()


def _record_name(zone: str, subdomain: str) -> str:
    fqdn = _zone_id(zone)
    if not subdomain or subdomain == "@":
        return fqdn
    return f"{subdomain}.{fqdn}"


def upsert_record(zone: str, subdomain: str, rtype: str, values: list[str], ttl: int = DEFAULT_TTL) -> None:
    """REPLACE semantics: this becomes the complete rrset for (name, type) --
    matches how Boron's UI/API models "edit this A record", not an
    append. Trailing dots required by PowerDNS for CNAME/MX/NS-type
    targets are added by the caller (shared.validation / handlers_dns)."""
    name = _record_name(zone, subdomain)
    payload = {
        "rrsets": [
            {
                "name": name,
                "type": rtype,
                "ttl": ttl,
                "changetype": "REPLACE",
                "records": [{"content": v, "disabled": False} for v in values],
            }
        ]
    }
    with _client() as client:
        resp = client.patch(f"/servers/{settings.powerdns_server_id}/zones/{_zone_id(zone)}", json=payload)
    if resp.status_code != 204:
        raise PowerDnsError(resp.status_code, resp.text)


def delete_record(zone: str, subdomain: str, rtype: str) -> None:
    name = _record_name(zone, subdomain)
    payload = {"rrsets": [{"name": name, "type": rtype, "changetype": "DELETE"}]}
    with _client() as client:
        resp = client.patch(f"/servers/{settings.powerdns_server_id}/zones/{_zone_id(zone)}", json=payload)
    if resp.status_code != 204:
        raise PowerDnsError(resp.status_code, resp.text)


def list_records(zone: str) -> list[dict]:
    zone_data = get_zone(zone)
    records = []
    for rrset in zone_data.get("rrsets", []):
        if rrset["type"] in ("SOA",):
            continue  # auto-managed, not part of Boron's editable A/AAAA/CNAME/MX/TXT surface
        records.append(
            {
                "name": rrset["name"].rstrip("."),
                "type": rrset["type"],
                "ttl": rrset["ttl"],
                "values": [r["content"] for r in rrset["records"]],
            }
        )
    return records
