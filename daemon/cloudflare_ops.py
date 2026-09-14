"""Cloudflare op handlers (docs/PLAN-cloudflare.md).

daemon/cloudflare.py is the pure API client (mirroring how powerdns.py is
a pure client); this module is the handler layer server.py registers under
cf.* -- the same split every other subsystem here uses (client/engine
module + handler-shaped functions taking a params dict).

Zone lifecycle (plan SS1.4): cf.zone_enable creates the Cloudflare zone,
seeds it from PowerDNS, and records the CF-assigned nameserver pair the
customer must set at their registrar. While the zone is 'pending',
PowerDNS stays authoritative and panel writes go to PowerDNS only
(daemon/dnsprovider.py routes on the row's status) -- so activation is a
RESYNC BARRIER, not dual-write: the pending->active transition does a
full one-shot PowerDNS->Cloudflare resync, then routing flips. The
PowerDNS zone is kept (stale) as the revert target; cf.zone_disable
resyncs Cloudflare->PowerDNS, drops the row, and tells the user to flip
NS back.
"""
from __future__ import annotations

from daemon import dns_operations

import json
import logging
import time
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, CloudflareAccount, CloudflareSettings, CloudflareZone, DnsZone, Domain
from shared.validation import validate_domain

from daemon import cloudflare, cloudflare_accounts, events, powerdns
from daemon.dns_zone_lookup import label_within_zone

logger = logging.getLogger("borond.cloudflare")


def _zone_token(row: CloudflareZone) -> str | None:
    """The decrypted API token for the pool account serving this zone, or
    None (legacy single-token zones -> cloudflare.use_token(None) falls back
    to settings.cloudflare_api_token). Every per-zone Cloudflare call wraps
    its client access in `with cloudflare.use_token(_zone_token(row)):` so
    multi-account zones always hit the right account."""
    return cloudflare_accounts.token_for_id(getattr(row, "cf_account_id", None))


def _preferred_ssl_mode(zone: str) -> str:
    """Phase 2+3 feature 5: 'strict' when an LE cert is already live for the
    zone apex (browser-trusted origin cert -> Full (strict) is safe and is
    the goal's target end state), else 'full' against the self-signed
    bootstrap cert (upgraded to strict by the SSL deploy hook once a real
    cert lands). Path mirrors daemon.ols.letsencrypt_cert_paths."""
    return "strict" if Path(f"/etc/letsencrypt/live/{zone}/fullchain.pem").exists() else "full"

# The ranges refresh cron (Phase 2) runs daily; anything older than this is
# stale enough to surface -- generous so one missed cron run isn't an alert.
RANGES_STALE_AFTER_SECONDS = 3 * 24 * 3600


def _ranges_file_status() -> dict:
    path = Path(settings.cloudflare_ranges_file)
    status: dict = {"path": str(path), "exists": False, "age_seconds": None, "stale": None}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return status
    age = max(0, int(time.time() - mtime))
    status.update(exists=True, age_seconds=age, stale=age > RANGES_STALE_AFTER_SECONDS)
    try:
        data = json.loads(path.read_text())
        status["ipv4_count"] = len(data.get("ipv4_cidrs") or [])
        status["ipv6_count"] = len(data.get("ipv6_cidrs") or [])
    except (OSError, ValueError):
        status["stale"] = True  # unreadable/corrupt is as bad as missing
    return status


