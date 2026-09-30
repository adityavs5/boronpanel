"""Inherited resource policies, cgroup application and bounded history."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from sqlalchemy import delete, or_, select, text

from daemon import cgroups
from shared.config import settings
from shared.db import write_session
from shared.models import (
    Account, Plan, ResellerAccount, ResellerProfile, ResourceFault,
    ResourcePolicy, ResourceSample,
)
from shared.validation import ValidationError, validate_username

POLICY_FIELDS = (
    "cpu_cores", "cpu_weight", "memory_high_mb", "memory_max_mb",
    "io_read_bps", "io_write_bps", "io_read_iops", "io_write_iops",
    "nproc", "entry_processes",
)

DEFAULT_READ_IOPS = 500
DEFAULT_WRITE_IOPS = 250


def available_cpu_cores() -> int:
    """Return CPU capacity visible to borond, including affinity limits.

    sched_getaffinity reflects VM hotplug and service/cgroup CPU affinity at
    the time the policy is validated.  os.cpu_count is the portable fallback.
    """
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def host_memory_gb() -> float:
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024 ** 3
    except (ValueError, OSError, AttributeError):
        return 0


def _policy_dict(row: ResourcePolicy) -> dict:
    return {
        "id": row.id, "scope_type": row.scope_type, "scope_id": row.scope_id,
        "version": row.version, **{field: getattr(row, field) for field in POLICY_FIELDS},
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "last_apply_status": row.last_apply_status, "last_apply_error": row.last_apply_error,
        "last_applied_at": row.last_applied_at.isoformat() if row.last_applied_at else None,
    }


def _legacy_policy(account: Account) -> dict:
    return {
        "cpu_cores": account.cpu_pct / 100, "cpu_weight": 100,
        "memory_high_mb": max(64, int(account.mem_mb * .9)), "memory_max_mb": account.mem_mb,
        "io_read_bps": account.io_mb * 1024 * 1024,
        "io_write_bps": account.io_mb * 1024 * 1024,
        "io_read_iops": None, "io_write_iops": None,
        "nproc": account.pids_max, "entry_processes": 20,
    }


def _active(row: ResourcePolicy) -> bool:
    if row.expires_at is None:
        return True
    expiry = row.expires_at.replace(tzinfo=dt.timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
    return expiry > _now()


def effective_for_account(db, account: Account) -> dict:
    effective = _legacy_policy(account)
    sources = {field: "legacy account limit" for field in POLICY_FIELDS}
    candidates: list[ResourcePolicy] = []
    server = db.scalar(select(ResourcePolicy).where(
        ResourcePolicy.scope_type == "server", ResourcePolicy.scope_id == 0,
    ))
    if server and _active(server):
        candidates.append(server)
    reseller_plan_id = db.scalar(
        select(ResellerProfile.plan_id).join(ResellerAccount, ResellerAccount.reseller_id == ResellerProfile.id)
        .where(ResellerAccount.account_id == account.id)
    )
    if reseller_plan_id is not None:
        row = db.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "reseller_plan", ResourcePolicy.scope_id == reseller_plan_id,
        ))
        if row and _active(row):
            candidates.append(row)
    if account.plan_id is not None:
        row = db.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "plan", ResourcePolicy.scope_id == account.plan_id,
        ))
        if row and _active(row):
            candidates.append(row)
    override = db.scalar(select(ResourcePolicy).where(
        ResourcePolicy.scope_type == "account", ResourcePolicy.scope_id == account.id,
    ))
    if override and _active(override):
        candidates.append(override)
    for row in candidates:
        label = f"{row.scope_type}:{row.scope_id}"
        # A concrete policy row is a complete contract. NULL explicitly means
        # Unlimited, so every field receives the row's value.
        for field in POLICY_FIELDS:
            effective[field] = getattr(row, field)
            sources[field] = label
    requested_cpu = effective['cpu_cores']
    capacity = available_cpu_cores()
    if requested_cpu is not None and requested_cpu > capacity:
        effective['cpu_cores'] = capacity
    return {"values": effective, "sources": sources, "policy": _policy_dict(candidates[-1]) if candidates else None,
            "configured_cpu_cores": requested_cpu,
            "capacity_adjusted": requested_cpu is not None and requested_cpu > capacity}


def _number(value, field: str, minimum: float, maximum: float, *, integer: bool = True):
    if value is None or value == "":
        return None
    try:
        result = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number or Unlimited") from None
    if not minimum <= result <= maximum:
        raise ValidationError(f"{field} must be between {minimum / 1024:g} and {maximum / 1024:g} GB" if field.startswith("Memory") else f"{field} must be between {minimum:g} and {maximum:g}")
    return result


def validate_policy(params: dict) -> dict:
    cpu_capacity = available_cpu_cores()
    policy = {
        "cpu_cores": _number(params.get("cpu_cores"), "CPU cores", .25, cpu_capacity, integer=False),
        "cpu_weight": _number(params.get("cpu_weight", 100), "CPU weight", 1, 10000),
        "memory_high_mb": _number(params.get("memory_high_mb"), "Memory high", 64, 1048576),
        "memory_max_mb": _number(params.get("memory_max_mb"), "Memory maximum", 64, 1048576),
        "io_read_bps": _number(params.get("io_read_bps"), "Read bandwidth", 1024, 10**13),
        "io_write_bps": _number(params.get("io_write_bps"), "Write bandwidth", 1024, 10**13),
        "io_read_iops": _number(params.get("io_read_iops"), "Read IOPS", 1, 10**9),
        "io_write_iops": _number(params.get("io_write_iops"), "Write IOPS", 1, 10**9),
        "nproc": _number(params.get("nproc"), "NPROC", 10, 1000000),
        "entry_processes": _number(params.get("entry_processes"), "Entry processes", 1, 10000),
    }
    if policy["memory_high_mb"] and policy["memory_max_mb"] and policy["memory_high_mb"] > policy["memory_max_mb"]:
        raise ValidationError("Soft memory limit must not exceed the hard memory limit")
    return policy


def save_policy(params: dict) -> dict:
    scope_type = str(params.get("scope_type", ""))
    if scope_type not in {"server", "plan", "reseller_plan", "account"}:
        raise ValidationError("Invalid resource policy scope")
    scope_id = int(params.get("scope_id", 0))
    if scope_id < 0 or (scope_type != "server" and scope_id == 0):
        raise ValidationError("Invalid resource policy scope identifier")
    values = validate_policy(params)
    expires_at = None
    if params.get("expires_at"):
        try:
            expires_at = dt.datetime.fromisoformat(str(params["expires_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError("Invalid override expiry") from None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
        if expires_at <= _now():
            raise ValidationError("Override expiry must be in the future")
    with write_session() as db:
        row = db.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == scope_type, ResourcePolicy.scope_id == scope_id,
        ))
        if row is None:
            row = ResourcePolicy(scope_type=scope_type, scope_id=scope_id)
            db.add(row)
        else:
            row.version += 1
        for field, value in values.items():
            setattr(row, field, value)
        row.expires_at = expires_at
        db.flush()
        result = _policy_dict(row)
    _reconcile_scope(scope_type, scope_id)
    return result


def save_account_policy(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as db:
        account = db.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError("Account not found")
        account_id = account.id
    return save_policy({**params, "scope_type": "account", "scope_id": account_id})


def _account_ids_for_scope(db, scope_type: str, scope_id: int) -> list[int]:
    if scope_type == "server":
        return list(db.scalars(select(Account.id).where(Account.status.in_(["active", "suspended"]))).all())
    if scope_type == "account":
        return [scope_id]
    if scope_type == "plan":
        return list(db.scalars(select(Account.id).where(Account.plan_id == scope_id)).all())
    return list(db.scalars(
        select(ResellerAccount.account_id).join(ResellerProfile, ResellerProfile.id == ResellerAccount.reseller_id)
        .where(ResellerProfile.plan_id == scope_id)
    ).all())


def _reconcile_scope(scope_type: str, scope_id: int) -> None:
    with write_session() as db:
        ids = _account_ids_for_scope(db, scope_type, scope_id)
    for account_id in ids:
        apply_account(account_id)


def apply_account(account_id: int) -> dict:
    with write_session() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise ValidationError("Account not found")
        effective = effective_for_account(db, account)
        username = account.username
        policy_id = effective["policy"]["id"] if effective["policy"] else None
    try:
        cgroups.apply_policy(username, effective["values"])
    except Exception as exc:
        if policy_id:
            with write_session() as db:
                row = db.get(ResourcePolicy, policy_id)
                if row:
                    row.last_apply_status, row.last_apply_error = "failed", str(exc)[:1000]
        raise
    if policy_id:
        with write_session() as db:
            row = db.get(ResourcePolicy, policy_id)
            if row:
                row.last_apply_status, row.last_apply_error, row.last_applied_at = "applied", None, _now()
    return {"username": username, **effective}


def reset_account(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as db:
        account = db.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError("Account not found")
        row = db.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "account", ResourcePolicy.scope_id == account.id,
        ))
        if row:
            db.delete(row)
        account_id = account.id
    return apply_account(account_id)


def overview(_params: dict | None = None) -> dict:
    with write_session() as db:
        accounts = db.scalars(select(Account).where(Account.status.in_(["active", "suspended"])).order_by(Account.username)).all()
        rows = []
        for account in accounts:
            effective = effective_for_account(db, account)
            latest = db.scalar(select(ResourceSample).where(ResourceSample.account_id == account.id)
                               .order_by(ResourceSample.sampled_at.desc()))
            faults = db.scalars(select(ResourceFault).where(ResourceFault.account_id == account.id)
                                .order_by(ResourceFault.detected_at.desc()).limit(5)).all()
            rows.append({
                "account_id": account.id, "username": account.username, "status": account.status,
                "plan_id": account.plan_id, **effective,
                "usage": None if latest is None else {
                    "cpu_pct": latest.cpu_pct, "memory_bytes": latest.memory_bytes,
                    "io_read_bytes": latest.io_read_bytes, "io_write_bytes": latest.io_write_bytes,
                    "pids": latest.pids, "sampled_at": latest.sampled_at.isoformat(),
                },
                "recent_faults": [{"resource": f.resource, "count": f.count, "detail": f.detail,
                                   "detected_at": f.detected_at.isoformat()} for f in faults],
            })
        policies = db.scalars(select(ResourcePolicy).order_by(ResourcePolicy.scope_type, ResourcePolicy.scope_id)).all()
        history = db.scalars(select(ResourceSample).order_by(ResourceSample.sampled_at.desc()).limit(500)).all()
        fault_rows = db.scalars(select(ResourceFault).order_by(ResourceFault.detected_at.desc()).limit(500)).all()
        return {
            "host": {"cpu_cores": available_cpu_cores(), "memory_gb": host_memory_gb()},
            "users": rows, "policies": [_policy_dict(row) for row in policies],
            "history": [{"account_id": row.account_id, "cpu_pct": row.cpu_pct,
                         "memory_bytes": row.memory_bytes, "io_read_bytes": row.io_read_bytes,
                         "io_write_bytes": row.io_write_bytes, "pids": row.pids,
                         "sampled_at": row.sampled_at.isoformat()} for row in history],
            "faults": [{"account_id": row.account_id, "resource": row.resource, "count": row.count,
                        "detail": row.detail, "detected_at": row.detected_at.isoformat()} for row in fault_rows],
        }


_previous: dict[int, tuple[dt.datetime, dict[str, int], dict[str, int]]] = {}
_last_compaction: dt.datetime | None = None


def reconcile_expired_policies() -> int:
    """Reapply inherited limits once a temporary policy expires.

    Expired rows remain as auditable history, but are marked once so the
    minute sampler does not reapply the same accounts forever.
    """
    now = _now()
    with write_session() as db:
        expired = db.scalars(select(ResourcePolicy).where(
            ResourcePolicy.expires_at.isnot(None),
            ResourcePolicy.expires_at <= now.replace(tzinfo=None),
            or_(ResourcePolicy.last_apply_status.is_(None), ResourcePolicy.last_apply_status != "expired"),
        )).all()
        work = [
            (row.id, _account_ids_for_scope(db, row.scope_type, row.scope_id))
            for row in expired
        ]

    reconciled: set[int] = set()
    for policy_id, account_ids in work:
        errors: list[str] = []
        for account_id in sorted(set(account_ids)):
            try:
                apply_account(account_id)
                reconciled.add(account_id)
            except Exception as exc:
                # Keep the expiry in a retryable state.  Marking it "expired"
                # before cgroup application succeeded left a widened temporary
                # policy in force indefinitely after one transient failure.
                errors.append(f"account {account_id}: {exc}"[:1000])
        with write_session() as db:
            row = db.get(ResourcePolicy, policy_id)
            if row is None:
                continue
            if errors:
                row.last_apply_status = "expire_failed"
                row.last_apply_error = "; ".join(errors)[:1000]
            else:
                row.last_apply_status = "expired"
                row.last_apply_error = None
                row.last_applied_at = _now()
    return len(reconciled)


def compact_samples(now: dt.datetime | None = None) -> int:
    """Collapse samples older than 24 hours into one averaged row per hour."""
    global _last_compaction
    now = now or _now()
    if _last_compaction and now - _last_compaction < dt.timedelta(hours=1):
        return 0
    _last_compaction = now
    start = now - dt.timedelta(days=max(1, settings.resource_sample_retention_days))
    end = now - dt.timedelta(hours=24)
    changed = 0
    with write_session() as db:
        buckets = db.execute(text("""
            SELECT account_id, strftime('%Y-%m-%d %H:00:00', sampled_at) AS bucket,
                   MIN(id) AS keep_id, AVG(cpu_pct), AVG(memory_bytes),
                   AVG(io_read_bytes), AVG(io_write_bytes), AVG(pids), COUNT(*)
              FROM resource_samples
             WHERE sampled_at >= :start AND sampled_at < :end
             GROUP BY account_id, bucket
            HAVING COUNT(*) > 1
        """), {"start": start.replace(tzinfo=None), "end": end.replace(tzinfo=None)}).all()
        for account_id, bucket, keep_id, cpu, memory, read_bytes, write_bytes, pids, count in buckets:
            keeper = db.get(ResourceSample, keep_id)
            if keeper is None:
                continue
            keeper.period_seconds = 3600
            keeper.cpu_pct = float(cpu or 0)
            keeper.memory_bytes = int(memory or 0)
            keeper.io_read_bytes = int(read_bytes or 0)
            keeper.io_write_bytes = int(write_bytes or 0)
            keeper.pids = int(pids or 0)
            db.execute(text("""
                DELETE FROM resource_samples
                 WHERE account_id=:account_id AND id != :keep_id
                   AND sampled_at >= :start AND sampled_at < :end
                   AND strftime('%Y-%m-%d %H:00:00', sampled_at)=:bucket
            """), {
                "account_id": account_id, "keep_id": keep_id, "bucket": bucket,
                "start": start.replace(tzinfo=None), "end": end.replace(tzinfo=None),
            })
            changed += int(count) - 1
    return changed


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _read_key_values(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and ":" not in parts[0] and "=" not in parts[1]:
                try:
                    result[parts[0]] = result.get(parts[0], 0) + int(parts[1])
                except ValueError:
                    pass
                continue
            for item in parts[1:] if len(parts) > 1 and ":" in parts[0] else parts:
                if "=" in item:
                    key, value = item.split("=", 1)
                else:
                    continue
                try:
                    result[key] = result.get(key, 0) + int(value)
                except ValueError:
                    continue
    except OSError:
        pass
    return result


def sample_all() -> int:
    now = _now()
    reconcile_expired_policies()
    with write_session() as db:
        accounts = db.scalars(select(Account).where(Account.status.in_(["active", "suspended"]))).all()
        snapshots = [(row.id, row.username) for row in accounts]
    created = 0
    for account_id, username in snapshots:
        root = cgroups._cgroup_path(username)
        cpu = _read_key_values(root / "cpu.stat")
        io = _read_key_values(root / "io.stat")
        events = {**_read_key_values(root / "memory.events"), **{
            f"pids_{key}": value for key, value in _read_key_values(root / "pids.events").items()
        }}
        previous = _previous.get(account_id)
        elapsed = (now - previous[0]).total_seconds() if previous else 60
        cpu_delta = max(0, cpu.get("usage_usec", 0) - (previous[1].get("usage_usec", 0) if previous else 0))
        with write_session() as db:
            db.add(ResourceSample(
                account_id=account_id, period_seconds=max(1, int(elapsed)),
                cpu_pct=(cpu_delta / (elapsed * 1_000_000)) * 100 if previous and elapsed else 0,
                memory_bytes=_read_int(root / "memory.current"),
                io_read_bytes=io.get("rbytes", 0), io_write_bytes=io.get("wbytes", 0),
                pids=_read_int(root / "pids.current"), sampled_at=now,
            ))
            if previous:
                for key in ("oom", "oom_kill", "high", "max", "pids_max"):
                    delta = events.get(key, 0) - previous[2].get(key, 0)
                    if delta > 0:
                        db.add(ResourceFault(account_id=account_id, resource=key, count=delta,
                                             detail="Kernel cgroup limit event", detected_at=now))
        _previous[account_id] = (now, cpu, events)
        created += 1
    cutoff = now - dt.timedelta(days=max(1, settings.resource_sample_retention_days))
    with write_session() as db:
        db.execute(delete(ResourceSample).where(ResourceSample.sampled_at < cutoff))
        db.execute(delete(ResourceFault).where(ResourceFault.detected_at < cutoff))
    compact_samples(now)
    return created
