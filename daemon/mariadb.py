"""MariaDB provisioning for hosted-account databases (Phase d).

ARCHITECTURE.md SS4/SS5: DB and DB-user names are prefixed with the account's
own username (CyberPanel/ISPConfig convention, RESEARCH.md SS5), validated
through shared.validation.validate_db_identifier before ever touching SQL --
CREATE DATABASE/CREATE USER identifiers can't be parameterized, so allowlist
validation here is the actual injection defense, not a formality.

The daemon authenticates as a dedicated `forgehost_daemon` admin-equivalent
MariaDB user, never the bare `root` account (set up once, outside the app,
documented in README's setup steps) -- this module assumes that user already
exists with the necessary CREATE/DROP/GRANT privileges.
"""
from __future__ import annotations

import secrets
import string

import pymysql

from shared.config import settings
from shared.validation import ValidationError, validate_db_identifier


class MariaDbError(Exception):
    pass


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
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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


def set_password(db_user: str, password: str, host: str = "localhost") -> None:
    validate_db_identifier(db_user)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER USER '{db_user}'@'{host}' IDENTIFIED BY %s", (password,))
    finally:
        conn.close()


def drop_db_user(db_user: str, host: str = "localhost") -> None:
    validate_db_identifier(db_user)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP USER IF EXISTS '{db_user}'@'{host}'")
    finally:
        conn.close()


# Explicit privilege set granted to a hosted account's own DB user --
# deliberately enumerated rather than using the "ALL PRIVILEGES" keyword.
# MariaDB requires the granting user to itself hold every privilege it
# grants, so this list is scoped to exactly what forgehost_daemon's setup
# grant provides (CREATE/DROP/ALTER/INDEX/REFERENCES + standard DML) --
# covers ordinary CRUD/DDL use (WordPress and most PHP CMSs need nothing
# more). It deliberately excludes CREATE VIEW/TRIGGER/EVENT/ROUTINE/EXECUTE:
# granting those to forgehost_daemon itself was flagged by this
# environment's permission classifier as a global-privilege expansion
# requiring explicit operator authorization, since the project goal never
# asked for that. Rather than work around the block, the hosted-account
# privilege surface was scoped down to match what was already safely
# grantable -- documented in CHECKPOINT-d.md as a deliberate v1 limitation,
# not an oversight. An operator who wants stored-procedure/trigger/view
# support for hosted databases can grant forgehost_daemon those specific
# privileges themselves and widen this constant to match.
HOSTED_DB_PRIVILEGES = (
    "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES, "
    "CREATE TEMPORARY TABLES, LOCK TABLES"
)


def grant_all(db_name: str, db_user: str, host: str = "localhost") -> None:
    db_ident = _quote_ident(db_name)
    validate_db_identifier(db_user)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"GRANT {HOSTED_DB_PRIVILEGES} ON {db_ident}.* TO '{db_user}'@'{host}'")
            cur.execute("FLUSH PRIVILEGES")
    finally:
        conn.close()


def revoke_all(db_name: str, db_user: str, host: str = "localhost") -> None:
    db_ident = _quote_ident(db_name)
    validate_db_identifier(db_user)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"REVOKE ALL PRIVILEGES ON {db_ident}.* FROM '{db_user}'@'{host}'")
            cur.execute("FLUSH PRIVILEGES")
    finally:
        conn.close()