def health(params: dict) -> dict:
    """cf.health: token verify + API reachability + ranges-file age, for the
    admin health page card. `ok` deliberately ignores the ranges file until
    the Phase 2 rails exist -- it's informational here (the Phase 0 gate is
    "health green with the real token", before any cron is installed).

    Phase 2+3: reports the account pool summary and verifies against the
    first pool account when one exists, falling back to the legacy single
    token otherwise -- so a fleet on the pool is health-checked correctly."""
    pool = cloudflare_accounts.list_accounts({})["accounts"]
    result: dict = {
        "configured": bool(settings.cloudflare_api_token) or bool(pool),
        "account_id_set": bool(settings.cloudflare_account_id) or any(a["account_id"] for a in pool),
        "pool": {
            "count": len(pool),
            "total_capacity_remaining": sum(a["capacity_remaining"] for a in pool if a["active"]),
            "accounts": pool,
        },
        "token_valid": None,
        "api_ok": None,
        "ranges_file": _ranges_file_status(),
        "error": None,
        "ok": False,
    }
    if not result["configured"]:
        result["error"] = (
            "no Cloudflare account configured -- add one under admin Cloudflare settings, "
            "or set CLOUDFLARE_API_TOKEN in secrets.env"
        )
        return result

    # Verify against the first pool account (its own token + scoped account),
    # else the legacy single token.
    if pool:
        probe_token = cloudflare_accounts.token_for_id(pool[0]["id"])
        probe_account = pool[0]["account_id"]
    else:
        probe_token = None  # -> settings.cloudflare_api_token via use_token(None)
        probe_account = settings.cloudflare_account_id

    try:
        with cloudflare.use_token(probe_token):
            verify = cloudflare.verify_token()
            result["token_valid"] = verify.get("status") == "active"
            if not result["token_valid"]:
                result["error"] = f"API token status is '{verify.get('status')}', expected 'active'"
            # Reachability of the zone surface with this token's actual scopes
            # (verify_token only proves the token exists, not that it can see
            # zones); filtered to the account when one is configured.
            zone_params = {"per_page": 1}
            if probe_account:
                zone_params["account.id"] = probe_account
            cloudflare.list_zones(zone_params)
            result["api_ok"] = True
    except cloudflare.CloudflareError as exc:
        result["api_ok"] = False
        result["error"] = str(exc)
        return result

    result["ok"] = bool(result["token_valid"] and result["api_ok"])
    return result


# --- admin settings (features 6 + 9) ----------------------------------------


def _settings_row(session) -> CloudflareSettings:
    row = session.get(CloudflareSettings, 1)
    if row is None:
        row = CloudflareSettings(id=1)
        session.add(row)
        session.flush()
    return row


def settings_get(params: dict) -> dict:
    """cf.settings_get: the runtime admin toggles (auto-enable for new
    domains, UFW lockdown state) plus the static default_dns_provider."""
    with write_session() as session:
        row = _settings_row(session)
        return {
            "auto_enable": row.auto_enable,
            "lockdown_enabled": row.lockdown_enabled,
            "default_dns_provider": settings.default_dns_provider,
        }


def settings_set(params: dict) -> dict:
    """cf.settings_set (feature 6): toggle auto-enable. Lockdown is toggled
    through cf.lockdown (feature 9), which also (un)applies the UFW rules --
    it is not a plain flag write, so it's intentionally not settable here."""
    with write_session() as session:
        row = _settings_row(session)
        if "auto_enable" in params:
            row.auto_enable = bool(params["auto_enable"])
        return {"auto_enable": row.auto_enable, "lockdown_enabled": row.lockdown_enabled}


def _lockdown_unsafe_domains() -> list[str]:
    """Domains that would become UNREACHABLE under CF-only lockdown -- any
    provisioned domain not covered by an ACTIVE Cloudflare zone (itself or a
    parent). The lockdown op refuses (without force) when this is non-empty."""
    with write_session() as session:
        active_cf = set(
            session.scalars(select(CloudflareZone.zone).where(CloudflareZone.status == "active")).all()
        )
        domains = [d.domain for d in session.scalars(select(Domain)).all()]
    return [d for d in domains if not any(d == z or d.endswith("." + z) for z in active_cf)]


def lockdown(params: dict) -> dict:
    """cf.lockdown (feature 9): toggle UFW CF-only web mode. Enabling scopes
    80/443 to Cloudflare edge ranges (origin IP unreachable directly);
    SSH + the panel port are never touched. Requires confirm=true and a
    populated ranges file (goal rule: ranges must exist before UFW). Refuses
    (without force) while any provisioned domain isn't on an active CF zone,
    since it would go dark -- and note webmail/pma resolve directly to this
    IP, so lockdown is only safe once everything is behind Cloudflare.
    Fully reversible."""
    from daemon import firewall, ols

    enabled = bool(params.get("enabled"))
    cf_ranges = ols.cloudflare_trusted_ips()

    if enabled:
        if not params.get("confirm"):
            raise RuntimeError("enabling Cloudflare-only lockdown requires confirm=true")
        if not cf_ranges:
            raise RuntimeError(
                "the Cloudflare ranges file is empty/missing -- run cf.refresh_ranges first "
                "(the ranges must exist before UFW is scoped to them)"
            )
        unsafe = _lockdown_unsafe_domains()
        if unsafe and not params.get("force"):
            raise RuntimeError(
                "refusing lockdown -- these domains are not on an active Cloudflare zone and would become "
                f"unreachable: {', '.join(sorted(unsafe)[:10])}"
                + (" ..." if len(unsafe) > 10 else "")
                + ". webmail/pma also resolve directly to this IP. Pass force=true only if every site is "
                "behind Cloudflare."
            )
        firewall.apply_cf_lockdown(cf_ranges)
    else:
        firewall.remove_cf_lockdown()

    with write_session() as session:
        _settings_row(session).lockdown_enabled = enabled
    return {"lockdown_enabled": enabled, "cf_ranges": len(cf_ranges)}


