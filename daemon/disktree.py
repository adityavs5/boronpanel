"""Interactive disk usage treemap (Phase 4 feature 7).

The goal's premise ("built from cached du output, already collected every
15min from Phase 2") is only partly true on inspection: Phase 2 feature 5
(daemon/usage.py) only ever runs `du -sb <home>` -- one scalar total per
account, no directory-level breakdown at all, since that's all its own
dashboard needs. There is no cached tree to read.

This module reuses that cached **total** for the root node (avoiding a
redundant top-level `du -sb` call -- daemon/usage.py's own snapshot is
already fresh to within ~15 minutes) but computes the actual per-directory
breakdown live, one level at a time, on demand:

  - `du --max-depth=1 -b <dir>` for immediate subdirectory sizes (its last
    line is the directory's own grand total, dropped here -- the cached
    Phase 2 total is used for the root, and a fresh one is taken for the
    directory being drilled into otherwise).
  - `find <dir> -maxdepth 1 -type f -printf '%s\\t%f\\n'` for immediate
    files -- `du --max-depth` does NOT report individual file sizes at all
    (confirmed empirically), only subdirectory subtotals.

This is a deliberate, more scalable design than eagerly recursing an
entire account's tree up front: "drill into subdirectories" becomes a real
interactive fetch-on-click (one shallow, fast `du`/`find` pair per click),
never a single unbounded recursive walk of a potentially huge tree the
customer may never look at.
"""
from __future__ import annotations

import os

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, UsageSnapshot, utcnow
from shared.validation import validate_protected_dir_relative_path, validate_username

from daemon import filemanager
from daemon.procutil import run
from daemon.usage import SNAPSHOT_MAX_AGE_SECONDS, _as_aware_utc

DU_TIMEOUT = 30.0
FIND_TIMEOUT = 30.0
TOP_FILES_TIMEOUT = 60.0
TOP_FILES_COUNT = 10


class DiskTreeError(Exception):
    pass


def _resolve_dir(username: str, relative_path: str) -> tuple[str, str]:
    relative_path = validate_protected_dir_relative_path(relative_path) if relative_path else ""
    resolved, home = filemanager._resolve(username, relative_path)
    if not os.path.isdir(resolved):
        raise DiskTreeError(f"'{relative_path}' is not a directory")
    return resolved, home


def _immediate_subdirs(path: str) -> list[dict]:
    result = run(["du", "--max-depth=1", "-b", path], timeout=DU_TIMEOUT)
    if not result.ok:
        raise DiskTreeError(f"du failed: {result.stderr.strip() or result.stdout.strip()}")
    entries = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        size_str, _, entry_path = line.partition("\t")
        if os.path.realpath(entry_path) == os.path.realpath(path):
            continue  # the grand-total line for `path` itself
        entries.append({"name": os.path.basename(entry_path.rstrip("/")), "size_bytes": int(size_str), "is_dir": True})
    return entries


def _immediate_files(path: str) -> list[dict]:
    result = run(["find", path, "-maxdepth", "1", "-type", "f", "-printf", "%s\t%f\n"], timeout=FIND_TIMEOUT)
    if not result.ok:
        raise DiskTreeError(f"find failed: {result.stderr.strip() or result.stdout.strip()}")
    entries = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        size_str, _, name = line.partition("\t")
        entries.append({"name": name, "size_bytes": int(size_str), "is_dir": False})
    return entries


def _total_size(path: str) -> int:
    result = run(["du", "-sb", path], timeout=DU_TIMEOUT)
    if not result.ok:
        raise DiskTreeError(f"du failed: {result.stderr.strip() or result.stdout.strip()}")
    return int(result.stdout.split(None, 1)[0])


def _cached_account_total_bytes(username: str) -> int | None:
    """Phase 2 feature 5's own periodic snapshot (daemon/usage.py,
    UsageSnapshot.disk_home_bytes -- specifically the account HOME
    figure, not the combined disk_total_bytes, since mail Maildirs and
    hosted-database files live outside the account home this treemap
    walks) -- reused for the root node only, avoiding a redundant
    `du -sb` of the whole account home when a value already fresh to
    within ~15 minutes exists. None if no snapshot has ever been taken
    yet (a brand new account) or it's already stale beyond that window,
    in which case the caller falls back to a fresh `du -sb`."""
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            return None
        latest = session.scalar(
            select(UsageSnapshot).where(UsageSnapshot.account_id == account.id).order_by(UsageSnapshot.taken_at.desc())
        )
        if latest is None:
            return None
        if (utcnow() - _as_aware_utc(latest.taken_at)).total_seconds() > SNAPSHOT_MAX_AGE_SECONDS:
            return None
        return latest.disk_home_bytes


def get_disk_tree(params: dict) -> dict:
    username = validate_username(params["username"])
    relative_path = params.get("path", "")
    resolved, home = _resolve_dir(username, relative_path)

    if resolved == home:
        total = _cached_account_total_bytes(username)
        if total is None:
            total = _total_size(resolved)
    else:
        total = _total_size(resolved)

    children = _immediate_subdirs(resolved) + _immediate_files(resolved)
    children.sort(key=lambda e: e["size_bytes"], reverse=True)

    return {
        "path": os.path.relpath(resolved, home) if resolved != home else "",
        "total_bytes": total,
        "children": children,
    }


def get_top_files(params: dict) -> dict:
    username = validate_username(params["username"])
    relative_path = params.get("path", "")
    resolved, home = _resolve_dir(username, relative_path)

    result = run(
        ["find", resolved, "-type", "f", "-printf", "%s\t%p\n"],
        timeout=TOP_FILES_TIMEOUT,
    )
    if not result.ok:
        raise DiskTreeError(f"find failed: {result.stderr.strip() or result.stdout.strip()}")

    files = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        size_str, _, full_path = line.partition("\t")
        files.append({"path": os.path.relpath(full_path, home), "size_bytes": int(size_str)})
    files.sort(key=lambda f: f["size_bytes"], reverse=True)
    return {"files": files[:TOP_FILES_COUNT]}
