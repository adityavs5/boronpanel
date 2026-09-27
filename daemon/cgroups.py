"""Aggregate per-account resource enforcement through native user slices.

OpenLiteSpeed's cgroup-v2 integration starts account CGI/PHP workloads below
``user-<uid>.slice``. PAM/logind place SSH, SFTP and panel-terminal sessions
below the same slice. Boron assigns account application services to that
slice too, so one CPU, memory, I/O and task budget covers the whole account.

Older Boron releases created ``boron-<username>.slice`` and attempted to move
already-running PHP workers directly into it. That destination becomes an
internal cgroup as soon as an app service exists below it, so cgroup v2
correctly rejects the move with ``EBUSY``. This module never writes PIDs to an
internal cgroup. Native OLS placement happens before customer code runs; the
periodic audit only detects uncovered workers and retires empty legacy slices.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from pwd import getpwnam as _getpwnam
import shutil

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account
from shared.validation import validate_username

from daemon.procutil import run

logger = logging.getLogger("borond.cgroups")

CGROUP_ROOT = Path("/sys/fs/cgroup")
USER_SLICE_ROOT = CGROUP_ROOT / "user.slice"
LSHTTPD_CGROUP_PROCS = CGROUP_ROOT / "system.slice" / "lshttpd.service" / "cgroup.procs"
MIN_ACCOUNT_UID = 1000

DEFAULT_CPU_PCT = 25
DEFAULT_MEM_MB = 512
DEFAULT_IO_MB = 50
DEFAULT_PIDS_MAX = 50


class CgroupError(Exception):
    pass


def user_slice_name(uid: int) -> str:
    uid = int(uid)
    if uid < MIN_ACCOUNT_UID:
        raise CgroupError(f"refusing account resource slice for system uid {uid}")
    return f"user-{uid}.slice"


def legacy_slice_name(username: str) -> str:
    return f"boron-{validate_username(username)}.slice"


def _resolve_uid(username: str, uid: int | None = None) -> int:
    """Resolve and cross-check the immutable kernel identity for an account."""
    username = validate_username(username)
    supplied = int(uid) if uid is not None else None
    with write_session() as session:
        db_uid = session.scalar(select(Account.uid).where(Account.username == username))
    try:
        passwd_uid = _getpwnam(username).pw_uid
    except KeyError:
        passwd_uid = None
    candidates = [value for value in (supplied, db_uid, passwd_uid) if value is not None]
    if not candidates:
        raise CgroupError(f"account '{username}' has no resolvable uid")
    if len(set(candidates)) != 1:
        raise CgroupError(f"account '{username}' uid does not match the Linux identity")
    resolved = candidates[0]
    user_slice_name(resolved)
    return resolved


def account_slice_name(username: str, uid: int | None = None) -> str:
    return user_slice_name(_resolve_uid(username, uid))


def _cgroup_path(username: str, uid: int | None = None) -> Path:
    resolved = _resolve_uid(username, uid)
    return USER_SLICE_ROOT / user_slice_name(resolved)


def _legacy_unit_path(username: str) -> Path:
    return Path("/etc/systemd/system") / legacy_slice_name(username)


def ensure_slice(username: str, uid: int | None = None) -> str:
    """Ensure the canonical per-UID parent exists before admitting work."""
    unit = account_slice_name(username, uid)
    result = run(["systemctl", "start", unit], timeout=20)
    if not result.ok:
        raise CgroupError(f"could not start {unit}: {result.stderr.strip()}")
    return unit


def apply_limits(
    username: str,
    cpu_pct: int,
    mem_mb: int,
    io_mb: int,
    pids_max: int,
    *,
    uid: int | None = None,
) -> None:
    apply_policy(username, {
        "cpu_cores": cpu_pct / 100,
        "memory_high_mb": None,
        "memory_max_mb": mem_mb,
        "io_read_bps": io_mb * 1024 * 1024,
        "io_write_bps": io_mb * 1024 * 1024,
        "io_read_iops": None,
        "io_write_iops": None,
        "nproc": pids_max,
    }, uid=uid)


def apply_policy(username: str, policy: dict, *, uid: int | None = None) -> None:
    """Apply one aggregate policy to PHP, apps and login-session descendants."""
    unit = ensure_slice(username, uid)
    device = settings.cgroup_io_device

    def bandwidth(value):
        if value is None:
            return "infinity"
        value = int(value)
        return f"{value // 1048576}M" if value % 1048576 == 0 else str(value)

    cpu = policy.get("cpu_cores")
    high = policy.get("memory_high_mb")
    maximum = policy.get("memory_max_mb")
    nproc = policy.get("nproc")
    properties = [
        f"CPUQuota={'infinity' if cpu is None else f'{float(cpu) * 100:g}%'}",
        f"MemoryHigh={'infinity' if high is None else f'{int(high)}M'}",
        f"MemoryMax={'infinity' if maximum is None else f'{int(maximum)}M'}",
        "MemorySwapMax=0",
        f"TasksMax={'infinity' if nproc is None else int(nproc)}",
        f"IOReadBandwidthMax={device} {bandwidth(policy.get('io_read_bps'))}",
        f"IOWriteBandwidthMax={device} {bandwidth(policy.get('io_write_bps'))}",
        f"IOReadIOPSMax={device} {'infinity' if policy.get('io_read_iops') is None else int(policy['io_read_iops'])}",
        f"IOWriteIOPSMax={device} {'infinity' if policy.get('io_write_iops') is None else int(policy['io_write_iops'])}",
        "CPUAccounting=yes",
        "MemoryAccounting=yes",
        "IOAccounting=yes",
        "TasksAccounting=yes",
    ]
    if policy.get("cpu_weight") is not None:
        properties.append(f"CPUWeight={int(policy['cpu_weight'])}")
    result = run(["systemctl", "set-property", unit, *properties], timeout=20)
    if not result.ok:
        raise CgroupError(f"systemctl set-property failed for '{username}': {result.stderr.strip()}")


def _retire_legacy_slice(username: str) -> bool:
    """Remove an obsolete Boron slice only after it has no descendants."""
    legacy = legacy_slice_name(username)
    old_root = CGROUP_ROOT / "boron.slice" / legacy
    if old_root.exists():
        try:
            populated = (old_root / "cgroup.events").read_text()
        except OSError:
            return False
        if "populated 1" in populated:
            return False
    run(["systemctl", "stop", legacy], timeout=20)
    _legacy_unit_path(username).unlink(missing_ok=True)
    shutil.rmtree(Path("/etc/systemd/system.control") / f"{legacy}.d", ignore_errors=True)
    run(["systemctl", "daemon-reload"], timeout=20)
    return True


def remove_slice(username: str, uid: int | None = None) -> None:
    """Reset a terminated account's policy and remove obsolete Boron state."""
    resolved = None
    try:
        resolved = _resolve_uid(username, uid)
    except CgroupError:
        if uid is not None:
            raise
    if resolved is not None:
        unit = user_slice_name(resolved)
        run(["systemctl", "stop", unit], timeout=20)
        run(["systemctl", "revert", unit], timeout=20)
        shutil.rmtree(Path("/etc/systemd/system.control") / f"{unit}.d", ignore_errors=True)
    _retire_legacy_slice(username)