def _auto_enable_on() -> bool:
    """Feature 6: auto-enable Cloudflare for new domains when the admin toggle
    is on OR the operator has flipped default_dns_provider to 'cloudflare'
    (plan SS1.9 / Phase 3)."""
    if settings.default_dns_provider == "cloudflare":
        return True
    with write_session() as session:
        return _settings_row(session).auto_enable


def maybe_auto_enable(domain: str) -> dict | None:
    """Feature 6: right after a new local zone is created, opportunistically
    move it to Cloudflare so the customer sees the NS pair immediately.
    Returns the zone_enable result (with name_servers) or None when
    auto-enable is off / no pool account has capacity / the enable failed
    (a failure here must never fail the zone creation itself)."""
    if not _auto_enable_on():
        return None
    if not cloudflare_accounts.has_capacity():
        logger.info("auto-enable skipped for '%s': no Cloudflare account has capacity", domain)
        return None
    try:
        return zone_enable({"domain": domain})
    except Exception:
        logger.exception("auto-enable of Cloudflare for new domain '%s' failed (zone kept on local DNS)", domain)
        return None


# --- real-IP rails (Phase 2 features 3+4) -----------------------------------


def _any_token() -> str | None:
    """A valid token for account-agnostic calls (GET /ips): the first pool
    account's, else None (-> legacy settings token via use_token(None))."""
    accts = cloudflare_accounts.list_accounts({})["accounts"]
    if accts:
        return cloudflare_accounts.token_for_id(accts[0]["id"])
    return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.chmod(0o644)  # public data (edge CIDRs) -- consumers are all root anyway
    tmp.rename(path)


def refresh_ranges(params: dict) -> dict:
    """cf.refresh_ranges (plan SS1.7, features 3+4): fetch the Cloudflare edge
    IP ranges, materialize them to settings.cloudflare_ranges_file, and -- if
    they changed (or force) -- re-render the OLS real-IP config and the
    fail2ban ignoreip and reload both.

    Ordering guarantees the goal's rule "CF ranges file must exist before the
    OLS reload / fail2ban restart": the file is written FIRST, then consumers
    re-read it. Reload of each consumer is best-effort and isolated, so one
    failing never leaves the ranges file half-updated."""
    force = bool(params.get("force"))
    with cloudflare.use_token(_any_token()):
        fetched = cloudflare.get_ip_ranges()
    new_v4 = sorted(fetched.get("ipv4_cidrs") or [])
    new_v6 = sorted(fetched.get("ipv6_cidrs") or [])
    if not new_v4 and not new_v6:
        raise RuntimeError("Cloudflare returned no IP ranges -- refusing to write an empty ranges file")

    path = Path(settings.cloudflare_ranges_file)
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = {}
    changed = (sorted(old.get("ipv4_cidrs") or []) != new_v4) or (sorted(old.get("ipv6_cidrs") or []) != new_v6)

    _atomic_write_json(
        path,
        {"ipv4_cidrs": new_v4, "ipv6_cidrs": new_v6, "etag": fetched.get("etag", ""), "fetched_at": int(time.time())},
    )

    ols_reloaded = False
    fail2ban_updated = False
    if changed or force:
        try:
            from daemon import ols

            ols.refresh_main_config()  # re-reads cloudflare_trusted_ips() from the file just written
            ols_reloaded = True
        except Exception:
            logger.exception("OLS reload after Cloudflare ranges refresh failed")
        try:
            from daemon import fail2ban

            fail2ban_updated = fail2ban.refresh_cloudflare_ignoreip()
        except Exception:
            logger.exception("fail2ban ignoreip refresh failed")

        # Feature 9: if CF-only lockdown is engaged, re-scope the UFW web-port
        # rules to the new range set (adds new CF CIDRs, prunes stale ones).
        try:
            with write_session() as session:
                locked = _settings_row(session).lockdown_enabled
            if locked:
                from daemon import firewall

                firewall.apply_cf_lockdown(new_v4 + new_v6)
        except Exception:
            logger.exception("re-applying CF lockdown after ranges refresh failed")

    return {
        "changed": bool(changed or force),
        "ipv4_count": len(new_v4),
        "ipv6_count": len(new_v6),
        "ols_reloaded": ols_reloaded,
        "fail2ban_updated": fail2ban_updated,
        "ranges_file": str(path),
    }


