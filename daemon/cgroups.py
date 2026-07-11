"""Per-account resource limits via cgroups v2 (Phase 2 feature 6).

Architecture decision (EFFORT: xhigh per the goal): OLS spawns each
account's LSAPI/PHP worker process itself, already setuid/setgid-dropped
to that account's own uid/gid *before* it execs the configured binary --
confirmed empirically by pointing a test account's extprocessor `path` at
a diagnostic wrapper script and inspecting its real uid/cgroup at runtime
(see CHECKPOINT-phase2-6.md). That means there is no privilege-elevated
moment inside the spawned process itself to join a cgroup outside
lshttpd's own hierarchy -- cgroup v2 requires write access to the nearest
*common ancestor* of the process's current and target cgroups to migrate
it there, not just the destination, and an already-unprivileged worker
has no such access (confirmed empirically: a plain unprivileged
self-migration attempt failed with EACCES).

The alternative considered was a small setuid-root (or
cap_sys_admin-capable) helper binary as the extprocessor's `path`, which
would let the kernel re-elevate privilege at exec time regardless of the
calling process's dropped uid. Rejected: that installs a permanent local
privilege-escalation binary in every account's PHP execution path on a
box that will host mutually-untrusted customer accounts -- real, ongoing
attack surface for the sake of closing what is at most a few-second
placement window, and it was flagged by this environment's own safety
review before it was ever installed. This build never installs one.

Instead: borond (which already runs as root, the same privilege level
ARCHITECTURE.md establishes for the whole daemon) periodically scans for
worker processes still sitting in lshttpd's own cgroup and moves each one
into its owning account's slice -- root crossing an arbitrary cgroup
boundary is always permitted, no capability/setuid surface needed. This
trades a small window (one reconcile interval, default a few seconds)
during which a freshly (re)spawned LSAPI worker is briefly unthrottled,
for zero new privilege-escalation surface. Given LSAPI backends are
long-lived/pooled (persistConn, not spawned per request -- Phase 1's
ARCHITECTURE.md SS6), this window is rare (only at initial spawn or after
a crash/restart) and short, a reasonable trade for a hosting panel where
the alternative is a standing root-equivalent local exploit primitive.

Limits themselves are systemd slices (`Slice=` unit per account, nested
under a static `boron.slice`), not hand-rolled cgroupfs writes --
`systemctl set-property` applies limits to the live cgroup immediately
*and* persists them via a systemd-generated drop-in under
/etc/systemd/system.control/ (confirmed empirically: survives daemon-reload,
which is what a reboot re-reads), satisfying "must survive reboot" without
this project needing to hand-write/parse raw unit files for updates.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account

from daemon.procutil import run

logger = logging.getLogger("borond.cgroups")

PARENT_SLICE = "boron.slice"
CGROUP_ROOT = Path("/sys/fs/cgroup")
LSHTTPD_CGROUP_PROCS = CGROUP_ROOT / "system.slice" / "lshttpd.service" / "cgroup.procs"
MIN_ACCOUNT_UID = 1000  # matches useradd's default UID_MIN (Phase 1 convention)

DEFAULT_CPU_PCT = 25
DEFAULT_MEM_MB = 512
DEFAULT_IO_MB = 50
DEFAULT_PIDS_MAX = 50


class CgroupError(Exception):
    pass


def slice_name(username: str) -> str:
    return f"boron-{username}.slice"


def _unit_path(username: str) -> Path:
    return Path("/etc/systemd/system") / slice_name(username)


def _cgroup_path(username: str) -> Path:
    return CGROUP_ROOT / PARENT_SLICE / slice_name(username)


def _write_unit_file(username: str) -> None:
    """A minimal, static base unit -- just enough to exist. No explicit
    `Slice=boron.slice` directive: systemd nests any "foo-bar.slice"
    unit under "foo.slice" automatically from the name alone (confirmed
    empirically -- CGroup path is /boron.slice/boron-<user>.slice
    with no Slice= line at all). Asserting it explicitly turned out to be
    not just redundant but noisy: re-running daemon-reload + start against
    an *already-active* slice with an explicit Slice= line logged "Failed
    to assign slice boron.slice to unit ..., ignoring: Invalid
    argument" on every subsequent apply_limits() call (harmless --
    "ignoring" means it kept the correct assignment -- but needless
    warning noise in the journal on every limit update). Actual limits are
    applied separately via `systemctl set-property` (see apply_limits),
    not templated into this file, so updating limits never requires
    rewriting/reloading the base unit."""
    unit_path = _unit_path(username)
    content = (
        "[Unit]\n"
        f"Description=Boron resource limits for account '{username}'\n"
    )
    if unit_path.exists() and unit_path.read_text() == content:
        return
    unit_path.write_text(content)


def ensure_slice(username: str) -> None:
    """Idempotent: creates the unit file (if missing/changed) and starts
    the slice so its cgroup exists on disk -- safe to call even if it's
    already running (`systemctl start` on an already-active unit is a
    no-op)."""
    _write_unit_file(username)
    run(["systemctl", "daemon-reload"], timeout=20, check=True)
    run(["systemctl", "start", slice_name(username)], timeout=20, check=True)


def apply_limits(username: str, cpu_pct: int, mem_mb: int, io_mb: int, pids_max: int) -> None:
    """Applies to the live cgroup immediately and persists via systemd's
    own drop-in mechanism (reboot-safe -- confirmed empirically, see
    module docstring). Called both at initial provisioning and on every
    subsequent limit update; there is no separate "update" code path."""
    ensure_slice(username)
    device = settings.cgroup_io_device
    result = run(
        [
            "systemctl", "set-property", slice_name(username),
            f"CPUQuota={cpu_pct}%",
            f"MemoryMax={mem_mb}M",
            "MemorySwapMax=0",
            f"TasksMax={pids_max}",
            f"IOReadBandwidthMax={device} {io_mb}M",
            f"IOWriteBandwidthMax={device} {io_mb}M",
        ],
        timeout=20,
    )
    if not result.ok:
        raise CgroupError(f"systemctl set-property failed for '{username}': {result.stderr.strip()}")


def remove_slice(username: str) -> None:
    """TERMINATE_HOOKS entry: idempotent -- safe even if the account never
    had a slice (e.g. termination failed partway through creation)."""
    run(["systemctl", "stop", slice_name(username)], timeout=20)
    unit_path = _unit_path(username)
    if unit_path.exists():
        unit_path.unlink()
    import shutil

    control_dropin = Path("/etc/systemd/system.control") / f"{slice_name(username)}.d"
    shutil.rmtree(control_dropin, ignore_errors=True)
    run(["systemctl", "daemon-reload"], timeout=20)


def bootstrap_all_slices() -> None:
    """Run once at borond startup: ensures every active/suspended
    account's slice exists with its current DB-recorded limits applied.
    This is what makes limits survive a *host* reboot, not just a
    borond restart -- systemd itself doesn't auto-recreate a slice's
    cgroup on boot just because a unit file is present (slices without an
    [Install] section aren't "enabled"/auto-started the way services are),
    so borond's own startup is the single source of truth that
    reconciles cgroups back to DB state, the same role bootstrap_baseline
    plays for OLS vhosts."""
    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.status.in_(["active", "suspended"]))).all()
        snapshots = [(a.username, a.cpu_pct, a.mem_mb, a.io_mb, a.pids_max) for a in accounts]

    for username, cpu_pct, mem_mb, io_mb, pids_max in snapshots:
        try:
            apply_limits(username, cpu_pct, mem_mb, io_mb, pids_max)
        except Exception:
            logger.exception("failed to bootstrap cgroup slice for '%s'", username)


def _account_uid_map(session) -> dict[int, str]:
    rows = session.execute(select(Account.uid, Account.username).where(Account.uid.isnot(None))).all()
    return {uid: username for uid, username in rows}


def reconcile_processes() -> int:
    """Periodic (called from borond's own asyncio loop, every few
    seconds): moves any worker process still sitting in lshttpd's own
    cgroup into its owning account's slice, keyed by the process's real
    uid against Account.uid. Root-privileged (borond's own level), so
    crossing the cgroup hierarchy here needs no special capability --
    this is the safe alternative to a setuid/capability helper binary
    (see module docstring). Returns the number of processes moved."""
    try:
        pids_text = LSHTTPD_CGROUP_PROCS.read_text()
    except OSError:
        return 0  # lshttpd not running / not yet started -- nothing to do

    pids = [int(p) for p in pids_text.split() if p.strip()]
    if not pids:
        return 0

    with write_session() as session:
        uid_map = _account_uid_map(session)

    moved = 0
    for pid in pids:
        try:
            uid = os.stat(f"/proc/{pid}").st_uid
        except OSError:
            continue  # process exited between listing and stat -- not an error

        if uid < MIN_ACCOUNT_UID:
            continue  # root/system processes (lshttpd itself, etc.), not a hosting account

        username = uid_map.get(uid)
        if username is None:
            continue  # uid doesn't belong to any known account (e.g. a non-Boron service)

        target = _cgroup_path(username) / "cgroup.procs"
        try:
            target.write_text(str(pid))
            moved += 1
        except OSError as exc:
            logger.warning("failed to move pid %d (uid %d, account '%s') into its slice: %s", pid, uid, username, exc)

    return moved
