"""Cloudflare provider endpoints (docs/PLAN-cloudflare.md).

Phase 0: admin-only health probe for the Server Health page card (token
verify + API reachability + edge-ranges file age).
Phase 1: per-zone lifecycle -- enable (create + seed + return the assigned
NS pair), status/check-activation, disable (revert to local DNS), purge
cache. Zone endpoints authorize with require_domain_access, same as every
other DNS endpoint: customers manage their own zones' provider from the
DNS section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin, require_domain_access

api_router = APIRouter(prefix="/api/v1/cloudflare", tags=["cloudflare"])
zone_api_router = APIRouter(prefix="/api/v1/dns/zones/{domain}/cloudflare", tags=["cloudflare"])


@api_router.get("/health")
def cloudflare_health(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.health", identity)


# --- Phase 2+3 features 2/3: real-IP rails + edge ranges (admin) -------------


@api_router.get("/rails")
def rails_status(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.rails_status", identity)


@api_router.post("/ranges/refresh")
def refresh_ranges(force: bool = False, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.refresh_ranges", identity, force=force)


# --- Phase 2+3 feature 6: auto-enable settings (admin) ----------------------


class SettingsBody(BaseModel):
    auto_enable: bool | None = None


@api_router.get("/settings")
def get_settings(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.settings_get", identity)


@api_router.patch("/settings")
def set_settings(body: SettingsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    params = {}
    if body.auto_enable is not None:
        params["auto_enable"] = body.auto_enable
    return call_daemon("cf.settings_set", identity, **params)


# --- Phase 2+3 features 7/8/9: fleet ops (admin) ----------------------------


class BulkMigrateBody(BaseModel):
    domains: list[str] | None = None
    limit: int | None = None


class BulkPurgeBody(BaseModel):
    domains: list[str] | None = None


class LockdownBody(BaseModel):
    enabled: bool
    confirm: bool = False
    force: bool = False


@api_router.get("/zones")
def zones_overview(live: bool = False, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.zones_overview", identity, live=live)


@api_router.post("/bulk-migrate")
def bulk_migrate(body: BulkMigrateBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.bulk_migrate", identity, domains=body.domains, limit=body.limit or 0)


@api_router.post("/bulk-purge")
def bulk_purge(body: BulkPurgeBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.bulk_purge", identity, domains=body.domains)


@api_router.patch("/lockdown")
def lockdown(body: LockdownBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.lockdown", identity, enabled=body.enabled, confirm=body.confirm, force=body.force)


# --- Phase 2+3 feature 1: account pool (admin-only) --------------------------


class AccountAddBody(BaseModel):
    name: str
    api_token: str
    account_id: str
    max_zones: int = 800


class AccountSetBody(BaseModel):
    max_zones: int | None = None
    active: bool | None = None


@api_router.get("/accounts")
def list_accounts(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.account_list", identity)


@api_router.post("/accounts")
def add_account(body: AccountAddBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon(
        "cf.account_add", identity,
        name=body.name, api_token=body.api_token, account_id=body.account_id, max_zones=body.max_zones,
    )


@api_router.patch("/accounts/{account_id}")
def set_account(account_id: int, body: AccountSetBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    params = {"id": account_id}
    if body.max_zones is not None:
        params["max_zones"] = body.max_zones
    if body.active is not None:
        params["active"] = body.active
    return call_daemon("cf.account_set", identity, **params)


@api_router.post("/accounts/{account_id}/test")
def test_account(account_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.account_test", identity, id=account_id)


@api_router.delete("/accounts/{account_id}")
def delete_account(account_id: int, force: bool = False, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cf.account_delete", identity, id=account_id, force=force)


class DisableBody(BaseModel):
    # Skip a failing final Cloudflare->PowerDNS resync (e.g. CF API down)
    # instead of aborting the revert -- see cloudflare_ops.zone_disable.
    force: bool = False


@zone_api_router.post("/enable")
def enable_zone(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("cf.zone_enable", identity, domain=domain)


@zone_api_router.get("/status")
def zone_status(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("cf.zone_status", identity, domain=domain)


@zone_api_router.post("/check")
def check_activation(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("cf.zone_status", identity, domain=domain, check_now=True)


@zone_api_router.post("/disable")
def disable_zone(domain: str, body: DisableBody | None = None, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("cf.zone_disable", identity, domain=domain, force=bool(body and body.force))


@zone_api_router.post("/purge")
def purge_cache(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("cf.purge_cache", identity, domain=domain)


@zone_api_router.post("/proxy-all")
def enable_proxy(domain: str, identity: Identity = Depends(get_identity)):
    # Phase 2+3 feature 2: turn on the orange cloud for every proxyable record
    # in this active zone (rails-gated in the daemon).
    require_domain_access(identity, domain)
    return call_daemon("cf.enable_proxy", identity, domain=domain)
