"""Live MariaDB monitor for admins (missing-features batch, goal feature 7)
-- explicitly separate from the existing slow query LOG viewer
(`daemon/slowquery.py`, Phase 5 feature 8): that feature reads
`mysql.slow_log` for queries that already finished and were slow enough to
be logged; this feature shows what's happening RIGHT NOW (`SHOW FULL
PROCESSLIST`) plus a live, always-available "slow queries in the last hour"
view over the same log table, DB sizes per account, and connection counts,
with a kill-query action -- a genuinely different admin surface (goal:
"Auto-refresh 10s"), not a re-skin of the existing one.

Reuses `daemon/mariadb._connect()` (the same `forgehost_daemon` admin
credential every other MariaDB-touching feature in this project already
authenticates as -- ARCHITECTURE.md SS4) rather than opening a second
connection mechanism; `KILL` and `SHOW FULL PROCESSLIST` need no privilege
beyond what that account already has confirmed live (`daemon/slowquery.py`'s
own module docstring: SUPER is NOT held, and was deliberately not
requested -- a plain, non-SUPER account can still `SHOW PROCESSLIST` and
`KILL` its OWN connections' threads; whether `forgehost_daemon` can see/kill
every OTHER account's threads too depends on whether it holds the PROCESS
privilege, which its blanket `SELECT ON *.*` grant does not by itself imply
-- confirmed empirically the first time this feature is exercised live
rather than assumed, per this project's own "verify, don't assume"
precedent; see the CHECKPOINT for this feature for the actual live result).
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DatabaseGrant
from shared.validation import ValidationError

from daemon import mariadb
from daemon.procutil import run

SLOW_QUERY_LOOKBACK_HOURS = 1
MAX_PROCESSLIST_INFO_LEN = 2000

# Confirmed live during this feature's own verification (not assumed, per
# this module's own docstring above): forgehost_daemon can SHOW FULL
# PROCESSLIST and KILL its OWN threads (any non-SUPER account can), but a
# real cross-account kill test -- a query held open as a hosted account's
# own MariaDB user -- got "You are not owner of thread N", confirming
# forgehost_daemon does NOT hold PROCESS/CONNECTION_ADMIN, so the "Kill
# query" button silently could not do its one actual job (killing a
# CUSTOMER's runaway query, not the daemon's own). Fixed the same way
# daemon/slowquery.py's bootstrap_slow_query_log widens forgehost_daemon's
# capability for its own feature: an explicit, confirm=true-gated,
# admin-triggered one-time action, not a silent ambient grant --
# CONNECTION_ADMIN (MariaDB 10.5+, this box is 10.11) rather than the
# broader SUPER, the same "narrowest privilege that actually does the job"
# posture HOSTED_DB_PRIVILEGES/CHECKPOINT-d.md already established for
# this account. Granted via the `mysql` CLI authenticating over the local
# unix socket as the OS root user (forgehostd already runs as root, and
# MariaDB's root@localhost account uses unix_socket auth on this box,
# confirmed live via `mysql -e "SELECT CURRENT_USER()"` returning
# root@localhost with no password) -- forgehost_daemon itself has no
# GRANT OPTION on CONNECTION_ADMIN and could not grant this to itself.
KILL_QUERY_PRIVILEGE = "CONNECTION_ADMIN"


def kill_query_privilege_status() -> dict:
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SHOW GRANTS FOR {settings.mariadb_admin_user}@localhost")
        grants = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    granted = any(KILL_QUERY_PRIVILEGE in g or "ALL PRIVILEGES" in g for g in grants)
    return {"privilege": KILL_QUERY_PRIVILEGE, "granted": granted}


def bootstrap_kill_query_privilege(params: dict) -> dict:
    """Admin-triggered, confirm=true-gated: grants forgehost_daemon the
    privilege its own kill-query action actually needs against OTHER
    accounts' connections. No service restart/interruption (unlike
    slowquery's bootstrap) -- GRANT takes effect immediately, so this is
    lower-stakes than that one, but still real, permanent widening of a
    credential's SQL privileges, so it stays explicit rather than
    ambient."""
    if not bool(params.get("confirm", False)):
        raise ValidationError(f"granting {KILL_QUERY_PRIVILEGE} requires confirm=true")

    result = run(
        ["mysql", "-e", f"GRANT {KILL_QUERY_PRIVILEGE} ON *.* TO `{settings.mariadb_admin_user}`@'localhost'"],
        timeout=15,
    )
    if not result.ok:
        raise RuntimeError(f"GRANT failed: {result.stderr.strip() or result.stdout.strip()}")

    status = kill_query_privilege_status()
    if not status["granted"]:
        raise RuntimeError(f"GRANT command succeeded but {KILL_QUERY_PRIVILEGE} is not showing in SHOW GRANTS -- not applied")
    return status


def _account_map_by_db(session) -> dict[str, str]:
    """db_name -> owning account's username, for attributing PROCESSLIST/DB
    size rows back to a Forgehost account in the UI."""
    rows = session.execute(select(DatabaseGrant.db_name, Account.username).join(Account, DatabaseGrant.account_id == Account.id)).all()
    return {db_name: username for db_name, username in rows}


def get_processlist() -> dict:
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute("SHOW FULL PROCESSLIST")
        columns = [d[0].lower() for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()

    with write_session() as session:
        db_to_account = _account_map_by_db(session)

    processes = []
    for row in rows:
        entry = dict(zip(columns, row))
        info = entry.get("info")
        if isinstance(info, str) and len(info) > MAX_PROCESSLIST_INFO_LEN:
            info = info[:MAX_PROCESSLIST_INFO_LEN] + "... (truncated)"
        processes.append({
            "id": entry.get("id"),
            "user": entry.get("user"),
            "host": entry.get("host"),
            "db": entry.get("db"),
            "command": entry.get("command"),
            "time_seconds": entry.get("time"),
            "state": entry.get("state"),
            "info": info,
            "username": db_to_account.get(entry.get("db")) if entry.get("db") else None,
        })
    return {"processes": processes}


def get_recent_slow_queries() -> dict:
    """Same `mysql.slow_log` source `daemon/slowquery.py` reads, scoped to
    the last hour and with no filter/search params -- a live-dashboard
    view, not the full historical log browser that feature already is."""
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT start_time, user_host, query_time, lock_time, rows_sent, rows_examined, db, sql_text, thread_id "
            "FROM mysql.slow_log WHERE start_time >= NOW() - INTERVAL %s HOUR ORDER BY start_time DESC LIMIT 200",
            (SLOW_QUERY_LOOKBACK_HOURS,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    queries = []
    for start_time, user_host, query_time, lock_time, rows_sent, rows_examined, db, sql_text, thread_id in rows:
        queries.append({
            "start_time": start_time.isoformat() if hasattr(start_time, "isoformat") else str(start_time),
            "user_host": user_host,
            "query_time_seconds": query_time.total_seconds() if hasattr(query_time, "total_seconds") else float(query_time),
            "lock_time_seconds": lock_time.total_seconds() if hasattr(lock_time, "total_seconds") else float(lock_time),
            "rows_sent": rows_sent,
            "rows_examined": rows_examined,
            "db": db,
            "sql_text": sql_text,
            "thread_id": thread_id,
        })
    return {"lookback_hours": SLOW_QUERY_LOOKBACK_HOURS, "queries": queries}


def get_db_sizes() -> dict:
    """Real size from information_schema (same source `daemon/mariadb.py`'s
    own `database_size_bytes` already uses for the per-account usage page),
    one query for every database at once rather than N per-account round
    trips, then mapped back to owning accounts in Python."""
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_schema, COALESCE(SUM(data_length + index_length), 0) "
            "FROM information_schema.tables GROUP BY table_schema"
        )
        size_by_schema = {schema: int(size) for schema, size in cur.fetchall()}
    finally:
        conn.close()

    with write_session() as session:
        grants = session.scalars(select(DatabaseGrant)).all()
        accounts_by_id = {a.id: a.username for a in session.scalars(select(Account)).all()}

    per_account: dict[str, dict] = {}
    for grant in grants:
        username = accounts_by_id.get(grant.account_id, f"#{grant.account_id}")
        size = size_by_schema.get(grant.db_name, 0)
        entry = per_account.setdefault(username, {"username": username, "databases": [], "total_bytes": 0})
        entry["databases"].append({"db_name": grant.db_name, "size_bytes": size})
        entry["total_bytes"] += size

    accounts = sorted(per_account.values(), key=lambda e: e["total_bytes"], reverse=True)
    return {
        "accounts": accounts,
        "server_total_bytes": sum(e["total_bytes"] for e in accounts),
        "unattributed_schemas": sorted(set(size_by_schema) - {g.db_name for g in grants}),
    }


def get_connection_summary() -> dict:
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute("SHOW STATUS LIKE 'Threads_connected'")
        total = int(cur.fetchone()[1])
        cur.execute("SHOW VARIABLES LIKE 'max_connections'")
        max_connections = int(cur.fetchone()[1])
        cur.execute("SHOW FULL PROCESSLIST")
        columns = [d[0].lower() for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()

    with write_session() as session:
        db_to_account = _account_map_by_db(session)

    per_account: dict[str, int] = {}
    for row in rows:
        entry = dict(zip(columns, row))
        db = entry.get("db")
        username = db_to_account.get(db) if db else None
        if username:
            per_account[username] = per_account.get(username, 0) + 1

    return {
        "total_connections": total,
        "max_connections": max_connections,
        "per_account": [{"username": u, "connections": c} for u, c in sorted(per_account.items(), key=lambda kv: kv[1], reverse=True)],
    }


ER_KILL_DENIED_ERROR = 1095


def kill_query(thread_id) -> dict:
    """Audit 3 finding A3-6: once KILL_QUERY_PRIVILEGE is granted,
    `KILL <thread_id>` alone can terminate ANY MariaDB connection on the
    server -- another admin's own session, an in-progress mysqldump/backup
    connection, a replication thread, or forgehost_daemon's own connections
    -- not just a hosted account's runaway query, which is the feature's
    entire stated purpose (see DbMonitor.jsx's own copy). Scope the kill to
    threads whose `db` is a real hosted-account database (present in
    DatabaseGrant, the same authoritative ownership table get_processlist
    already uses to attribute rows) before ever issuing KILL."""
    import pymysql

    thread_id = int(thread_id)
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute("SHOW FULL PROCESSLIST")
        columns = [d[0].lower() for d in cur.description]
        target = next(
            (dict(zip(columns, row)) for row in cur.fetchall() if row[columns.index("id")] == thread_id),
            None,
        )
        if target is None:
            raise RuntimeError(f"thread {thread_id} is not currently active (already finished or never existed)")

        db_name = target.get("db")
        if not db_name:
            raise RuntimeError(
                f"refusing to kill thread {thread_id}: it has no associated database (not a hosted-account query)"
            )
        with write_session() as session:
            owned = session.scalar(select(DatabaseGrant).where(DatabaseGrant.db_name == db_name)) is not None
        if not owned:
            raise RuntimeError(
                f"refusing to kill thread {thread_id}: database '{db_name}' does not belong to any hosted account"
            )

        try:
            # KILL's target is a numeric thread id, not a value MariaDB
            # accepts a bind placeholder for (it isn't a DML/DQL statement) --
            # safe to interpolate directly since it's already coerced to
            # `int` above, the same "validate to a known-safe type, then
            # interpolate" posture daemon/mariadb.py's _quote_ident uses for
            # identifiers.
            cur.execute(f"KILL {thread_id}")
        except pymysql.err.OperationalError as exc:
            if exc.args and exc.args[0] == ER_KILL_DENIED_ERROR:
                raise RuntimeError(
                    f"cannot kill thread {thread_id} -- forgehost_daemon does not (yet) hold "
                    f"{KILL_QUERY_PRIVILEGE}; enable it once from the Database Monitor page"
                ) from exc
            raise
    finally:
        conn.close()
    return {"thread_id": thread_id, "status": "killed", "db": db_name}
