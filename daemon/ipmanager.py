"""Safe multi-IP inventory and account allocation.

The panel never changes routes or netplan. An operator/provider attaches an
address to the host first; Boron discovers it through psutil and can then use
it for account DNS. This avoids turning a control-panel convenience into a
remote lockout primitive while still supporting shared pools, dedicated IPs,
random allocation, and a chosen default.
"""
from __future__ import annotations

import ipaddress
import re
import secrets

import psutil
from sqlalchemy import delete, func, select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DnsZone, Domain, ServerIp, ServerIpAssignment, ServerIpSettings
from shared.validation import ValidationError, validate_username

_LABEL_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,100}$")
POLICIES = {"primary", "random_shared", "specific"}
MODES = {"shared", "dedicated"}


def _canonical_address(value: str) -> tuple[str, str]:
    try:
        parsed = ipaddress.ip_address(str(value).strip().split("%", 1)[0])
    except ValueError as exc:
        raise ValidationError("Enter a valid IPv4 or IPv6 address") from exc
    if parsed.is_unspecified or parsed.is_loopback or parsed.is_multicast or parsed.is_link_local:
        raise ValidationError("Loopback, link-local, multicast, and unspecified addresses cannot host accounts")
    return str(parsed), "ipv4" if parsed.version == 4 else "ipv6"


def _prefix_length(netmask: str | None, version: int) -> int | None:
    if not netmask:
        return None
    try:
        # psutil may return an expanded IPv6 mask, which ip_network does not
        # accept as a prefix token. Counting a validated contiguous mask works
        # for both families.
        mask = ipaddress.ip_address(netmask)
        if mask.version != version:
            return None
        bits = f"{int(mask):0{mask.max_prefixlen}b}"
        if "01" in bits:
            return None
        return bits.count("1")
    except ValueError:
        return None


def detected_addresses() -> list[dict]:
    found: dict[str, dict] = {}
    for interface, entries in psutil.net_if_addrs().items():
        for entry in entries:
            if entry.family not in (2, 10):  # socket.AF_INET / AF_INET6
                continue
            try:
                address, family = _canonical_address(entry.address)
            except ValidationError:
                continue
            parsed = ipaddress.ip_address(address)
            found[address] = {
                "address": address,
                "family": family,
                "interface": interface,
                "prefix_length": _prefix_length(entry.netmask, parsed.version),
            }
    # NAT installations may expose a configured public address that is not
    # directly present on an interface. Keep the installer-selected primary
    # usable, but require every additional address to be host-detected.
    if settings.server_public_ip:
        try:
            address, family = _canonical_address(settings.server_public_ip)
            found.setdefault(address, {"address": address, "family": family, "interface": "configured-primary", "prefix_length": None})
        except ValidationError:
            pass
    return sorted(found.values(), key=lambda item: (item["family"], ipaddress.ip_address(item["address"])))


def _present_addresses() -> set[str]:
    return {item["address"] for item in detected_addresses()}


def _settings_row(session) -> ServerIpSettings:
    row = session.get(ServerIpSettings, 1)
    if row is None:
        row = ServerIpSettings(id=1)
        session.add(row)
        session.flush()
    return row


def _ip_dict(row: ServerIp, assignments: list[tuple[str, str]] | None = None, present: bool = True) -> dict:
    return {
        "id": row.id,
        "address": row.address,
        "family": row.family,
        "interface": row.interface,
        "prefix_length": row.prefix_length,
        "allocation_mode": row.allocation_mode,
        "label": row.label,
        "active": row.active,
        "present_on_host": present,
        "assignments": [{"username": username, "status": status} for username, status in (assignments or [])],
        "assignment_count": len(assignments or []),
    }


def list_state(params: dict | None = None) -> dict:
    detected = detected_addresses()
    detected_map = {item["address"]: item for item in detected}
    with write_session() as session:
        rows = session.scalars(select(ServerIp).order_by(ServerIp.address)).all()
        settings_row = _settings_row(session)
        grouped: dict[int, list[tuple[str, str]]] = {row.id: [] for row in rows}
        for ip_id, username, status in session.execute(
            select(ServerIpAssignment.server_ip_id, Account.username, Account.status)
            .join(Account, Account.id == ServerIpAssignment.account_id)
            .order_by(Account.username)
        ):
            grouped.setdefault(ip_id, []).append((username, status))
        registered = {row.address for row in rows}
        return {
            "ips": [_ip_dict(row, grouped.get(row.id), row.address in detected_map) for row in rows],
            "detected": [item for item in detected if item["address"] not in registered],
            "policy": {
                "allocation_policy": settings_row.allocation_policy,
                "default_server_ip_id": settings_row.default_server_ip_id,
                "primary_address": settings.server_public_ip or None,
            },
        }


