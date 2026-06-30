from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from shared.config import settings
from shared.models import Base


def make_engine(db_path: str | None = None, read_only: bool = False):
    path = db_path or settings.db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    uri = f"sqlite:///{path}"
    if read_only:
        uri = f"file:{path}?mode=ro"
        engine = create_engine(uri, connect_args={"uri": True}, future=True)
    else:
        engine = create_engine(uri, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
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
    _WriteSession = sessionmaker(bind=_write_engine, future=True, expire_on_commit=False)


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
