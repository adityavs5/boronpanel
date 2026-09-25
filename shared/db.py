from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from shared.config import settings
from shared.models import Base


def make_engine(db_path: str | None = None, read_only: bool = False):
    path = db_path or settings.db_path
    if not read_only:
        # boron-api (the read_only=True caller) must never create this
        # file/directory -- only borond, running as root, owns that.
        # mkdir here unconditionally would have silently given boron-api
        # write access to the parent dir the first time it ran before the
        # daemon had, which defeats the whole point of the OS-level
        # permission split below.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # SQLAlchemy's create_engine() takes a SQLAlchemy URL, not a raw sqlite3
    # "file:...?mode=ro" URI passed as connect_args={"uri": True} -- that
    # combination doesn't parse (caught by real testing: the first time
    # boron-api actually exercised this read-only path, not by
    # reasoning about it). Read-only enforcement instead comes from the
    # actual filesystem permissions (the DB file is 0640 root:boron-api
    # -- ARCHITECTURE.md SS4), which is simpler and was already the
    # documented design; SQLite raises "attempt to write a readonly
    # database" if this process ever tried to write through a connection
    # that genuinely can't open the file for writing at the OS level.
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        if not read_only:
            # WAL mode is a property of the file itself, set once by
            # whichever connection creates/opens it for writing -- forcing
            # it again on a read-only connection would itself require a
            # write to the DB header, which the read-only OS permissions
            # correctly refuse. The writer (borond) always connects
            # first in practice, but don't depend on ordering: only the
            # writer ever issues this pragma.
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


_write_engine = None
_WriteSession: sessionmaker | None = None


def init_db() -> None:
    """Create tables. Only the daemon (writer) should call this."""
    global _write_engine, _WriteSession
    _write_engine = make_engine()
    # sqlite3's legacy transaction mode does not begin a transaction for DDL.
    # Explicitly group schema creation so every table/index does not incur its
    # own durable commit, and a failed creation cannot leave a partial schema.
    with _write_engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        Base.metadata.create_all(connection)
    _apply_additive_migrations(_write_engine)
    _migrate_session_identifiers(_write_engine)
    _WriteSession = sessionmaker(bind=_write_engine, future=True, expire_on_commit=False)
    _grant_api_group_read()


# create_all() creates whole tables but never ALTERs an existing one (the
# project's stated schema convention -- new state normally goes in NEW
# tables). The rare exception is an additive, nullable column on a table that
# predates it: cloudflare_zones shipped in Phase 1, and Phase 2+3 links each
# zone to a CloudflareAccount pool row via cloudflare_zones.cf_account_id.
# SQLite's ADD COLUMN is a cheap metadata-only operation and is safe here
# (the column is nullable, the table is empty until a zone is enabled). This
# helper stays tiny and strictly additive -- it only ever ADDs a declared
# missing column, never drops/renames/retypes anything.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "totp_credentials": {"last_used_step": "INTEGER"},
    "impersonation_sessions": {"admin_session_enc": "VARCHAR(512)"},
    "branding_settings": {"terminal_banner": "TEXT"},
    "cloudflare_zones": {"cf_account_id": "INTEGER", "last_purge_at": "DATETIME"},
    # Run A feature 1 (plan templates): both tables predate the Plan model.
    "accounts": {"plan_id": "INTEGER"},
    "account_resource_limits": {"ftp_account_limit": "INTEGER", "app_limit": "INTEGER"},
    # QA round 2, item 3: WordPress multi-install + subdirectory support --
    # see _migrate_wordpress_installs_uniqueness below for the accompanying
    # index change this column enables.
    "wordpress_installs": {"path": "VARCHAR(255) NOT NULL DEFAULT ''"},
    "domains": {
        "suspended": "BOOLEAN NOT NULL DEFAULT 0",
        "ols_log_level": "VARCHAR(16) NOT NULL DEFAULT 'WARN'",
        "ols_log_previous_level": "VARCHAR(16)",
        "ols_log_debug_until": "DATETIME",
    },
    "snapshot_destinations": {
        "enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "customer_visible": "BOOLEAN NOT NULL DEFAULT 1",
        "capabilities": "JSON NOT NULL DEFAULT '{}'",
        "last_verified_at": "DATETIME",
        "last_speed_bps": "INTEGER",
    },
    "snapshot_runs": {"cancel_requested": "BOOLEAN NOT NULL DEFAULT 0"},
    "waf_settings": {
        "mode": "VARCHAR(16) NOT NULL DEFAULT 'disabled'",
        "paranoia_level": "INTEGER NOT NULL DEFAULT 1",
        "anomaly_threshold": "INTEGER NOT NULL DEFAULT 5",
        "wp_login_limit": "INTEGER NOT NULL DEFAULT 10",
        "wp_xmlrpc_limit": "INTEGER NOT NULL DEFAULT 5",
        "wp_rate_window_seconds": "INTEGER NOT NULL DEFAULT 60",
    },
    "waf_domain_overrides": {"mode": "VARCHAR(16) NOT NULL DEFAULT 'disabled'"},
    # Unified external-account importer. Existing rows are cPanel jobs.
    "cpanel_import_jobs": {
        "panel": "VARCHAR(16) NOT NULL DEFAULT 'cpanel'",
        "initial_password": "VARCHAR(128)",
    },
}