def _ols_real_ip_configured() -> bool:
    """True when the LIVE OLS config restores the real IP AND trusts exactly
    the current CF ranges -- checks the rendered file rather than the DB so
    rails_status reflects what OLS is actually running."""
    from daemon import ols

    ranges = ols.cloudflare_trusted_ips()
    if not ranges:
        return False
    try:
        conf = Path(ols.HTTPD_CONFIG_PATH).read_text()
    except OSError:
        return False
    if "useIpInProxyHeader" not in conf:
        return False
    return all(f"{cidr}T" in conf for cidr in ranges)


def rails_status(params: dict) -> dict:
    """cf.rails_status (plan Phase 2): are the real-IP rails green? The
    record-level proxied toggle (feature 2) unlocks ONLY when ready is True
    -- the foolproof code gate, not documentation. Green requires: a fresh
    ranges file, the OLS listeners restoring the real IP from the CF trusted
    ranges, and the CF ranges present in the fail2ban ignoreip."""
    from daemon import fail2ban

    rf = _ranges_file_status()
    ranges_ok = bool(rf.get("exists")) and not rf.get("stale")
    ols_ok = _ols_real_ip_configured()
    f2b_ok = fail2ban.cloudflare_ignoreip_configured()
    return {
        "ready": bool(ranges_ok and ols_ok and f2b_ok),
        "ranges_ok": ranges_ok,
        "ols_real_ip": ols_ok,
        "fail2ban_ignoreip": f2b_ok,
        "ranges_file": rf,
    }


# --- zone lifecycle (Phase 1) -----------------------------------------------


def _require_row(zone: str) -> CloudflareZone:
    with write_session() as session:
        row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone))
        if row is None:
            raise RuntimeError(f"zone '{zone}' is not on Cloudflare")
        session.expunge(row)
        return row


def _is_apex_ns(rrset: dict, zone: str) -> bool:
    """Apex NS rrsets never cross providers: Cloudflare assigns its own
    pair (and rejects writes to it), and on revert the local zone keeps
    the ns1/ns2 defaults it always had. Delegated-child NS records inside
    the zone are ordinary records and DO sync."""
    return rrset["type"] == "NS" and rrset["name"] == zone


def _resync_powerdns_to_cloudflare(zone: str, cf_zone_id: str) -> int:
    """Full one-shot resync: after this, Cloudflare's record set matches
    PowerDNS's exactly (minus apex NS/SOA). Each step is an idempotent
    REPLACE upsert, so a half-applied resync converges on retry."""
    pdns_rrsets = powerdns.list_records(zone)
    desired: set[tuple[str, str]] = set()
    for rrset in pdns_rrsets:
        if _is_apex_ns(rrset, zone):
            continue
        label = label_within_zone(rrset["name"], zone)
        cloudflare.upsert_record(
            zone, label, rrset["type"], rrset["values"], ttl=rrset["ttl"], zone_id=cf_zone_id
        )
        desired.add((rrset["name"], rrset["type"]))
    for rrset in cloudflare.list_records(zone, zone_id=cf_zone_id):
        if _is_apex_ns(rrset, zone):
            continue
        if (rrset["name"], rrset["type"]) not in desired:
            cloudflare.delete_record(
                zone, label_within_zone(rrset["name"], zone), rrset["type"], zone_id=cf_zone_id
            )
    return len(desired)


def _resync_cloudflare_to_powerdns(zone: str, cf_zone_id: str) -> int:
    """Revert-path mirror of the above: PowerDNS's record set is made to
    match Cloudflare's (minus apex NS), capturing every edit made while
    Cloudflare was authoritative."""
    cf_rrsets = cloudflare.list_records(zone, zone_id=cf_zone_id)
    desired: set[tuple[str, str]] = set()
    for rrset in cf_rrsets:
        if _is_apex_ns(rrset, zone):
            continue
        label = label_within_zone(rrset["name"], zone)
        powerdns.upsert_record(zone, label, rrset["type"], rrset["values"], ttl=rrset["ttl"])
        desired.add((rrset["name"], rrset["type"]))
    for rrset in powerdns.list_records(zone):
        if _is_apex_ns(rrset, zone):
            continue
        if (rrset["name"], rrset["type"]) not in desired:
            powerdns.delete_record(zone, label_within_zone(rrset["name"], zone), rrset["type"])
    return len(desired)


