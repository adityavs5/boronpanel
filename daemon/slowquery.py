"""Phase 5 feature 8: MySQL/MariaDB slow query viewer.

Enabling `slow_query_log`/`long_query_time`/`log_output` needs MariaDB's
SUPER privilege via `SET GLOBAL` -- confirmed live that `forgehost_daemon`
does NOT have it (`Access denied; you need ... SUPER privilege`), and per
this project's own established precedent (`daemon/mariadb.py`'s
`HOSTED_DB_PRIVILEGES`, CHECKPOINT-d.md) broadening this account's SQL
grants is treated as a real decision, not a rubber stamp. Rather than
requesting SUPER, this feature applies the setting the same way every
other server-wide config change in this project does: write a real
MariaDB config file as root (`forgehostd` already has full OS-level
control of `/etc/mysql/` and of `mariadb.service`) and restart the
service -- no new SQL privilege needed at all, since the *reading* side
(querying `mysql.slow_log`) is already covered by `forgehost_daemon`'s
existing blanket `SELECT ON *.*` grant, confirmed live.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from shared.validation import ValidationError

from daemon import mariadb
from daemon.procutil import run

SLOWLOG_CONF_PATH = "/etc/mysql/mariadb.conf.d/60-forgehost-slowlog.cnf"
SLOW_QUERY_THRESHOLD_SECONDS = 1

_CONF_CONTENT = f"""# Managed by Forgehost (daemon/slowquery.py). Phase 5 feature 8.
[mysqld]
slow_query_log = 1
long_query_time = {SLOW_QUERY_THRESHOLD_SECONDS}
log_output = TABLE
"""


def get_status(params: dict) -> dict:
    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute("SHOW VARIABLES LIKE 'slow_query_log'")
        enabled = cur.fetchone()[1].upper() == "ON"
        cur.execute("SHOW VARIABLES LIKE 'long_query_time'")
        long_query_time = float(cur.fetchone()[1])
        cur.execute("SHOW VARIABLES LIKE 'log_output'")
        log_output = cur.fetchone()[1]
        return {
            "enabled": enabled,
            "long_query_time": long_query_time,
            "log_output": log_output,
            "config_managed": os.path.exists(SLOWLOG_CONF_PATH),
        }
    finally:
        conn.close()


def bootstrap_slow_query_log(params: dict) -> dict:
    """Writes the config file + restarts MariaDB -- a real, brief
    interruption for every hosted database connection, the same
    disruptive-action class Feature 2's service manager already requires
    explicit confirmation for, applied here too."""
    if not bool(params.get("confirm", False)):
        raise ValidationError("enabling the slow query log requires confirm=true (restarts MariaDB)")

    conf_path = Path(SLOWLOG_CONF_PATH)
    backup_content = conf_path.read_text() if conf_path.exists() else None
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(_CONF_CONTENT)

    result = run(["systemctl", "restart", "mariadb.service"], timeout=60)
    if not result.ok:
        _restore(conf_path, backup_content)
        raise RuntimeError(f"mariadb restart failed: {result.stderr.strip() or result.stdout.strip()}")

    status = get_status({})
    if not status["enabled"]:
        _restore(conf_path, backup_content)
        run(["systemctl", "restart", "mariadb.service"], timeout=60)
        raise RuntimeError("slow query log did not take effect after restart -- config change rolled back")
    return status


def _restore(conf_path: Path, backup_content: str | None) -> None:
    if backup_content is None:
        conf_path.unlink(missing_ok=True)
    else:
        conf_path.write_text(backup_content)


def _row_to_dict(row: tuple) -> dict:
    start_time, user_host, query_time, lock_time, rows_sent, rows_examined, db, sql_text, thread_id = row
    return {
        "start_time": start_time.isoformat() if isinstance(start_time, dt.datetime) else str(start_time),
        "user_host": user_host,
        "query_time_seconds": query_time.total_seconds() if isinstance(query_time, dt.timedelta) else float(query_time),
        "lock_time_seconds": lock_time.total_seconds() if isinstance(lock_time, dt.timedelta) else float(lock_time),
        "rows_sent": rows_sent,
        "rows_examined": rows_examined,
        "db": db,
        "sql_text": sql_text,
        "thread_id": thread_id,
    }


def list_slow_queries(params: dict) -> dict:
    db_filter = (params.get("db") or "").strip()
    search = (params.get("q") or "").strip()
    limit = max(1, min(int(params.get("limit", 100) or 100), 1000))

    conditions = []
    args: list = []
    if db_filter:
        conditions.append("db = %s")
        args.append(db_filter)
    if search:
        conditions.append("(sql_text LIKE %s OR db LIKE %s)")
        like = f"%{search}%"
        args.extend([like, like])
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = mariadb._connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT start_time, user_host, query_time, lock_time, rows_sent, rows_examined, db, sql_text, thread_id "
            f"FROM mysql.slow_log {where_clause} ORDER BY query_time DESC LIMIT %s",
            (*args, limit),
        )
        rows = cur.fetchall()
        return {"queries": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()
