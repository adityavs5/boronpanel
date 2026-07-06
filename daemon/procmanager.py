"""Phase 8 feature 10: process manager, strictly scoped to an account's uid.

Lists live processes owned by the account's own Linux uid (via psutil) and
kills a chosen one -- but only after re-confirming the target pid is owned by
that exact uid, so a customer can never signal another account's (or root's)
process, and an admin acting on account {u} only ever touches {u}'s processes.
"""
from __future__ import annotations

import time

import psutil
from sqlalchemy import select

from shared.db import write_session
from shared.models import Account
from shared.validation import validate_username

# A safety floor: never operate on system/service uids even if an Account row
# somehow carried one -- account uids are >= 1000 (useradd default range).
MIN_ACCOUNT_UID = 1000
_CPU_SAMPLE_SECONDS = 0.12


def _account_uid(username: str) -> int:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.uid is None:
            raise RuntimeError(f"account '{username}' has no uid assigned")
        if account.uid < MIN_ACCOUNT_UID:
            raise RuntimeError("refusing to manage processes for a system uid")
        return account.uid


def _proc_uid(proc: psutil.Process) -> int | None:
    try:
        return proc.uids().real
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def list_processes(params: dict) -> dict:
    username = validate_username(params["username"])
    uid = _account_uid(username)

    matching = []
    for proc in psutil.process_iter(["pid"]):
        if _proc_uid(proc) == uid:
            matching.append(proc)
            try:
                proc.cpu_percent(None)  # prime the CPU% counter (first call returns 0.0)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    time.sleep(_CPU_SAMPLE_SECONDS)  # let the CPU% delta accumulate
    now = time.time()
    entries = []
    for proc in matching:
        try:
            with proc.oneshot():
                cmdline = proc.cmdline()
                entries.append({
                    "pid": proc.pid,
                    "command": " ".join(cmdline) if cmdline else proc.name(),
                    "cpu_pct": round(proc.cpu_percent(None), 1),
                    "memory_bytes": proc.memory_info().rss,
                    "runtime_seconds": int(now - proc.create_time()),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    entries.sort(key=lambda e: e["cpu_pct"], reverse=True)
    return {"processes": entries, "count": len(entries), "uid": uid}


def kill_process(params: dict) -> dict:
    username = validate_username(params["username"])
    pid = int(params["pid"])
    uid = _account_uid(username)

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        raise RuntimeError(f"process {pid} does not exist")
    # Strict scoping: the pid MUST be owned by this account's own uid.
    if _proc_uid(proc) != uid:
        raise RuntimeError(f"process {pid} is not owned by account '{username}'")

    try:
        proc.terminate()  # SIGTERM
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()  # SIGKILL if it didn't exit gracefully
    except psutil.NoSuchProcess:
        pass  # already gone -- effectively success
    except psutil.AccessDenied as exc:
        raise RuntimeError(f"could not kill process {pid}: {exc}")
    return {"pid": pid, "status": "killed"}