@dns_operations.serialized
def _activate(zone: str) -> None:
    """The pending->active transition: resync barrier FIRST (so routing
    only flips once Cloudflare's content matches PowerDNS's, including any
    edits made during the pending window), then flip, then best-effort
    zone settings + event."""
    row = _require_row(zone)
    with cloudflare.use_token(_zone_token(row)):
        _resync_powerdns_to_cloudflare(zone, row.cf_zone_id)
        try:
            # Phase 2+3 feature 5: activation sets Full (strict) directly when
            # an LE cert already covers the zone's apex (the common case for a
            # domain that was already served over HTTPS locally); otherwise
            # 'full' works against the self-signed bootstrap cert and the SSL
            # deploy hook upgrades to strict once a cert is live.
            cloudflare.set_ssl_mode(zone, _preferred_ssl_mode(zone), zone_id=row.cf_zone_id)
        except cloudflare.CloudflareError:
            logger.exception("failed to set SSL mode for '%s' (zone still activated)", zone)
    account = None
    with write_session() as session:
        db_row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone))
        if db_row is not None:
            db_row.status = "active"
            account = session.get(Account, db_row.account_id)
            if account is not None:
                session.expunge(account)
    events.emit("dns.zone_activated", account, zone=zone)
    logger.info("zone '%s' is now active on Cloudflare", zone)


@dns_operations.serialized
def zone_enable(params: dict) -> dict:
    """cf.zone_enable: create the zone at Cloudflare, seed it with the
    zone's current PowerDNS records, store the row (pending), and return
    the CF-assigned nameserver pair for the customer to set at their
    registrar."""
    domain = validate_domain(params["domain"])

    with write_session() as session:
        dns_zone = session.scalar(select(DnsZone).where(DnsZone.zone == domain))
        if dns_zone is None:
            raise RuntimeError(f"no Boron-managed DNS zone for '{domain}' -- create one first")
        account_id = dns_zone.account_id
        if session.scalar(select(CloudflareZone).where(CloudflareZone.zone == domain)) is not None:
            raise RuntimeError(f"zone '{domain}' is already on Cloudflare")

    # Phase 2+3 feature 1: pick a pool account (or the legacy single token).
    assignment, reason = cloudflare_accounts.assign_for_new_zone()
    if reason:
        raise RuntimeError(reason)
    token = assignment["token"] if assignment else None
    cf_account_pk = assignment["id"] if assignment else None
    cf_account_id = assignment["account_id"] if assignment else None

    with cloudflare.use_token(token):
        try:
            created = cloudflare.create_zone(domain, account_id=cf_account_id)
        except cloudflare.CloudflareError as exc:
            # 1061/1097: the domain already exists under some other Cloudflare
            # account -- nothing we can fix from here (plan SS2).
            if exc.cf_code in (1061, 1097):
                raise RuntimeError(
                    f"Cloudflare refused the zone: {exc.detail} -- the domain is already registered on "
                    "another Cloudflare account. Remove it there first, or keep local DNS."
                ) from exc
            raise

        # Compensation on any seeding error: delete the CF zone again so a
        # retry starts clean (mirrors add_domain's compensation pattern).
        try:
            _resync_powerdns_to_cloudflare(domain, created["id"])
        except Exception:
            try:
                cloudflare.delete_zone(domain, zone_id=created["id"])
            except cloudflare.CloudflareError:
                logger.exception("compensation delete of half-seeded zone '%s' failed", domain)
            raise

    with write_session() as session:
        session.add(
            CloudflareZone(
                account_id=account_id,
                zone=domain,
                cf_zone_id=created["id"],
                status="pending",
                name_servers=created["name_servers"],
                cf_account_id=cf_account_pk,
            )
        )
        if cf_account_pk is not None:
            cloudflare_accounts._sync_zone_count(session, cf_account_pk)

    # Rare but real: re-adding a zone whose registrar NS already point at
    # Cloudflare comes back active immediately.
    if created["status"] == "active":
        _activate(domain)

    row = _require_row(domain)
    return {"zone": domain, "status": row.status, "name_servers": row.name_servers}


