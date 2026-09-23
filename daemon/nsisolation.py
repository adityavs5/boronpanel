"""Phase 6b: per-account OLS Namespace Container gating (docs/NAMESPACE-DESIGN.md,
docs/NAMESPACE-ANSWERS.md).

OLS's own `lsnsctl` implements an opt-OUT model, not an allowlist: once the
server-level `namespace`/`namespaceConf` directives (set once, as one-time
infrastructure -- not touched by this module) are live, every uid at or
above the configured `min_uid` floor is namespaced automatically unless it
is explicitly present in the disabled-uids denylist. This module owns
exactly that per-uid denylist toggle (`lsnsctl enable-uid`/`disable-uid`)
plus `/var/lsns/<uid>` teardown on termination.

Deliberately stores NO persistent per-account state of its own: "enabled"
status is always derived live from `lsnsctl`'s own denylist + min_uid floor
(both already persisted in /usr/local/lsws/lsns/conf/*.conf, surviving
daemon restarts and OLS reloads) rather than a duplicate DB column/table
that could drift out of sync with the actual gate. Scope accepted per
docs/NAMESPACE-ANSWERS.md's "Scope decision": mount-only isolation, not
`/proc`/process isolation, which OLS's native feature cannot provide.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, NamespaceMigrationJob, utcnow
from shared.validation import validate_username

from daemon import audit
from daemon.procutil import run

logger = logging.getLogger("borond.nsisolation")

LSNSCTL_BIN = "/usr/local/lsws/lsns/bin/lsnsctl"

# Single worker, deliberately: Step 4's own safety rule is "one at a time,
# verified between each, stop on first failure" -- concurrency here would
# defeat that by construction, not just make it harder to reason about.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ns-migration")


class NamespaceError(Exception):
    pass


def _account_uid(username: str) -> int:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NamespaceError(f"account '{username}' not found")
        if account.uid is None:
            raise NamespaceError(f"account '{username}' has no uid assigned")
        return account.uid


def get_min_uid() -> int:
    result = run([LSNSCTL_BIN, "get-min-uid"], timeout=15)
    if not result.ok:
        raise NamespaceError(f"lsnsctl get-min-uid failed: {result.stderr.strip() or result.stdout.strip()}")
    # lsnsctl writes its `[INFO]`-prefixed log lines to stderr, not stdout
    # (confirmed live, Phase 6b Step 2 -- only JSON-payload subcommands like
    # list-disabled-uids use stdout) -- search both to be safe regardless of
    # which stream a given subcommand happens to use.
    combined = result.stdout + result.stderr
    match = re.search(r"Minimum UID:\s*(\d+)", combined)
    if not match:
        raise NamespaceError(f"could not parse lsnsctl get-min-uid output: stdout={result.stdout!r} stderr={result.stderr!r}")
    return int(match.group(1))


def list_disabled_uids() -> list[int]:
    result = run([LSNSCTL_BIN, "list-disabled-uids"], timeout=15)
    if not result.ok:
        raise NamespaceError(f"lsnsctl list-disabled-uids failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        raw = json.loads(result.stdout.strip())
    except ValueError as exc:
        raise NamespaceError(f"could not parse lsnsctl list-disabled-uids output: {result.stdout!r}") from exc
    # lsnsctl returns uids as JSON *strings* (confirmed live: `["1001"]`, not
    # `[1001]`) -- cast to int so callers can compare directly against an
    # Account.uid without every call site needing to remember this. Found
    # live, Step 3: a disable-uid call's own immediately-following
    # get_status() reported `explicitly_disabled: false` right after
    # successfully disabling, because `1001 in ["1001"]` is False in Python
    # -- an int-vs-str mismatch, not a real timing/consistency race.
    return [int(u) for u in raw]


def _audit_lsnsctl(subcommand: str, uid: int, ok: bool, detail: str = "") -> None:
    """Step 6: 'log all lsnsctl calls to audit log'. Only the
    *state-changing* subcommands (enable-uid/disable-uid/unmount) are
    logged here, not the read-only status queries (get-min-uid/
    list-disabled-uids) -- matching this project's existing convention
    that audit entries record mutations, not lookups (`account.get`/
    `db.list` aren't audit-worthy events either).

    Actor is always "system"/"daemon": most real invocations happen as a
    CREATE_HOOKS/TERMINATE_HOOKS side effect of some other RPC (account
    create/terminate) or from the bulk-enable background job, neither of
    which has the original human caller's identity available down here
    (`dispatch()` in daemon/server.py pops `_actor`/`_role` off params
    before the handler ever runs). Direct `namespace.enable`/`.disable`
    RPC calls already get a *second*, human-attributed audit entry for
    free from that same generic dispatch-level logging -- this one adds
    the specific lsnsctl-level detail (exact uid, exact CLI outcome) that
    the generic entry doesn't have, and is the only audit trail at all for
    hook-triggered calls, which the generic per-RPC logging can't see
    since they aren't their own top-level RPC.

    Does NOT cover `set-min-uid`: Step 4 found that command is not
    reachable through this module at all (its own uid-range validation
    makes lowering the floor via the CLI impossible), so the one real
    `min_uid` change made so far was a manual `lsns.conf` edit, already
    described in full in docs/NAMESPACE-ANSWERS.md's Step 4 section
    rather than something this module's code path could log."""
    audit.record(
        "system", "daemon", f"lsnsctl.{subcommand}", str(uid), {},
        "ok" if ok else "failed", detail,
    )


def enable_uid(uid: int) -> None:
    result = run([LSNSCTL_BIN, "--uid", str(uid), "enable-uid"], timeout=15)
    ok = result.ok
    detail = "" if ok else (result.stderr.strip() or result.stdout.strip())
    _audit_lsnsctl("enable-uid", uid, ok, detail)
    if not ok:
        raise NamespaceError(f"lsnsctl enable-uid failed for uid {uid}: {detail}")


def disable_uid(uid: int) -> None:
    result = run([LSNSCTL_BIN, "--uid", str(uid), "disable-uid"], timeout=15)
    ok = result.ok
    detail = "" if ok else (result.stderr.strip() or result.stdout.strip())
    _audit_lsnsctl("disable-uid", uid, ok, detail)
    if not ok:
        raise NamespaceError(f"lsnsctl disable-uid failed for uid {uid}: {detail}")


def unmount_uid(uid: int) -> None:
    """Non-disruptive per docs/NAMESPACE-ANSWERS.md Q4: silently no-ops
    (creates a fresh namespace for future spawns instead) if live
    processes still hold a reference to the old one -- safe to call even
    if this uid was never namespaced at all."""
    result = run([LSNSCTL_BIN, "--uid", str(uid), "unmount"], timeout=15)
    _audit_lsnsctl("unmount", uid, result.ok, "" if result.ok else (result.stderr.strip() or result.stdout.strip()))


def get_status(username: str) -> dict:
    uid = _account_uid(username)
    min_uid = get_min_uid()
    disabled_uids = list_disabled_uids()
    eligible = uid >= min_uid
    explicitly_disabled = uid in disabled_uids
    return {
        "username": username,
        "uid": uid,
        "min_uid": min_uid,
        "eligible": eligible,
        "explicitly_disabled": explicitly_disabled,
        "enabled": eligible and not explicitly_disabled,
    }


def enable_namespace(params: dict) -> dict:
    """RPC: namespace.enable"""
    username = validate_username(params["username"])
    uid = _account_uid(username)
    enable_uid(uid)
    return get_status(username)


def disable_namespace(params: dict) -> dict:
    """RPC: namespace.disable"""
    username = validate_username(params["username"])
    uid = _account_uid(username)
    disable_uid(uid)
    return get_status(username)


def namespace_status(params: dict) -> dict:
    """RPC: namespace.status"""
    username = validate_username(params["username"])
    return get_status(username)


def enable_for_account(account: Account) -> None:
    """CREATE_HOOKS entry (also fires via reactivate_account, same list):
    new accounts get namespace isolation by default. A failure here is
    expected and benign for any uid still below the current min_uid floor
    (real accounts, until Step 4's deliberate migration lowers it) -- logged,
    not raised, so it never blocks account creation itself."""
    if account.uid is None:
        return
    try:
        enable_uid(account.uid)
    except NamespaceError:
        logger.info(
            "namespace not yet enabled for '%s' (uid %s) -- likely still below the min_uid floor, "
            "expected until Step 4's migration lowers it",
            account.username, account.uid,
        )


def teardown_account(account: Account) -> None:
    """TERMINATE_HOOKS entry: idempotent, safe even if this uid was never
    namespaced. Order matters relative to sysops.delete_linux_user(): must
    run before the uid is freed by userdel, while it can still be
    unambiguously identified."""
    if account.uid is None:
        return
    try:
        unmount_uid(account.uid)
    except Exception:
        logger.exception("lsnsctl unmount failed for '%s' (uid %s)", account.username, account.uid)
    try:
        disable_uid(account.uid)
    except Exception:
        logger.exception("lsnsctl disable-uid failed for '%s' (uid %s)", account.username, account.uid)


# --- Step 3/4: admin bulk-enable job (async, same table+executor pattern as
# daemon/appinstaller.py's AppInstallJob) --------------------------------


def _job_to_dict(job: NamespaceMigrationJob) -> dict:
    return {
        "id": job.id, "status": job.status, "total": job.total,
        "completed_count": job.completed_count, "current_username": job.current_username,
        "results": job.results, "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _update_job(job_id: int, **fields) -> None:
    with write_session() as session:
        job = session.get(NamespaceMigrationJob, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def trigger_bulk_enable(params: dict) -> dict:
    """RPC: namespace.bulk_enable.trigger. Admin-invoked (enforced at the
    API layer, same as every other admin-only action) -- enables every
    currently-active account not already enabled, one at a time, stopping
    immediately on the first failure per Step 4's explicit safety rule
    (no bulk migration without per-account verification)."""
    with write_session() as session:
        usernames = list(
            session.scalars(select(Account.username).where(Account.status == "active")).all()
        )
        job = NamespaceMigrationJob(status="pending", total=len(usernames))
        session.add(job)
        session.flush()
        job_id = job.id

    _executor.submit(_run_bulk_enable_job, job_id, usernames)
    return _job_to_dict(job)


def get_bulk_enable_job(params: dict) -> dict:
    """RPC: namespace.bulk_enable.get"""
    job_id = int(params["job_id"])
    with write_session() as session:
        job = session.get(NamespaceMigrationJob, job_id)
        if job is None:
            raise NamespaceError(f"namespace migration job {job_id} not found")
        return _job_to_dict(job)


def _run_bulk_enable_job(job_id: int, usernames: list[str]) -> None:
    _update_job(job_id, status="running")
    results: list[dict] = []
    for username in usernames:
        _update_job(job_id, current_username=username)
        try:
            with write_session() as session:
                account = session.scalar(select(Account).where(Account.username == username))
                if account is None or account.status != "active":
                    raise NamespaceError(f"account '{username}' is no longer active")
            status = enable_namespace({"username": username})
            if not status["enabled"]:
                # Structurally possible (e.g. still below min_uid) without
                # enable_uid() itself raising -- treat exactly like a raised
                # error for this job's purposes: stop, don't skip and continue.
                raise NamespaceError(
                    f"'{username}' not enabled after enable-uid (uid {status['uid']} "
                    f"vs min_uid {status['min_uid']}) -- likely still below the floor"
                )
            results.append({"username": username, "ok": True, "detail": status})
        except Exception as exc:
            results.append({"username": username, "ok": False, "detail": str(exc)})
            _update_job(
                job_id, status="failed", results=results, error=f"stopped at '{username}': {exc}",
                completed_count=len(results) - 1, completed_at=utcnow(),
            )
            logger.error("namespace bulk-enable job %s stopped at '%s': %s", job_id, username, exc)
            return
        _update_job(job_id, results=results, completed_count=len(results))
    _update_job(job_id, status="completed", current_username=None, completed_at=utcnow())


# --- Step 6: health dashboard summary + anomaly detection ---------------


def health_summary(params: dict | None = None) -> dict:
    """RPC: namespace.health_summary. One `get_min_uid()`/
    `list_disabled_uids()` call each, then a plain in-Python pass over
    every active account's uid -- not one `lsnsctl` round trip per
    account, which would turn an admin dashboard load into N subprocess
    spawns.

    `anomalies`: active accounts that are namespace-*eligible* (uid >=
    min_uid) but explicitly disabled -- "namespace disabled on an account
    that should have it" (the goal's own Step 6 wording). A brand new
    account momentarily mid-CREATE_HOOKS wouldn't show up here since it's
    either not yet in the "active" status or already re-enabled by the
    time this runs; a real anomaly is an account someone (or some bug)
    explicitly disabled and never re-enabled."""
    min_uid = get_min_uid()
    disabled_uids = set(list_disabled_uids())
    with write_session() as session:
        accounts = session.scalars(
            select(Account).where(Account.status == "active", Account.uid.is_not(None))
        ).all()
        rows = [(a.username, a.uid) for a in accounts]

    total = len(rows)
    enabled = 0
    not_eligible = 0
    anomalies = []
    for username, uid in rows:
        eligible = uid >= min_uid
        disabled = uid in disabled_uids
        if not eligible:
            not_eligible += 1
        elif disabled:
            anomalies.append({"username": username, "uid": uid})
        else:
            enabled += 1

    return {
        "min_uid": min_uid,
        "total_active_accounts": total,
        "enabled_count": enabled,
        "not_yet_eligible_count": not_eligible,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }
