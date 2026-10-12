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

from daemon import database_operations, mariadb
from shared.db import write_session
from shared.models import (
    Account, DatabaseUser, DbGovernorEvent, DbGovernorPolicy, FeatureControl, PmaToken,
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
    users = set(session.execute(
        select(DatabaseUser.db_user, DatabaseUser.host)
        .where(DatabaseUser.account_id == account_id)
        .order_by(DatabaseUser.db_user)
    ).all())
    # Retained token rows represent still-live SQL principals even after the
    # launch token expires: cleanup keeps the row whenever native revocation
    # fails. They must therefore remain governed until the user is dropped.
    users.update((name, "localhost") for name in session.scalars(
        select(PmaToken.ephemeral_db_user).where(PmaToken.account_id == account_id)
    ).all())
    return sorted(users)


def _safe_limit(value, name: str, *, maximum: int = 1_000_000) -> int | None:
    if value in (None, ""):
        return None
    number = int(value)
    if number < 1 or number > maximum:
        raise ValidationError(f"{name} must be between 1 and {maximum}, or Unlimited")
    return number


def _statement_time_is_unsupported(exc: Exception) -> bool:
    """Recognize only MariaDB's explicit syntax/unsupported-feature errors.

    Clearing an optional limit is harmless on a build that never implements
    the clause. Permission, connection, and server failures must propagate so
    callers can roll back instead of committing contradictory durable state.
    """
    code = exc.args[0] if getattr(exc, "args", ()) else None
    return code in {1064, 1235}


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
            except Exception as exc:
                if not _statement_time_is_unsupported(exc):
                    raise
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


def _restore_native_users(
    touched: list[tuple[str, str]],
    prior_policy: SimpleNamespace | None,
    original: Exception,
    operation: str,
) -> None:
    rollback_errors = []
    for user, host in reversed(touched):
        try:
            _apply_user(user, host, prior_policy)
        except Exception as rollback_exc:
            rollback_errors.append(f"{user}@{host}: {rollback_exc}")
    if rollback_errors:
        raise RuntimeError(
            f"DB Governor {operation} failed ({original}); restoring prior limits also failed for "
            + "; ".join(rollback_errors)
        ) from original


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
        return apply_new_user_in_session(session, account_id, user, host)


def apply_new_user_in_session(session, account_id: int, user: str, host: str) -> dict:
    """Session-aware variant for restore transactions holding SQLite state."""
    if session.get(Account, account_id) is None:
        raise ValidationError("Account not found")
    policy = _effective_enforced_policy(session, account_id)
    if policy is not None:
        _apply_user(user, host, policy)
    return {"applied": policy is not None}


@database_operations.serialized
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
        account_ids = sorted(
            set(session.scalars(select(DatabaseUser.account_id)).all())
            | set(session.scalars(select(PmaToken.account_id)).all())
        )
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


@database_operations.serialized
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
            applied.append((user, host, previous))
            _apply_user(user, host, policy)
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
    try:
        with write_session() as session:
            control = _control(session)
            control.db_governor_mode = mode
            for account_id, user, _host, policy, _previous in operations:
                session.add(DbGovernorEvent(
                    account_id=account_id, database_user=user, event_type="global_mode",
                    action="limits_applied" if policy else "limits_removed",
                    detail=f"DB Governor global mode changed to {mode}",
                ))
    except Exception as exc:
        rollback_errors = []
        for user, host, previous in reversed(applied):
            try:
                _apply_user(user, host, previous)
            except Exception as rollback_exc:
                rollback_errors.append(f"{user}@{host}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                f"DB Governor state commit failed ({exc}); restoring prior limits also failed for "
                + "; ".join(rollback_errors)
            ) from exc
        raise
    return overview()


@database_operations.serialized
def save_policy(params: dict) -> dict:
    username = validate_username(params["username"])
    mode = str(params.get("mode", "monitor"))
    if mode not in {"monitor", "enforce"}:
        raise ValidationError("Policy mode must be monitor or enforce")
    value = params.get("max_statement_time")
    target = SimpleNamespace(
        mode=mode,
        max_user_connections=_safe_limit(params.get("max_user_connections"), "Max user connections", maximum=10000),
        max_queries_per_hour=_safe_limit(params.get("max_queries_per_hour"), "Queries per hour"),
        max_updates_per_hour=_safe_limit(params.get("max_updates_per_hour"), "Updates per hour"),
        max_connections_per_hour=_safe_limit(params.get("max_connections_per_hour"), "Connections per hour"),
        max_statement_time=None if value in (None, "") else float(value),
        warning_threshold_pct=int(params.get("warning_threshold_pct", 80)),
        cooldown_seconds=int(params.get("cooldown_seconds", 300)),
    )
    if target.max_statement_time is not None and not 0.1 <= target.max_statement_time <= 3600:
        raise ValidationError("Max statement time must be between 0.1 and 3600 seconds")
    if not 1 <= target.warning_threshold_pct <= 100 or not 30 <= target.cooldown_seconds <= 86400:
        raise ValidationError("Warning threshold or cooldown is outside the supported range")

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
        control = _control(session)
        account_id = account.id
        prior_native = (_snapshot_policy(row) if control.db_governor_mode == "enforce"
                        and row is not None and row.mode == "enforce" else None)
        target_native = target if control.db_governor_mode == "enforce" and mode == "enforce" else None

    touched = []
    if target_native is not None or prior_native is not None:
        try:
            for user, host in users:
                touched.append((user, host))
                _apply_user(user, host, target_native)
        except Exception as exc:
            _restore_native_users(touched, prior_native, exc, "policy update")
            raise
    try:
        with write_session() as session:
            policy = session.scalar(select(DbGovernorPolicy).where(
                DbGovernorPolicy.scope_type == "account", DbGovernorPolicy.scope_id == account_id
            ))
            if policy is None:
                policy = DbGovernorPolicy(scope_type="account", scope_id=account_id)
                session.add(policy)
            for field in ("mode", "max_user_connections", "max_queries_per_hour", "max_updates_per_hour",
                          "max_connections_per_hour", "max_statement_time", "warning_threshold_pct", "cooldown_seconds"):
                setattr(policy, field, getattr(target, field))
            session.flush()
            result = _policy_dict(policy)
            session.add(DbGovernorEvent(
                account_id=account_id, database_user=", ".join(user for user, _ in users),
                event_type="policy_saved", action="limits_applied" if target_native else "monitor_only",
                detail="Native limits applied" if target_native else "No native limits active",
            ))
    except Exception as exc:
        _restore_native_users(touched, prior_native, exc, "policy persistence")
        raise
    return {"username": username, "policy": result, "applied": target_native is not None}


@database_operations.serialized
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
        control = _control(session)
        account_id = account.id
        prior_native = (_snapshot_policy(row) if control.db_governor_mode == "enforce"
                        and row is not None and row.mode == "enforce" else None)
    touched = []
    try:
        if prior_native is not None:
            for user, host in users:
                touched.append((user, host))
                _apply_user(user, host, None)
        with write_session() as session:
            row = session.scalar(select(DbGovernorPolicy).where(
                DbGovernorPolicy.scope_type == "account", DbGovernorPolicy.scope_id == account_id
            ))
            if row is not None:
                session.delete(row)
            session.add(DbGovernorEvent(
                account_id=account_id, database_user=", ".join(user for user, _ in users),
                event_type="policy_reset", action="limits_removed", detail="Account limits reset to Unlimited",
            ))
    except Exception as exc:
        _restore_native_users(touched, prior_native, exc, "policy reset")
        raise
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