def import_addresses(params: dict) -> dict:
    available = {item["address"]: item for item in detected_addresses()}
    requested = params.get("addresses") or list(available)
    if not isinstance(requested, list) or not requested:
        raise ValidationError("Choose at least one detected address")
    normalized = []
    for raw in requested:
        address, _family = _canonical_address(raw)
        if address not in available:
            raise ValidationError(f"{address} is not currently configured on this host")
        normalized.append(address)
    with write_session() as session:
        existing = {row.address for row in session.scalars(select(ServerIp).where(ServerIp.address.in_(normalized))).all()}
        created = []
        for address in normalized:
            if address in existing:
                continue
            item = available[address]
            row = ServerIp(**item, allocation_mode="shared", active=True)
            session.add(row)
            session.flush()
            created.append(_ip_dict(row))
    return {"imported": created, "count": len(created)}


def update_ip(params: dict) -> dict:
    ip_id = int(params["id"])
    with write_session() as session:
        row = session.get(ServerIp, ip_id)
        if row is None:
            raise ValidationError("Server IP was not found")
        mode = params.get("allocation_mode", row.allocation_mode)
        if mode not in MODES:
            raise ValidationError("allocation_mode must be shared or dedicated")
        count = session.scalar(select(func.count()).select_from(ServerIpAssignment).where(ServerIpAssignment.server_ip_id == row.id)) or 0
        if mode == "dedicated" and count > 1:
            raise ValidationError("Remove extra account assignments before marking this IP dedicated")
        label = params.get("label", row.label)
        label = str(label or "").strip() or None
        if label and not _LABEL_RE.fullmatch(label):
            raise ValidationError("Label contains unsupported characters")
        active = bool(params.get("active", row.active))
        if active and row.address not in _present_addresses():
            raise ValidationError("This address is not currently configured on the host")
        if not active and count:
            raise ValidationError("Remove account assignments before disabling this IP")
        row.allocation_mode = mode
        row.label = label
        row.active = active
        session.flush()
        assignments = session.execute(
            select(Account.username, Account.status).join(ServerIpAssignment, ServerIpAssignment.account_id == Account.id).where(ServerIpAssignment.server_ip_id == row.id)
        ).all()
        return _ip_dict(row, list(assignments), row.address in {x["address"] for x in detected_addresses()})


def delete_ip(params: dict) -> dict:
    ip_id = int(params["id"])
    with write_session() as session:
        row = session.get(ServerIp, ip_id)
        if row is None:
            raise ValidationError("Server IP was not found")
        if session.scalar(select(ServerIpAssignment.id).where(ServerIpAssignment.server_ip_id == ip_id)) is not None:
            raise ValidationError("Remove account assignments before deleting this IP")
        cfg = _settings_row(session)
        if cfg.default_server_ip_id == ip_id:
            raise ValidationError("Choose another new-account policy before deleting the default IP")
        address = row.address
        session.delete(row)
    return {"id": ip_id, "address": address, "status": "removed"}


def set_policy(params: dict) -> dict:
    policy = str(params.get("allocation_policy", "")).strip()
    if policy not in POLICIES:
        raise ValidationError("allocation_policy must be primary, random_shared, or specific")
    raw_id = params.get("default_server_ip_id")
    ip_id = int(raw_id) if raw_id not in (None, "") else None
    with write_session() as session:
        if policy == "specific":
            if ip_id is None:
                raise ValidationError("Choose the IP used for new accounts")
            row = session.get(ServerIp, ip_id)
            if row is None or not row.active:
                raise ValidationError("The selected default IP is unavailable")
            if row.address not in _present_addresses():
                raise ValidationError("The selected default IP is missing from the host")
            if row.allocation_mode != "shared":
                raise ValidationError("A new-account default must be a shared IP")
        elif policy == "random_shared":
            present = _present_addresses()
            available = session.scalars(select(ServerIp).where(ServerIp.active == True, ServerIp.allocation_mode == "shared")).all()  # noqa: E712
            if not any(row.address in present for row in available):
                raise ValidationError("Import and enable at least one shared IP first")
            ip_id = None
        else:
            ip_id = None
        cfg = _settings_row(session)
        cfg.allocation_policy = policy
        cfg.default_server_ip_id = ip_id
        return {"allocation_policy": policy, "default_server_ip_id": ip_id, "primary_address": settings.server_public_ip or None}


def _choose_ip(session, selection: str, requested_id: int | None) -> ServerIp | None:
    cfg = _settings_row(session)
    mode = selection
    ip_id = requested_id
    if mode == "automatic":
        mode = cfg.allocation_policy
        ip_id = cfg.default_server_ip_id
    if mode == "primary":
        if not settings.server_public_ip:
            return None
        address, _family = _canonical_address(settings.server_public_ip)
        return session.scalar(select(ServerIp).where(ServerIp.address == address, ServerIp.active == True))  # noqa: E712
    if mode in {"random", "random_shared"}:
        present = _present_addresses()
        candidates = [row for row in session.scalars(select(ServerIp).where(ServerIp.active == True, ServerIp.allocation_mode == "shared")).all() if row.address in present]  # noqa: E712
        if not candidates:
            raise ValidationError("No active shared IP is available")
        return secrets.choice(candidates)
    if mode == "specific":
        if ip_id is None:
            raise ValidationError("Choose a server IP")
        row = session.get(ServerIp, int(ip_id))
        if row is None or not row.active:
            raise ValidationError("The selected server IP is unavailable")
        if row.address not in _present_addresses():
            raise ValidationError("The selected server IP is missing from the host")
        return row
    raise ValidationError("selection must be automatic, primary, random, or specific")


