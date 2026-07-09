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
        # forgehost-api (the read_only=True caller) must never create this
        # file/directory -- only forgehostd, running as root, owns that.
        # mkdir here unconditionally would have silently given forgehost-api
        # write access to the parent dir the first time it ran before the
        # daemon had, which defeats the whole point of the OS-level
        # permission split below.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # SQLAlchemy's create_engine() takes a SQLAlchemy URL, not a raw sqlite3
    # "file:...?mode=ro" URI passed as connect_args={"uri": True} -- that
    # combination doesn't parse (caught by real testing: the first time
    # forgehost-api actually exercised this read-only path, not by
    # reasoning about it). Read-only enforcement instead comes from the
    # actual filesystem permissions (the DB file is 0640 root:forgehost-api
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
            # correctly refuse. The writer (forgehostd) always connects
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
    Base.metadata.create_all(_write_engine)
    _apply_additive_migrations(_write_engine)
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
    "cloudflare_zones": {"cf_account_id": "INTEGER", "last_purge_at": "DATETIME"},
}


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


def _grant_api_group_read() -> None:
    """forgehost-api needs group-read on the DB (+ -wal/-shm, WAL mode
    creates both) to open it via read_session() -- a fresh install's first
    `CREATE TABLE` otherwise leaves these owned root:root from the
    process's own umask, and forgehost-api gets a bare PermissionError with
    no obvious cause until someone manually chowns it (found while writing
    the install instructions for this project, not by code review)."""
    import grp
    import os

    if os.geteuid() != 0:
        return  # tests and other non-root callers leave ownership alone
    try:
        gid = grp.getgrnam("forgehost-api").gr_gid
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
    """Read-only session for forgehost-api. Opens the same SQLite file
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
