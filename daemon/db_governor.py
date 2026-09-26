"""Native MariaDB account limits and user statistics for Boron DB Monitor.

This is deliberately conservative.  Policies are selected only from Boron's
owned ``database_users`` inventory, start in monitor mode, and can be paused
globally.  Enforcement uses MariaDB's account resource limits; it never edits
system, migration, backup, or panel service users.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from daemon import mariadb
from shared.db import write_session
from shared.models import (
    Account, DatabaseUser, DbGovernorEvent, DbGovernorPolicy, FeatureControl,
    utcnow,
)
from shared.validation import ValidationError, validate_username

USERSTAT_CONFIG = Path("/etc/mysql/mariadb.conf.d/90-boron-userstat.cnf")


def _control(session) -> FeatureControl:
    row = session.get(FeatureControl, 1)
    if row is None:
        row = FeatureControl(id=1)
        session.add(row)
        session.flush()
    return row


def _policy_dict(row: DbGovernorPolicy | None) -> dict:
    if row is None:
        return {
            "id": None, "mode": "monitor", "max_user_connections": None,
            "max_queries_per_hour": None, "max_updates_per_hour": None,
            "max_connections_per_hour": None, "max_statement_time": None,
            "warning_threshold_pct": 80, "cooldown_seconds": 300,
        }
    return {
        "id": row.id, "mode": row.mode,
        "max_user_connections": row.max_user_connections,
        "max_queries_per_hour": row.max_queries_per_hour,
        "max_updates_per_hour": row.max_updates_per_hour,
        "max_connections_per_hour": row.max_connections_per_hour,
        "max_statement_time": row.max_statement_time,
        "warning_threshold_pct": row.warning_threshold_pct,
        "cooldown_seconds": row.cooldown_seconds,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _owned_users(session, account_id: int) -> list[tuple[str, str]]:
    return list(session.execute(
        select(DatabaseUser.db_user, DatabaseUser.host)
        .where(DatabaseUser.account_id == account_id)
        .order_by(DatabaseUser.db_user)
    ).all())


def _safe_limit(value, name: str, *, maximum: int = 1_000_000) -> int | None:
    if value in (None, ""):
        return None
    number = int(value)
    if number < 1 or number > maximum:
        raise ValidationError(f"{name} must be between 1 and {maximum}, or Unlimited")
    return number


def _apply_user(user: str, host: str, policy: DbGovernorPolicy | None) -> None:
    """Apply one policy. MariaDB uses zero for an unlimited account limit."""
    values = {
        "MAX_USER_CONNECTIONS": policy.max_user_connections if policy else 0,
        "MAX_QUERIES_PER_HOUR": policy.max_queries_per_hour if policy else 0,
        "MAX_UPDATES_PER_HOUR": policy.max_updates_per_hour if policy else 0,
        "MAX_CONNECTIONS_PER_HOUR": policy.max_connections_per_hour if policy else 0,
    }
    conn = mariadb._connect()
    try:
        account = f"{conn.escape(user)}@{conn.escape(host)}"
        clauses = " ".join(f"{key} {int(value or 0)}" for key, value in values.items())
        cur = conn.cursor()
        cur.execute(f"ALTER USER {account} WITH {clauses}")
        # MAX_STATEMENT_TIME is supported as an account limit by MariaDB 10.1+
        # but some vendor builds omit it. Apply it separately so the portable
        # connection/query limits are never lost when that optional clause is
        # unavailable.
        if policy and policy.max_statement_time is not None:
            try:
                cur.execute(
                    f"ALTER USER {account} WITH MAX_STATEMENT_TIME {float(policy.max_statement_time):.3f}"
                )
            except Exception as exc:
                raise ValidationError(
                    "This MariaDB build does not support MAX_STATEMENT_TIME as an account limit"
                ) from exc
        elif policy is None or policy.max_statement_time is None:
            try:
                cur.execute(f"ALTER USER {account} WITH MAX_STATEMENT_TIME 0")
            except Exception:
                pass
    finally:
        conn.close()


def _snapshot_policy(policy: DbGovernorPolicy | None) -> SimpleNamespace | None:
    if policy is None:
        return None
    return SimpleNamespace(
        max_user_connections=policy.max_user_connections,
        max_queries_per_hour=policy.max_queries_per_hour,
        max_updates_per_hour=policy.max_updates_per_hour,
        max_connections_per_hour=policy.max_connections_per_hour,
        max_statement_time=policy.max_statement_time,
    )


def _effective_enforced_policy(session, account_id: int) -> SimpleNamespace | None:
    """Return the native policy that must be installed, or Unlimited.

    This is deliberately resolved from durable global/account state each time
    rather than trusting UI state.  It is used by database-user lifecycle
    hooks and startup reconciliation so enforcement remains an invariant when
    users are created after a policy was saved.
    """
    control = _control(session)
    policy = session.scalar(select(DbGovernorPolicy).where(
        DbGovernorPolicy.scope_type == "account",
        DbGovernorPolicy.scope_id == account_id,
    ))
    if control.db_governor_mode != "enforce" or policy is None or policy.mode != "enforce":
        return None
    return _snapshot_policy(policy)


def apply_new_user(account_id: int, user: str, host: str) -> dict:
    """Apply the effective account policy before a new login is exposed."""
    with write_session() as session:
        if session.get(Account, account_id) is None:
            raise ValidationError("Account not found")
        policy = _effective_enforced_policy(session, account_id)
    if policy is not None:
        _apply_user(user, host, policy)
    return {"applied": policy is not None}


def reconcile_account(account_id: int) -> dict:
    with write_session() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise ValidationError("Account not found")
        users = _owned_users(session, account_id)
        policy = _effective_enforced_policy(session, account_id)
        username = account.username
    for user, host in users:
        _apply_user(user, host, policy)
    return {"username": username, "users": len(users), "applied": policy is not None}


def reconcile_all(params: dict | None = None) -> dict:
    """Re-establish native limits after daemon or MariaDB restarts."""
    with write_session() as session:
        account_ids = sorted(set(session.scalars(select(DatabaseUser.account_id)).all()))
    errors = []
    reconciled = 0
    for account_id in account_ids:
        try:
            reconcile_account(account_id)
            reconciled += 1
        except Exception as exc:
            errors.append({"account_id": account_id, "error": str(exc)[:1000]})
    return {"accounts": reconciled, "errors": errors}


def userstat_status(params: dict | None = None) -> dict:
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute("SHOW GLOBAL VARIABLES LIKE 'userstat'")
        row = cur.fetchone()
        enabled = bool(row and str(row[1]).lower() in {"on", "1"})
        cur.execute("SELECT VERSION()")
        version = str(cur.fetchone()[0])
    finally:
        conn.close()
    return {"enabled": enabled, "version": version}


def enable_userstat(params: dict) -> dict:
    if not bool(params.get("confirm")):
        raise ValidationError("Enabling MariaDB user statistics requires confirm=true")
    USERSTAT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = USERSTAT_CONFIG.with_name(f".{USERSTAT_CONFIG.name}.tmp.{os.getpid()}")
    temporary.write_text("# Managed by Boron\n[mariadb]\nuserstat=1\n")
    os.chmod(temporary, 0o644)
    try:
        result = mariadb._connect()
        try:
            cur = result.cursor()
            cur.execute("SET GLOBAL userstat = ON")
        finally:
            result.close()
        os.replace(temporary, USERSTAT_CONFIG)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return userstat_status()


def statistics(params: dict | None = None) -> dict:
    status = userstat_status()
    if not status["enabled"]:
        return {"status": status, "users": []}
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT USER, TOTAL_CONNECTIONS, CONCURRENT_CONNECTIONS, CONNECTED_TIME, "
            "BUSY_TIME, CPU_TIME, BYTES_RECEIVED, BYTES_SENT, ROWS_READ, ROWS_SENT, "
            "ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED "
            "FROM information_schema.USER_STATISTICS"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    with write_session() as session:
        owned = {
            row.db_user: account.username
            for row, account in session.execute(
                select(DatabaseUser, Account).join(Account, DatabaseUser.account_id == Account.id)
            ).all()
        }
    users = []
    for row in rows:
        if row[0] not in owned:
            continue
        users.append({
            "db_user": row[0], "username": owned[row[0]],
            "total_connections": int(row[1] or 0), "concurrent_connections": int(row[2] or 0),
            "connected_seconds": int(row[3] or 0), "busy_seconds": float(row[4] or 0),
            "cpu_seconds": float(row[5] or 0), "bytes_received": int(row[6] or 0),
            "bytes_sent": int(row[7] or 0), "rows_read": int(row[8] or 0),
            "rows_sent": int(row[9] or 0), "rows_inserted": int(row[10] or 0),
            "rows_updated": int(row[11] or 0), "rows_deleted": int(row[12] or 0),
        })
    return {"status": status, "users": sorted(users, key=lambda item: item["busy_seconds"], reverse=True)}


def overview(params: dict | None = None) -> dict:
    with write_session() as session:
        control = _control(session)
        accounts = session.scalars(select(Account).order_by(Account.username)).all()
        policies = {
            row.scope_id: row for row in session.scalars(
                select(DbGovernorPolicy).where(DbGovernorPolicy.scope_type == "account")
            ).all()
        }
        users = {account.id: _owned_users(session, account.id) for account in accounts}
        events = session.scalars(
            select(DbGovernorEvent).order_by(DbGovernorEvent.created_at.desc()).limit(250)
        ).all()
        account_names = {account.id: account.username for account in accounts}
        return {
            "global_mode": control.db_governor_mode,
            "accounts": [{
                "id": account.id, "username": account.username,
                "database_users": [f"{user}@{host}" for user, host in users[account.id]],
                "policy": _policy_dict(policies.get(account.id)),
            } for account in accounts if users[account.id]],
            "events": [{
                "id": event.id, "username": account_names.get(event.account_id),
                "database_user": event.database_user, "event_type": event.event_type,
                "action": event.action, "detail": event.detail,
                "created_at": event.created_at.isoformat(),
            } for event in events],
        }


def set_global_mode(params: dict) -> dict:
    mode = str(params.get("mode", "monitor"))
    if mode not in {"paused", "monitor", "enforce"}:
        raise ValidationError("Mode must be paused, monitor, or enforce")
    with write_session() as session:
        previous_global_mode = _control(session).db_governor_mode
        policies = session.scalars(select(DbGovernorPolicy).where(
            DbGovernorPolicy.scope_type == "account"
        )).all()
        operations = []
        for policy in policies:
            users = _owned_users(session, policy.scope_id)
            snapshot = _snapshot_policy(policy) if mode == "enforce" and policy.mode == "enforce" else None
            previous = _snapshot_policy(policy) if previous_global_mode == "enforce" and policy.mode == "enforce" else None
            operations.extend((policy.scope_id, user, host, snapshot, previous) for user, host in users)
    # Apply first. If MariaDB rejects a setting, the global mode remains at
    # its previous value instead of claiming an enforcement state that was
    # only partially installed.
    applied = []
    try:
        for _account_id, user, host, policy, previous in operations:
            _apply_user(user, host, policy)
            applied.append((user, host, previous))
    except Exception as exc:
        rollback_errors = []
        for user, host, previous in reversed(applied):
            try:
                _apply_user(user, host, previous)
            except Exception as rollback_exc:
                rollback_errors.append(f"{user}@{host}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                f"DB Governor mode change failed ({exc}); restoring prior limits also failed for "
                + "; ".join(rollback_errors)
            ) from exc
        raise
    with write_session() as session:
        control = _control(session)
        control.db_governor_mode = mode
        for account_id, user, _host, policy, _previous in operations:
            session.add(DbGovernorEvent(
                account_id=account_id, database_user=user, event_type="global_mode",
                action="limits_applied" if policy else "limits_removed",
                detail=f"DB Governor global mode changed to {mode}",
            ))
    return overview()


def save_policy(params: dict) -> dict:
    username = validate_username(params["username"])
    mode = str(params.get("mode", "monitor"))
    if mode not in {"monitor", "enforce"}:
        raise ValidationError("Policy mode must be monitor or enforce")
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError("Account not found")
        users = _owned_users(session, account.id)
        if not users:
            raise ValidationError("This account has no database users")
        row = session.scalar(select(DbGovernorPolicy).where(
            DbGovernorPolicy.scope_type == "account", DbGovernorPolicy.scope_id == account.id
        ))
        previous_mode = row.mode if row is not None else None
        if row is None:
            row = DbGovernorPolicy(scope_type="account", scope_id=account.id)
            session.add(row)
        row.mode = mode
        row.max_user_connections = _safe_limit(params.get("max_user_connections"), "Max user connections", maximum=10000)
        row.max_queries_per_hour = _safe_limit(params.get("max_queries_per_hour"), "Queries per hour")
        row.max_updates_per_hour = _safe_limit(params.get("max_updates_per_hour"), "Updates per hour")
        row.max_connections_per_hour = _safe_limit(params.get("max_connections_per_hour"), "Connections per hour")
        value = params.get("max_statement_time")
        row.max_statement_time = None if value in (None, "") else float(value)
        if row.max_statement_time is not None and not 0.1 <= row.max_statement_time <= 3600:
            raise ValidationError("Max statement time must be between 0.1 and 3600 seconds")
        row.warning_threshold_pct = int(params.get("warning_threshold_pct", 80))
        row.cooldown_seconds = int(params.get("cooldown_seconds", 300))
        if not 1 <= row.warning_threshold_pct <= 100 or not 30 <= row.cooldown_seconds <= 86400:
            raise ValidationError("Warning threshold or cooldown is outside the supported range")
        session.flush()
        control = _control(session)
        should_apply = control.db_governor_mode == "enforce" and mode == "enforce"
        account_id = account.id
        policy_id = row.id
        applied_policy = SimpleNamespace(
            max_user_connections=row.max_user_connections,
            max_queries_per_hour=row.max_queries_per_hour,
            max_updates_per_hour=row.max_updates_per_hour,
            max_connections_per_hour=row.max_connections_per_hour,
            max_statement_time=row.max_statement_time,
        )
    if should_apply:
        for user, host in users:
            _apply_user(user, host, applied_policy)
        action = "limits_applied"
    else:
        # A switch from enforce to monitor must remove limits immediately;
        # otherwise the UI would say monitor while MariaDB still rejects work.
        if previous_mode == "enforce":
            for user, host in users:
                _apply_user(user, host, None)
        action = "monitor_only"
    with write_session() as session:
        policy = session.get(DbGovernorPolicy, policy_id)
        session.add(DbGovernorEvent(
            account_id=account_id, database_user=", ".join(user for user, _ in users),
            event_type="policy_saved", action=action,
            detail="Native limits applied" if should_apply else "No MariaDB limits changed",
        ))
        result = _policy_dict(policy)
    return {"username": username, "policy": result, "applied": should_apply}


def reset_policy(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError("Account not found")
        users = _owned_users(session, account.id)
        row = session.scalar(select(DbGovernorPolicy).where(
            DbGovernorPolicy.scope_type == "account", DbGovernorPolicy.scope_id == account.id
        ))
        if row is not None:
            session.delete(row)
    for user, host in users:
        _apply_user(user, host, None)
    with write_session() as session:
        session.add(DbGovernorEvent(
            account_id=account.id, database_user=", ".join(user for user, _ in users),
            event_type="policy_reset", action="limits_removed", detail="Account limits reset to Unlimited",
        ))
    return {"username": username, "reset": True}


def evaluate(_params: dict | None = None) -> dict:
    """Record bounded threshold warnings from MariaDB user statistics."""
    snapshot = statistics()
    if not snapshot["status"]["enabled"]:
        return {"evaluated": 0, "events": 0}
    now = utcnow()
    with write_session() as session:
        account_by_name = {row.username: row.id for row in session.scalars(select(Account)).all()}
        policies = {row.scope_id: row for row in session.scalars(select(DbGovernorPolicy).where(
            DbGovernorPolicy.scope_type == "account"
        )).all()}
        created = 0
        for item in snapshot["users"]:
            account_id = account_by_name.get(item["username"])
            policy = policies.get(account_id)
            if policy is None or not policy.max_user_connections:
                continue
            threshold = max(1, int(policy.max_user_connections * policy.warning_threshold_pct / 100))
            if item["concurrent_connections"] < threshold:
                continue
            cutoff = now - dt.timedelta(seconds=policy.cooldown_seconds)
            recent = session.scalar(select(DbGovernorEvent.id).where(
                DbGovernorEvent.account_id == account_id,
                DbGovernorEvent.database_user == item["db_user"],
                DbGovernorEvent.event_type == "connection_threshold",
                DbGovernorEvent.created_at >= cutoff,
            ).limit(1))
            if recent is not None:
                continue
            session.add(DbGovernorEvent(
                account_id=account_id, database_user=item["db_user"],
                event_type="connection_threshold", action="observed",
                detail=(f"{item['concurrent_connections']} concurrent connections; "
                        f"warning threshold {threshold}, limit {policy.max_user_connections}"),
            ))
            created += 1
    return {"evaluated": len(snapshot["users"]), "events": created}