def _assignment_dict(row: ServerIp | None, username: str) -> dict:
    return {"username": username, "server_ip_id": row.id if row else None, "address": row.address if row else settings.server_public_ip or None, "allocation_mode": row.allocation_mode if row else "primary"}


def _sync_account_dns(account_id: int, old_address: str | None, new_address: str | None) -> tuple[int, list[str]]:
    """Move Boron-managed website records to the newly assigned address.

    The operation is intentionally best-effort after the allocation commit:
    a temporarily unavailable DNS provider must not make the allocation lie.
    Warnings are returned to the UI with the affected name.
    """
    if not new_address or new_address == old_address:
        return 0, []
    from daemon import dnsprovider
    from daemon.dns_zone_lookup import label_within_zone

    with write_session() as session:
        zones = session.scalars(select(DnsZone.zone).where(DnsZone.account_id == account_id)).all()
        domains = session.scalars(select(Domain.domain).where(Domain.account_id == account_id)).all()
    zone_set = set(zones)
    new_type = "AAAA" if ":" in new_address else "A"
    old_type = ("AAAA" if ":" in old_address else "A") if old_address else None
    updated = 0
    warnings = []
    for domain in domains:
        parents = [zone for zone in zone_set if domain == zone or domain.endswith("." + zone)]
        if not parents:
            continue
        zone = max(parents, key=len)
        label = label_within_zone(domain, zone)
        try:
            dnsprovider.upsert_record(zone, label, new_type, [new_address])
            if old_type and old_type != new_type:
                try:
                    dnsprovider.delete_record(zone, label, old_type)
                except dnsprovider.DnsError:
                    pass
            updated += 1
        except dnsprovider.DnsError as exc:
            warnings.append(f"{domain}: {exc}")
    return updated, warnings


def assign_account(params: dict) -> dict:
    username = validate_username(params["username"])
    selection = str(params.get("selection", "specific"))
    raw_id = params.get("server_ip_id")
    requested_id = int(raw_id) if raw_id not in (None, "") else None
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None or account.status == "terminated":
            raise ValidationError(f"Account '{username}' was not found")
        old = session.scalar(select(ServerIpAssignment).where(ServerIpAssignment.account_id == account.id))
        old_row = session.get(ServerIp, old.server_ip_id) if old is not None else None
        old_address = old_row.address if old_row else settings.server_public_ip or None
        if selection == "unassigned":
            if old is not None:
                session.delete(old)
            result = _assignment_dict(None, username)
            new_address = result["address"]
            account_id = account.id
        else:
            chosen = _choose_ip(session, selection, requested_id)
            if chosen is None:
                if old is not None:
                    session.delete(old)
                result = _assignment_dict(None, username)
                new_address = result["address"]
                account_id = account.id
            else:
                if chosen.allocation_mode == "dedicated":
                    occupied = session.scalar(select(ServerIpAssignment).where(ServerIpAssignment.server_ip_id == chosen.id, ServerIpAssignment.account_id != account.id))
                    if occupied is not None:
                        raise ValidationError(f"Dedicated IP {chosen.address} is already assigned")
                if old is None:
                    session.add(ServerIpAssignment(server_ip_id=chosen.id, account_id=account.id))
                else:
                    old.server_ip_id = chosen.id
                session.flush()
                result = _assignment_dict(chosen, username)
                new_address = chosen.address
                account_id = account.id
    dns_updates, warnings = _sync_account_dns(account_id, old_address, new_address)
    result["dns_updates"] = dns_updates
    result["warnings"] = warnings
    return result


def assign_for_new_account(params: dict) -> dict:
    return assign_account({
        "username": params["username"],
        "selection": params.get("selection", "automatic"),
        "server_ip_id": params.get("server_ip_id"),
    })


def address_for_account(account_id: int) -> str | None:
    with write_session() as session:
        address = session.scalar(
            select(ServerIp.address).join(ServerIpAssignment, ServerIpAssignment.server_ip_id == ServerIp.id)
            .where(ServerIpAssignment.account_id == account_id, ServerIp.active == True)  # noqa: E712
        )
    return address or settings.server_public_ip or None


def release_account(account: Account) -> None:
    with write_session() as session:
        session.execute(delete(ServerIpAssignment).where(ServerIpAssignment.account_id == account.id))
