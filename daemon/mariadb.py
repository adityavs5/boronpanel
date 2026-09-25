"""MariaDB provisioning for hosted-account databases (Phase d).

ARCHITECTURE.md SS4/SS5: DB and DB-user names are prefixed with the account's
own username (CyberPanel/ISPConfig convention, RESEARCH.md SS5), validated
through shared.validation.validate_db_identifier before ever touching SQL --
CREATE DATABASE/CREATE USER identifiers can't be parameterized, so allowlist
validation here is the actual injection defense, not a formality.

The daemon authenticates as a dedicated `boron_daemon` admin-equivalent
MariaDB user, never the bare `root` account (set up once, outside the app,
documented in README's setup steps) -- this module assumes that user already
exists with the necessary CREATE/DROP/GRANT privileges.
"""
from __future__ import annotations

import ipaddress
import re
import pymysql

from shared.config import settings
from shared.validation import ValidationError, generate_strong_password, validate_db_identifier


class MariaDbError(Exception):
    pass


def validate_database_host(host: str) -> str:
    """Allow one exact client host/IP, never a MariaDB wildcard pattern."""
    value = str(host or "localhost").strip().lower().rstrip(".")
    if value == "localhost":
        return value
    if not value or len(value) > 253 or any(char in value for char in "%_'\"\\\0\r\n\t "):
        raise ValidationError("Enter one exact database host or IP address; wildcards are not allowed")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    labels = value.split(".")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValidationError("Enter a valid exact database hostname or IP address")
    return value


def _connect():
    return pymysql.connect(
        unix_socket=settings.mariadb_socket,
        user=settings.mariadb_admin_user,
        password=settings.mariadb_admin_password,
        autocommit=True,
    )


def _quote_ident(name: str) -> str:
    """Validate then backtick-quote a SQL identifier. Defense in depth on
    top of validate_db_identifier: even a validated identifier is backtick-
    escaped here rather than trusted to be safe to concatenate as-is."""
    validate_db_identifier(name)
    return "`" + name.replace("`", "``") + "`"


def generate_password(length: int = 24) -> str:
    return generate_strong_password(length)


def database_exists(db_name: str) -> bool:
    validate_db_identifier(db_name)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (db_name,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def user_exists(db_user: str, host: str = "localhost") -> bool:
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT User FROM mysql.user WHERE User = %s AND Host = %s", (db_user, host))
            return cur.fetchone() is not None
    finally:
        conn.close()


def create_database(db_name: str) -> None:
    ident = _quote_ident(db_name)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {ident} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        conn.close()


def drop_database(db_name: str) -> None:
    ident = _quote_ident(db_name)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {ident}")
    finally:
        conn.close()


def create_db_user(db_user: str, password: str, host: str = "localhost") -> None:
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # CREATE USER's username/host can't take a placeholder either,
            # but the password CAN be parameterized (it's a literal, not an
            # identifier) -- so it never touches string formatting.
            cur.execute(
                f"CREATE USER IF NOT EXISTS '{db_user}'@'{host}' IDENTIFIED BY %s",
                (password,),
            )
    finally:
        conn.close()


def create_db_user_from_hash(db_user: str, password_hash: str, host: str = "localhost") -> None:
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    if not re.fullmatch(r"\*[0-9A-F]{40}", str(password_hash or "")):
        raise ValidationError("Invalid database authentication hash")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE USER '{db_user}'@'{host}' IDENTIFIED BY PASSWORD %s", (password_hash,))
    finally:
        conn.close()


def set_password(db_user: str, password: str, host: str = "localhost") -> None:
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER USER '{db_user}'@'{host}' IDENTIFIED BY %s", (password,))
    finally:
        conn.close()


def drop_db_user(db_user: str, host: str = "localhost") -> None:
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP USER IF EXISTS '{db_user}'@'{host}'")
    finally:
        conn.close()


# Explicit privilege set granted to a hosted account's own DB user --
# deliberately enumerated rather than using the "ALL PRIVILEGES" keyword.
# MariaDB requires the granting user to itself hold every privilege it
# grants, so this list is scoped to exactly what boron_daemon's setup
# grant provides (CREATE/DROP/ALTER/INDEX/REFERENCES + standard DML) --
# covers ordinary CRUD/DDL use (WordPress and most PHP CMSs need nothing
# more). It deliberately excludes CREATE VIEW/TRIGGER/EVENT/ROUTINE/EXECUTE:
# granting those to boron_daemon itself was flagged by this
# environment's permission classifier as a global-privilege expansion
# requiring explicit operator authorization, since the project goal never
# asked for that. Rather than work around the block, the hosted-account
# privilege surface was scoped down to match what was already safely
# grantable -- documented in CHECKPOINT-d.md as a deliberate v1 limitation,
# not an oversight. An operator who wants stored-procedure/trigger/view
# support for hosted databases can grant boron_daemon those specific
# privileges themselves and widen this constant to match.
HOSTED_DB_PRIVILEGES = (
    "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES, "
    "CREATE TEMPORARY TABLES, LOCK TABLES"
)

HOSTED_DB_PRIVILEGE_NAMES = (
    "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX",
    "REFERENCES", "CREATE TEMPORARY TABLES", "LOCK TABLES",
)
READ_ONLY_DB_PRIVILEGES = ("SELECT",)


def normalize_database_privileges(preset: str = "all", custom=None) -> tuple[str, tuple[str, ...]]:
    preset = str(preset or "all").lower()
    if preset == "all":
        return preset, HOSTED_DB_PRIVILEGE_NAMES
    if preset == "read_only":
        return preset, READ_ONLY_DB_PRIVILEGES
    if preset != "custom":
        raise ValidationError("Choose full access, read-only access, or custom privileges")
    if not isinstance(custom, (list, tuple)):
        raise ValidationError("Choose at least one custom database privilege")
    allowed = set(HOSTED_DB_PRIVILEGE_NAMES)
    selected = []
    for value in custom:
        name = str(value).strip().upper().replace("_", " ")
        if name not in allowed:
            raise ValidationError(f"Unsupported hosted database privilege: {value}")
        if name not in selected:
            selected.append(name)
    if not selected:
        raise ValidationError("Choose at least one custom database privilege")
    return preset, tuple(selected)


def grant_all(db_name: str, db_user: str, host: str = "localhost") -> None:
    # Database-level GRANT patterns treat `_` and `%` as wildcards even in
    # backticks. All hosted database names contain an underscore, so the
    # general identifier quoter is insufficient for an authorization grant.
    grant_exact_database(db_name, db_user, host)


def _grant_database_pattern(db_name: str) -> str:
    validate_db_identifier(db_name)
    return '`' + db_name.replace('_', r'\_').replace('%', r'\%') + '`'


def grant_exact_database(db_name: str, db_user: str, host: str = "localhost") -> None:
    """Database GRANT patterns treat underscores specially even inside backticks."""
    validate_db_identifier(db_name)
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    db_ident = _grant_database_pattern(db_name)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"GRANT {HOSTED_DB_PRIVILEGES} ON {db_ident}.* TO '{db_user}'@'{host}'")
            cur.execute("FLUSH PRIVILEGES")
    finally:
        conn.close()