def bootstrap_all_slices() -> None:
    """Reapply every account policy before OLS/apps/customer sessions start."""
    from daemon.resource_manager import effective_for_account

    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.status.in_(["active", "suspended"]))).all()
        snapshots = [(account, effective_for_account(session, account)) for account in accounts]
    for account, effective in snapshots:
        try:
            if effective["policy"] is None:
                apply_limits(
                    account.username, account.cpu_pct, account.mem_mb, account.io_mb,
                    account.pids_max, uid=account.uid,
                )
            else:
                apply_policy(account.username, effective["values"], uid=account.uid)
        except Exception:
            logger.exception("failed to bootstrap account resource slice for '%s'", account.username)


def _account_uid_map(session) -> dict[int, str]:
    rows = session.execute(select(Account.uid, Account.username).where(
        Account.uid.isnot(None), Account.status.in_(["active", "suspended"]),
    )).all()
    return {uid: username for uid, username in rows}


def _pid_cgroup(pid: int) -> str | None:
    try:
        for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                return line[3:]
    except OSError:
        pass
    return None


def _pid_uid(pid: int) -> int | None:
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return None


def audit_php_coverage() -> dict:
    """Detect OLS workers that escaped native startup placement.

    This is observation-only. Moving a live process after it has allocated
    memory cannot prove complete accounting, and placing it directly in the
    per-user parent would violate cgroup-v2's internal-process rule.
    """
    try:
        pids = [int(value) for value in LSHTTPD_CGROUP_PROCS.read_text().split()]
    except OSError:
        return {"checked": 0, "uncovered": 0, "accounts": {}}
    with write_session() as session:
        uid_map = _account_uid_map(session)
    uncovered: dict[str, int] = {}
    checked = 0
    for pid in pids:
        uid = _pid_uid(pid)
        if uid is None:
            continue
        username = uid_map.get(uid)
        if username is None:
            continue
        checked += 1
        path = _pid_cgroup(pid)
        expected = f"/user.slice/{user_slice_name(uid)}"
        if path is None or not (path == expected or path.startswith(expected + "/")):
            uncovered[username] = uncovered.get(username, 0) + 1
    for username in uid_map.values():
        try:
            _retire_legacy_slice(username)
        except Exception:
            logger.exception("failed to retire legacy resource slice for '%s'", username)
    return {"checked": checked, "uncovered": sum(uncovered.values()), "accounts": uncovered}


def reconcile_processes() -> int:
    """Compatibility wrapper returning uncovered PHP workers, never moving them."""
    return audit_php_coverage()["uncovered"]