@dns_operations.serialized
def zone_status(params: dict) -> dict:
    """cf.zone_status: report (and, for pending zones, poll) activation.
    params.check_now additionally asks Cloudflare to re-check registrar
    delegation immediately (the panel's "Check activation" button)."""
    domain = validate_domain(params["domain"])
    row = _require_row(domain)
    result = {
        "zone": domain,
        "provider": "cloudflare",
        "status": row.status,
        "name_servers": row.name_servers,
        "activated": False,
    }
    if row.status == "active":
        return result

    with cloudflare.use_token(_zone_token(row)):
        if params.get("check_now"):
            try:
                cloudflare.activation_check(domain, zone_id=row.cf_zone_id)
            except cloudflare.CloudflareError:
                pass  # rate-limited/transient -- the status read below still stands

        status = cloudflare.get_zone_status(domain, zone_id=row.cf_zone_id)
    if status["name_servers"] and status["name_servers"] != row.name_servers:
        with write_session() as session:
            db_row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == domain))
            if db_row is not None:
                db_row.name_servers = status["name_servers"]
        result["name_servers"] = status["name_servers"]

    if status["status"] == "active":
        _activate(domain)
        result["status"] = "active"
        result["activated"] = True
    return result


@dns_operations.serialized
def zone_disable(params: dict) -> dict:
    """cf.zone_disable: revert to local DNS. Resync Cloudflare->PowerDNS
    (captures edits made while active), delete the CF zone, drop the row.
    params.force skips a failing resync (e.g. Cloudflare API down) --
    without it, a resync failure aborts the revert rather than silently
    losing record edits."""
    domain = validate_domain(params["domain"])
    row = _require_row(domain)
    cf_account_pk = getattr(row, "cf_account_id", None)

    with cloudflare.use_token(_zone_token(row)):
        if row.status == "active":
            try:
                _resync_cloudflare_to_powerdns(domain, row.cf_zone_id)
            except cloudflare.CloudflareError:
                if not params.get("force"):
                    raise RuntimeError(
                        f"could not sync '{domain}' records back from Cloudflare -- reverting now could lose "
                        "edits made while Cloudflare was active. Retry later, or pass force=true to revert "
                        "with the last-known local records."
                    )
                logger.exception("forced revert of '%s' without a final Cloudflare resync", domain)

        cf_zone_deleted = True
        try:
            cloudflare.delete_zone(domain, zone_id=row.cf_zone_id)
        except cloudflare.CloudflareError:
            cf_zone_deleted = False
            logger.exception("could not delete Cloudflare zone for '%s' (row dropped anyway)", domain)

    with write_session() as session:
        db_row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == domain))
        if db_row is not None:
            session.delete(db_row)
        if cf_account_pk is not None:
            session.flush()
            cloudflare_accounts._sync_zone_count(session, cf_account_pk)

    nameservers = [f"ns1.{domain}", f"ns2.{domain}"]
    return {
        "zone": domain,
        "status": "reverted",
        "cf_zone_deleted": cf_zone_deleted,
        "nameservers": nameservers,
        "message": (
            "Local DNS is authoritative again. Point the domain's registrar nameservers back to "
            f"{nameservers[0]} and {nameservers[1]}."
        ),
    }


def purge_cache(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    row = _require_row(domain)
    if row.status != "active":
        raise RuntimeError(f"zone '{domain}' is not active on Cloudflare yet")
    with cloudflare.use_token(_zone_token(row)):
        cloudflare.purge_cache(domain, zone_id=row.cf_zone_id)
    with write_session() as session:
        db_row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == domain))
        if db_row is not None:
            import datetime as _dt

            db_row.last_purge_at = _dt.datetime.now(_dt.timezone.utc)
    return {"zone": domain, "status": "purged"}


@dns_operations.serialized
def enable_proxy(params: dict) -> dict:
    """cf.enable_proxy (feature 2): turn the orange cloud ON for every
    proxyable (A/AAAA/CNAME) record in an ACTIVE zone -- the DNS editor's
    per-zone 'proxy everything' action, and how existing active zones are
    updated once the rails go green. MX/TXT/NS are never proxied (Cloudflare
    forbids it and cloudflare.py forces grey anyway). Refuses unless the
    real-IP rails are ready -- the same gate as any single proxied write, so
    a real visitor IP is always restored before traffic goes through the
    proxy. Enabling only; disabling a live domain's proxy needs explicit
    admin action (goal rule)."""
    from daemon import dnsprovider

    domain = validate_domain(params["domain"])
    if not dnsprovider.proxied_allowed():
        raise RuntimeError(
            "the Cloudflare real-IP rails are not ready yet (see cf.rails_status) -- refusing to enable proxy"
        )
    row = _require_row(domain)
    if row.status != "active":
        raise RuntimeError(f"zone '{domain}' is not active on Cloudflare yet")
    changed = 0
    with cloudflare.use_token(_zone_token(row)):
        for rrset in cloudflare.list_records(domain, zone_id=row.cf_zone_id):
            if rrset["type"] not in cloudflare.PROXYABLE_TYPES or _is_apex_ns(rrset, domain):
                continue
            if not rrset.get("proxied"):
                cloudflare.upsert_record(
                    domain, label_within_zone(rrset["name"], domain), rrset["type"],
                    rrset["values"], ttl=rrset["ttl"], proxied=True, zone_id=row.cf_zone_id,
                )
                changed += 1
    return {"zone": domain, "proxied_records": changed}