def _migrate_session_identifiers(engine) -> None:
    """Rehash legacy raw IDs atomically before the API can read the DB.

    A restart finds only digests and does nothing. Impersonation's original
    admin cookie is encrypted with borond's root-only key before plaintext is
    cleared. The migration never falls back to raw lookup after this point.
    """
    from sqlalchemy import text

    from shared.session_ids import is_session_digest, session_digest

    with engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        for row_id, value in conn.execute(text("SELECT id, session_id FROM sessions")):
            if not is_session_digest(value):
                conn.execute(text("UPDATE sessions SET session_id=:digest WHERE id=:id"),
                             {"digest": session_digest(value), "id": row_id})
        for row_id, value, raw_admin, encrypted_admin in conn.execute(text(
            "SELECT id, session_id, admin_session_id, admin_session_enc FROM impersonation_sessions"
        )):
            updates = {}
            if not is_session_digest(value):
                updates["digest"] = session_digest(value)
            if raw_admin is not None:
                from daemon.appcrypto import encrypt_secret

                updates["encrypted"] = encrypt_secret(raw_admin)
            if updates:
                conn.execute(text("UPDATE impersonation_sessions SET "
                                  "session_id=COALESCE(:digest, session_id), "
                                  "admin_session_id=NULL, "
                                  "admin_session_enc=COALESCE(:encrypted, admin_session_enc) WHERE id=:id"),
                             {"digest": updates.get("digest"), "encrypted": updates.get("encrypted"), "id": row_id})


def _apply_additive_migrations(engine) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all just made it with every column already
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, coltype in columns.items():
                if name not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {coltype}'))
    _migrate_wordpress_installs_uniqueness(engine)


def _migrate_wordpress_installs_uniqueness(engine) -> None:
    """QA round 2, item 3: WordPressInstall used to allow exactly one row
    per domain, enforced by a UNIQUE INDEX on `domain` alone
    (`ix_wordpress_installs_domain`). Multi-install + subdirectory support
    needs uniqueness on (domain, path) instead. SQLite can't ALTER a
    constraint in place, but this project's schema convention (§ above)
    already established that this particular uniqueness was created as a
    separate INDEX, not baked into the table's own DDL -- confirmed by
    inspecting the actual generated schema -- so it's droppable and
    replaceable without a full table rebuild, unlike a genuine inline
    UNIQUE column constraint would be.

    A brand-new install never touches this function's DROP/CREATE branch:
    create_all() already emits the current model's schema (plain index on
    domain, composite unique on (domain, path)) directly for a table that
    doesn't exist yet. Idempotent and safe to call every startup."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "wordpress_installs" not in inspector.get_table_names():
        return  # create_all() will make (or already made) the current schema

    existing = {ix["name"]: ix for ix in inspector.get_indexes("wordpress_installs")}
    old_unique = existing.get("ix_wordpress_installs_domain")
    with engine.begin() as conn:
        if old_unique is not None and old_unique.get("unique"):
            conn.execute(text("DROP INDEX ix_wordpress_installs_domain"))
            conn.execute(text("CREATE INDEX ix_wordpress_installs_domain ON wordpress_installs (domain)"))
        if "uq_wordpress_installs_domain_path" not in existing:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_wordpress_installs_domain_path "
                    "ON wordpress_installs (domain, path)"
                )
            )


def _grant_api_group_read() -> None:
    """boron-api needs group-read on the DB (+ -wal/-shm, WAL mode
    creates both) to open it via read_session() -- a fresh install's first
    `CREATE TABLE` otherwise leaves these owned root:root from the
    process's own umask, and boron-api gets a bare PermissionError with
    no obvious cause until someone manually chowns it (found while writing
    the install instructions for this project, not by code review)."""
    import grp
    import os

    if os.geteuid() != 0:
        return  # tests and other non-root callers leave ownership alone
    try:
        gid = grp.getgrnam("boron-api").gr_gid
    except KeyError:
        return
    base = Path(settings.db_path)
    for suffix in ("", "-wal", "-shm"):
        path = base.with_name(base.name + suffix)
        if path.exists():
            os.chown(path, 0, gid)
            os.chmod(path, 0o640)
    if base.parent.exists():
        os.chown(base.parent, 0, gid)
        os.chmod(base.parent, 0o750)


@contextmanager
def write_session():
    if _WriteSession is None:
        init_db()
    assert _WriteSession is not None
    session: Session = _WriteSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_read_engine = None
_ReadSession: sessionmaker | None = None


@contextmanager
def read_session():
    """Read-only session for boron-api. Opens the same SQLite file
    without requiring write permission on it (ARCHITECTURE.md SS4)."""
    global _read_engine, _ReadSession
    if _ReadSession is None:
        _read_engine = make_engine(read_only=True)
        _ReadSession = sessionmaker(bind=_read_engine, future=True, expire_on_commit=False)
    session: Session = _ReadSession()
    try:
        yield session
    finally:
        session.close()
