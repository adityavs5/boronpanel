"""Persistent, idempotent server setup wizard operations."""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import shutil
from pathlib import Path

from sqlalchemy import select

from daemon import cloudflare_accounts, dnscluster, dnsprovider, dnssetup, geoip, panel_config, panel_tls, ssl as ssl_ops
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import ServerSetupState
from shared.validation import ValidationError, validate_domain, validate_email_address

STEPS = {
    1: "Server identity",
    2: "DNS operating mode",
    3: "DNS provider",
    4: "Service hostnames and records",
    5: "DNS verification",
    6: "TLS certificates",
    7: "MaxMind GeoLite2",
    8: "Review and finish",
}
_SECRET_MARKERS = ("token", "credential", "password", "secret", "license_key", "recovery_key")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _public(value):
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if not any(marker in key.lower() for marker in _SECRET_MARKERS)}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _state_dict(row: ServerSetupState) -> dict:
    return {
        "current_step": row.current_step, "completed": row.completed,
        "contact_email": row.contact_email, "maxmind_skipped": row.maxmind_skipped,
        "draft": dict(row.draft or {}), "step_results": dict(row.step_results or {}),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "steps": [{"number": number, "title": title} for number, title in STEPS.items()],
    }


def _get_or_create(db) -> ServerSetupState:
    row = db.get(ServerSetupState, 1)
    if row is None:
        row = ServerSetupState(id=1)
        db.add(row)
        db.flush()
    return row


def status(_params: dict | None = None) -> dict:
    with write_session() as db:
        row = _get_or_create(db)
        result = _state_dict(row)
    result["system"] = {
        "panel_hostname": settings.panel_hostname,
        "server_public_ip": settings.server_public_ip,
        "webmail_hostname": settings.webmail_hostname,
        "pma_hostname": settings.pma_hostname,
        "panel_port": settings.api_bind_port,
        "geoip_configured": geoip.is_configured(),
        "panel_certificate": panel_tls.certificate_status(),
    }
    result["dns"] = dnssetup.get_settings({})
    return result


def _record(step: int, state: str, result: dict, *, draft: dict | None = None,
            contact_email: str | None = None, maxmind_skipped: bool | None = None) -> None:
    with write_session() as db:
        row = _get_or_create(db)
        results = dict(row.step_results or {})
        results[str(step)] = {"state": state, "at": _now(), **_public(result)}
        row.step_results = results
        if draft:
            merged = dict(row.draft or {})
            merged.update(_public(draft))
            row.draft = merged
        if contact_email is not None:
            row.contact_email = contact_email
        if maxmind_skipped is not None:
            row.maxmind_skipped = maxmind_skipped
        if state == "completed":
            row.current_step = max(row.current_step, min(8, step + 1))
        if step == 8 and state == "completed":
            row.completed = True


def _service_hosts(params: dict) -> dict[str, str]:
    hosts = {}
    for key in ("panel_hostname", "webmail_hostname", "pma_hostname"):
        raw = str(params.get(key) or getattr(settings, key) or "").strip()
        if raw:
            hosts[key] = validate_domain(raw)
    if len(set(hosts.values())) != len(hosts):
        raise ValidationError("Panel, webmail, and phpMyAdmin hostnames must be distinct")
    return hosts


def _service_record_plan(params: dict) -> dict:
    hosts = _service_hosts(params)
    address = str(params.get("server_public_ip") or settings.server_public_ip or "")
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise ValidationError("A valid server public IP is required before creating service records") from exc
    rtype = "AAAA" if ip.version == 6 else "A"
    rows = []
    for purpose, hostname in hosts.items():
        zone = find_managed_zone(hostname)
        if zone is None:
            rows.append({"purpose": purpose, "hostname": hostname, "zone": None, "type": rtype,
                         "value": address, "state": "external", "conflicts": [], "proxied": False})
            continue
        current = dnsprovider.list_records(zone)
        exact = [item for item in current if str(item.get("name", "")).rstrip(".") == hostname]
        same = any(item.get("type") == rtype and address in (item.get("values") or []) for item in exact)
        conflicts = [
            {"type": item.get("type"), "values": list(item.get("values") or [])}
            for item in exact if item.get("type") != rtype or address not in (item.get("values") or [])
        ]
        rows.append({
            "purpose": purpose, "hostname": hostname, "zone": zone, "type": rtype,
            "value": address, "state": "present" if same and not conflicts else "conflict" if conflicts else "create",
            "conflicts": conflicts, "proxied": False,
        })
    return {
        "records": rows,
        "panel_port": settings.api_bind_port,
        "panel_dns_only": True,
        "note": f"The panel listens on port {settings.api_bind_port}; service records are created as DNS-only.",
    }


def service_records_preview(params: dict) -> dict:
    return _service_record_plan(params)