def upgrade_ssl_strict(domain: str) -> bool:
    """Phase 2+3 feature 5: called from the SSL deploy hook after an LE cert
    lands. If the zone is Cloudflare-active, move edge SSL from 'full' to
    'strict' (a browser-trusted origin cert now exists, so strict is safe and
    is the goal's target end state). Best-effort; returns False for non-CF /
    non-active zones. Self-contained (own session) so the standalone deploy
    hook can call it outside the daemon process."""
    domain = validate_domain(domain)
    with write_session() as session:
        row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == domain))
        if row is None or row.status != "active":
            return False
        session.expunge(row)
    with cloudflare.use_token(_zone_token(row)):
        cloudflare.set_ssl_mode(domain, "strict", zone_id=row.cf_zone_id)
    logger.info("Cloudflare edge SSL for '%s' upgraded to Full (strict)", domain)
    return True


# --- admin fleet: zone overview + bulk ops (features 7 + 8) -----------------


def zones_overview(params: dict) -> dict:
    """cf.zones_overview (feature 8): every Cloudflare zone across all pool
    accounts with its status, which hosting account + which CF pool account
    serves it, the NS pair, and last cache purge. Cheap by default (pure DB,
    sortable/filterable client-side). params.live=true additionally fetches
    the per-zone proxy status + edge SSL mode from the API (one call each --
    opt-in because it's O(zones) HTTPS round-trips)."""
    live = bool(params.get("live"))
    with write_session() as session:
        rows = session.scalars(select(CloudflareZone).order_by(CloudflareZone.zone)).all()
        accounts = {a.id: a.name for a in session.scalars(select(CloudflareAccount)).all()}
        host = {a.id: a.username for a in session.scalars(select(Account)).all()}
        snapshot = [
            {
                "zone": r.zone,
                "status": r.status,
                "hosting_account": host.get(r.account_id),
                "cf_account": accounts.get(r.cf_account_id) if r.cf_account_id else None,
                "name_servers": r.name_servers,
                "last_purge_at": r.last_purge_at.isoformat() if r.last_purge_at else None,
                "cf_zone_id": r.cf_zone_id,
                "cf_account_id": r.cf_account_id,
                "proxied": None,
                "ssl_mode": None,
            }
            for r in rows
        ]
    if live:
        for entry in snapshot:
            if entry["status"] != "active":
                continue
            token = cloudflare_accounts.token_for_id(entry["cf_account_id"])
            try:
                with cloudflare.use_token(token):
                    recs = cloudflare.list_records(entry["zone"], zone_id=entry["cf_zone_id"])
                    entry["proxied"] = any(r.get("proxied") for r in recs)
                    entry["ssl_mode"] = cloudflare.get_ssl_mode(entry["zone"], zone_id=entry["cf_zone_id"])
            except cloudflare.CloudflareError:
                logger.exception("live overview fetch failed for '%s'", entry["zone"])
    return {"zones": snapshot, "count": len(snapshot)}


def bulk_purge(params: dict) -> dict:
    """cf.bulk_purge (feature 8): purge the CDN cache for every active zone
    (or the provided `domains` list). Best-effort per zone -- one failure
    doesn't stop the rest; each is reported."""
    domains = params.get("domains")
    with write_session() as session:
        q = select(CloudflareZone.zone).where(CloudflareZone.status == "active")
        if domains:
            q = q.where(CloudflareZone.zone.in_(domains))
        zones = list(session.scalars(q).all())
    results = []
    for zone in zones:
        try:
            purge_cache({"domain": zone})
            results.append({"zone": zone, "status": "purged"})
        except Exception as exc:
            logger.exception("bulk purge failed for '%s'", zone)
            results.append({"zone": zone, "status": "error", "error": str(exc)})
    return {"purged": sum(1 for r in results if r["status"] == "purged"), "total": len(zones), "results": results}