def revoke_all(db_name: str, db_user: str, host: str = "localhost") -> None:
    db_ident = _grant_database_pattern(db_name)
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"REVOKE ALL PRIVILEGES ON {db_ident}.* FROM '{db_user}'@'{host}'")
            cur.execute("FLUSH PRIVILEGES")
    finally:
        conn.close()


def grant_database_privileges(db_name: str, db_user: str, host: str = "localhost", *, preset="all", custom=None):
    validate_db_identifier(db_user)
    host = validate_database_host(host)
    preset, privileges = normalize_database_privileges(preset, custom)
    db_ident = _grant_database_pattern(db_name)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # Replacing the database-scoped grant prevents stale privileges
            # surviving a preset change. User/global grants are untouched.
            try:
                cur.execute(f"REVOKE ALL PRIVILEGES ON {db_ident}.* FROM '{db_user}'@'{host}'")
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1141, 1269):
                    raise
            cur.execute(f"GRANT {', '.join(privileges)} ON {db_ident}.* TO '{db_user}'@'{host}'")
            cur.execute("FLUSH PRIVILEGES")
    finally:
        conn.close()
    return preset, privileges


def rename_db_user(old_user: str, new_user: str, host: str = "localhost") -> None:
    validate_db_identifier(old_user)
    validate_db_identifier(new_user)
    host = validate_database_host(host)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"RENAME USER '{old_user}'@'{host}' TO '{new_user}'@'{host}'")
    finally:
        conn.close()


def database_statistics(db_names: list[str]) -> dict[str, dict]:
    if not db_names:
        return {}
    for name in db_names:
        validate_db_identifier(name)
    result = {name: {"size_bytes": 0, "table_count": 0, "engines": []} for name in db_names}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(db_names))
            cur.execute(
                f"SELECT TABLE_SCHEMA, COUNT(*), COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) "
                f"FROM information_schema.TABLES WHERE TABLE_SCHEMA IN ({placeholders}) GROUP BY TABLE_SCHEMA",
                db_names,
            )
            for name, count, size in cur.fetchall():
                result[name].update(table_count=int(count), size_bytes=int(size or 0))
            cur.execute(
                f"SELECT TABLE_SCHEMA, ENGINE FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA IN ({placeholders}) AND ENGINE IS NOT NULL GROUP BY TABLE_SCHEMA, ENGINE",
                db_names,
            )
            for name, engine in cur.fetchall():
                result[name]["engines"].append(engine)
    finally:
        conn.close()
    return result


def check_database(db_name: str, *, repair: bool = False) -> dict:
    validate_db_identifier(db_name)
    conn = _connect()
    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE'", (db_name,))
            tables = cur.fetchall()
            for table, engine in tables:
                ident = _quote_ident(db_name) + ".`" + table.replace("`", "``") + "`"
                operation = "CHECK"
                note = None
                if repair:
                    if str(engine or "").upper() not in {"MYISAM", "ARIA"}:
                        rows.append({"table": table, "engine": engine, "operation": "skipped", "status": "not_applicable", "message": "InnoDB and this engine recover through transactions/crash recovery; REPAIR TABLE is not used."})
                        continue
                    operation = "REPAIR"
                cur.execute(f"{operation} TABLE {ident}")
                response = cur.fetchall()
                final = response[-1] if response else (None, None, "error", "No result returned")
                rows.append({"table": table, "engine": engine, "operation": operation.lower(), "status": final[2], "message": final[3], "note": note})
    finally:
        conn.close()
    return {"database": db_name, "operation": "repair" if repair else "check", "tables": rows}


def database_size_bytes(db_names: list[str]) -> int:
    """Phase 2 feature 5: real size from information_schema, the same
    source `SHOW TABLE STATUS`/phpMyAdmin's own size column reads from --
    not an estimate, so it matches what an operator would see querying
    MySQL directly by hand."""
    if not db_names:
        return 0
    for name in db_names:
        validate_db_identifier(name)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(db_names))
            cur.execute(
                f"SELECT COALESCE(SUM(data_length + index_length), 0) "
                f"FROM information_schema.tables WHERE table_schema IN ({placeholders})",
                db_names,
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()