def _apply_service_records(params: dict) -> dict:
    plan = _service_record_plan(params)
    conflicts = [row for row in plan["records"] if row["conflicts"]]
    if conflicts and not params.get("replace_conflicts"):
        raise ValidationError("Existing service-host records conflict with this server; review them and explicitly allow replacement")
    applied = []
    for row in plan["records"]:
        if row["zone"] is None:
            continue
        for conflict in row["conflicts"]:
            if conflict["type"] != row["type"]:
                dnsprovider.delete_record(
                    row["zone"], label_within_zone(row["hostname"], row["zone"]), conflict["type"],
                )
        dnsprovider.upsert_record(
            row["zone"], label_within_zone(row["hostname"], row["zone"]),
            row["type"], [row["value"]], proxied=False,
        )
        applied.append(row["hostname"])
    return {**plan, "applied": applied}


def _step1(params: dict) -> tuple[dict, dict]:
    hostname = validate_domain(params.get("panel_hostname"))
    public_ip = str(ipaddress.ip_address(params.get("server_public_ip")))
    email = validate_email_address(params.get("contact_email"))
    result = panel_config.apply_setup_values({
        "panel_hostname": hostname, "server_public_ip": public_ip, "letsencrypt_email": email,
    })
    return result, {"panel_hostname": hostname, "server_public_ip": public_ip, "contact_email": email}


def _step2(params: dict) -> tuple[dict, dict]:
    preview = dnssetup.preview_mode(params)
    if not params.get("confirm"):
        return {"state": "preview", **preview}, {}
    draft = {"dns_mode": preview["requested_mode"], "local_nameservers": preview["local_nameservers"]}
    # Provider credentials belong to the next HTTPS step. Selecting
    # Cloudflare/cluster here must not require putting their secrets first or
    # weakening dnssetup.set_mode's global safety prerequisite.
    if preview["blockers"] and preview["requested_mode"] in {"cloudflare", "cluster"}:
        return {
            "status": "selected", "mode": preview["requested_mode"],
            "provider_required": True, "provider_requirements": preview["blockers"],
            "existing_zones_unchanged": preview["existing_zones_unchanged"],
        }, draft
    result = dnssetup.set_mode(params)
    return result, draft


def _step3(params: dict) -> tuple[dict, dict]:
    with write_session() as db:
        state = _get_or_create(db)
        selected_mode = (state.draft or {}).get("dns_mode")
        local_nameservers = list((state.draft or {}).get("local_nameservers") or [])
    mode = str(selected_mode or dnssetup.current_mode())
    if mode == "cloudflare":
        account_id = str(params.get("account_id") or "")
        existing = next((row for row in cloudflare_accounts.list_accounts({})["accounts"] if row.get("account_id") == account_id), None)
        if existing:
            result = {"status": "already_connected", "account": existing}
        else:
            result = cloudflare_accounts.add_account(params)
        dnssetup.set_mode({"mode": "cloudflare", "local_nameservers": []})
        return result, {"dns_provider_configured": "cloudflare", "cloudflare_account_id": account_id}
    if mode == "cluster":
        result = dnscluster.create_peer(params)
        dnssetup.set_mode({"mode": "cluster", "local_nameservers": local_nameservers})
        return result, {"dns_provider_configured": "cluster", "peer_name": result["name"]}
    nameservers = local_nameservers or dnssetup.local_nameservers()
    if len(nameservers) < 2:
        raise ValidationError("Configure at least two local authoritative nameservers")
    return {"status": "configured", "nameservers": nameservers}, {"dns_provider_configured": "local"}


def _step4(params: dict) -> tuple[dict, dict]:
    hosts = _service_hosts(params)
    if not params.get("confirm"):
        return {"state": "preview", **_service_record_plan(params)}, {}
    panel_config.apply_setup_values(hosts, reload_services=True)
    records = _apply_service_records(params)
    return records, {**hosts, "service_records": records["records"]}


def _resolve_host(hostname: str, rtype: str) -> list[str]:
    result = run(["dig", "+short", rtype, hostname], timeout=20)
    return sorted(line.rstrip(".") for line in result.stdout.splitlines() if line.strip())


def _step5(params: dict) -> tuple[dict, dict]:
    expected = str(params.get("server_public_ip") or settings.server_public_ip or "")
    hosts = _service_hosts(params)
    rtype = "AAAA" if ":" in expected else "A"
    checks = []
    for purpose, hostname in hosts.items():
        observed = _resolve_host(hostname, rtype)
        checks.append({"purpose": purpose, "hostname": hostname, "observed": observed,
                       "expected": expected, "ready": expected in observed})
    ready = bool(checks) and all(item["ready"] for item in checks)
    state = "completed" if ready else "waiting_for_external_dns"
    return {"state": state, "ready": ready, "checks": checks}, {"dns_verified": ready}