def purge_account_zones(account: Account) -> list[dict]:
    """SUSPEND_HOOKS / UNSUSPEND_HOOKS entry -- best-effort purge of every
    active Cloudflare zone this account owns (same per-zone try/except
    shape as bulk_purge above, scoped to one account), so a proxied,
    edge-cached suspended site doesn't keep serving stale content from
    Cloudflare's cache even after the origin's own LSCache is cleared.
    Deliberately never raises: a flaky Cloudflare API call must not block
    the rest of suspend/unsuspend (the account's Linux user is already
    locked/unlocked by the time hooks run, unlike TERMINATE_HOOKS' errors
    list, suspend/unsuspend has no equivalent partial-failure surface to
    report into -- silently continuing is safer than aborting mid-suspend
    over a third-party API hiccup). Idempotent/silent for accounts with no
    domains or no Cloudflare zones at all."""
    with write_session() as session:
        domains = list(session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all())
        if not domains:
            return []
        zones = list(
            session.scalars(
                select(CloudflareZone.zone).where(CloudflareZone.status == "active", CloudflareZone.zone.in_(domains))
            ).all()
        )
    results = []
    for zone in zones:
        try:
            purge_cache({"domain": zone})
            results.append({"zone": zone, "status": "purged"})
        except Exception:
            logger.exception("suspend/unsuspend-triggered Cloudflare purge failed for zone '%s'", zone)
            results.append({"zone": zone, "status": "error"})
    return results


def bulk_migrate(params: dict) -> dict:
    """cf.bulk_migrate (feature 7): move every PowerDNS-only zone to Cloudflare,
    ONE AT A TIME with verification between each. Per zone: create the CF zone,
    seed records, record the NS pair. Does NOT flip registrar nameservers --
    the customer does that (the zone sits pending until they do). Stops at the
    first failure and reports progress so a partial run is transparent.

    params.limit caps how many are attempted in one call (the async UI drives
    it in batches); params.domains restricts to a specific set."""
    only = set(params.get("domains") or [])
    try:
        limit = int(params.get("limit", 0)) or None
    except (TypeError, ValueError):
        limit = None

    with write_session() as session:
        already_cf = {z for z in session.scalars(select(CloudflareZone.zone)).all()}
        candidates = [
            z for z in session.scalars(select(DnsZone.zone).order_by(DnsZone.zone)).all()
            if z not in already_cf and (not only or z in only)
        ]
    if limit:
        candidates = candidates[:limit]

    results = []
    stopped = False
    for zone in candidates:
        try:
            enabled = zone_enable({"domain": zone})
            results.append({"zone": zone, "status": "prepared", "name_servers": enabled["name_servers"]})
        except Exception as exc:
            logger.exception("bulk migrate stopped at '%s'", zone)
            results.append({"zone": zone, "status": "error", "error": str(exc)})
            stopped = True
            break  # stop on first failure (goal: "Stop on failure, report progress")
    return {
        "attempted": len(results),
        "prepared": sum(1 for r in results if r["status"] == "prepared"),
        "remaining": len(candidates) - len(results),
        "stopped_on_error": stopped,
        "results": results,
    }


def reconcile_pending_zones() -> int:
    """Poll every pending zone once -- called at daemon startup and by the
    scripts/cloudflare_zone_check.py cron so activation is detected within
    ~15 minutes of the registrar NS flip, without needing a panel visit."""
    with write_session() as session:
        zones = session.scalars(select(CloudflareZone.zone).where(CloudflareZone.status == "pending")).all()
    activated = 0
    for zone in zones:
        try:
            if zone_status({"domain": zone}).get("activated"):
                activated += 1
        except Exception:
            logger.exception("activation poll failed for pending zone '%s'", zone)
    if zones:
        logger.info("polled %d pending Cloudflare zone(s), %d newly active", len(zones), activated)
    return activated


@dns_operations.serialized
def terminate_account_cloudflare(account: Account) -> None:
    """TERMINATE_HOOKS entry: delete this account's Cloudflare zones via
    the API + their rows. Idempotent and best-effort on the API side --
    an unreachable Cloudflare must never block account termination."""
    with write_session() as session:
        rows = session.scalars(select(CloudflareZone).where(CloudflareZone.account_id == account.id)).all()
        zones = [(r.zone, r.cf_zone_id, r.cf_account_id) for r in rows]
    for zone, cf_zone_id, cf_account_pk in zones:
        try:
            with cloudflare.use_token(cloudflare_accounts.token_for_id(cf_account_pk)):
                cloudflare.delete_zone(zone, zone_id=cf_zone_id)
        except cloudflare.CloudflareError:
            logger.exception("could not delete Cloudflare zone '%s' during termination", zone)
        with write_session() as session:
            row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone))
            if row is not None:
                session.delete(row)
            if cf_account_pk is not None:
                session.flush()
                cloudflare_accounts._sync_zone_count(session, cf_account_pk)