def _step6(params: dict) -> tuple[dict, dict]:
    with write_session() as db:
        state = _get_or_create(db)
        verified = (state.step_results or {}).get("5", {}).get("state") == "completed"
    if not verified:
        raise ValidationError("Verify service DNS before issuing certificates")
    email = validate_email_address(params.get("email") or settings.letsencrypt_email)
    requested = params.get("services") or ["panel", "webmail", "phpmyadmin"]
    results = []
    for service in requested:
        if service == "panel":
            results.append({"service": service, **panel_tls.issue_certificate(email)})
        elif service == "webmail" and settings.webmail_hostname:
            results.append({"service": service, **ssl_ops.issue_certificate({"domain": settings.webmail_hostname})})
        elif service == "phpmyadmin" and settings.pma_hostname:
            results.append({"service": service, **ssl_ops.issue_certificate({"domain": settings.pma_hostname})})
        else:
            raise ValidationError(f"Unknown or unconfigured certificate service: {service}")
    return {"certificates": results}, {"certificates_issued": [row["service"] for row in results]}


def _install_maxmind_refresh(license_key: str) -> None:
    target = Path("/etc/boron/maxmind-license")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(license_key)
    tmp.chmod(0o600)
    os.replace(tmp, target)
    source = Path(__file__).resolve().parent.parent / "deploy/boron-geoip.cron"
    if source.is_file():
        shutil.copy2(source, "/etc/cron.d/boron-geoip")
        Path("/etc/cron.d/boron-geoip").chmod(0o644)


def _step7(params: dict) -> tuple[dict, dict]:
    if params.get("skip"):
        return {"geoip_configured": geoip.is_configured(), "skipped": True}, {"maxmind_skipped": True}
    license_key = str(params.get("license_key") or "")
    geoip.download_database(license_key)
    _install_maxmind_refresh(license_key)
    return {"geoip_configured": geoip.is_configured(), "skipped": False}, {"maxmind_skipped": False}


def _step8(_params: dict) -> tuple[dict, dict]:
    with write_session() as db:
        row = _get_or_create(db)
        results = dict(row.step_results or {})
        required = [1, 2, 3, 4, 5, 6, 7]
        missing = [number for number in required if results.get(str(number), {}).get("state") != "completed"]
    if missing:
        raise ValidationError("Complete setup steps: " + ", ".join(str(number) for number in missing))
    return {"status": "completed", "completed_at": _now()}, {}


_HANDLERS = {1: _step1, 2: _step2, 3: _step3, 4: _step4, 5: _step5, 6: _step6, 7: _step7, 8: _step8}


def run_step(params: dict) -> dict:
    try:
        step = int(params.get("step"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Setup step must be an integer from 1 to 8") from exc
    if step not in _HANDLERS:
        raise ValidationError("Setup step must be from 1 to 8")
    try:
        result, draft = _HANDLERS[step](params)
        result_state = result.get("state")
        if result_state == "preview":
            return {"step": step, **result, "wizard": status({})}
        record_state = "completed" if result_state not in {"preview", "waiting_for_external_dns"} else result_state
        _record(
            step, record_state, result, draft=draft,
            contact_email=draft.get("contact_email"),
            maxmind_skipped=draft.get("maxmind_skipped"),
        )
        return {"step": step, **result, "wizard": status({})}
    except Exception as exc:
        _record(step, "failed", {"error": type(exc).__name__})
        raise


def reset_section(params: dict) -> dict:
    step = int(params.get("step", 0))
    if step not in STEPS:
        raise ValidationError("Setup step must be from 1 to 8")
    with write_session() as db:
        row = _get_or_create(db)
        results = dict(row.step_results or {})
        results.pop(str(step), None)
        row.step_results = results
        row.current_step = min(row.current_step, step)
        row.completed = False
    return status({})


def seed_installer_draft(panel_hostname: str, server_public_ip: str, contact_email: str,
                         *, maxmind_skipped: bool) -> None:
    """Seed only non-secret installer facts so first login resumes at DNS."""
    draft = {}
    if panel_hostname:
        draft["panel_hostname"] = validate_domain(panel_hostname)
    if server_public_ip:
        draft["server_public_ip"] = str(ipaddress.ip_address(server_public_ip))
    if contact_email:
        draft["contact_email"] = validate_email_address(contact_email)
    with write_session() as db:
        row = _get_or_create(db)
        row.draft = {**dict(row.draft or {}), **draft}
        row.contact_email = draft.get("contact_email") or row.contact_email
        row.maxmind_skipped = bool(maxmind_skipped)
        if all(key in draft for key in ("panel_hostname", "server_public_ip", "contact_email")):
            results = dict(row.step_results or {})
            results.setdefault("1", {"state": "completed", "at": _now(), "source": "installer"})
            row.step_results = results
            row.current_step = max(row.current_step, 2)
