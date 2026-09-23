"""Protect one-time installer credentials from read-only database consumers."""
from sqlalchemy import select

from daemon import appcrypto
from shared.db import write_session
from shared.models import AppInstallJob, WordPressJob, CommandRun

PREFIX = 'encrypted:v1:'


def seal(value: str | None) -> str | None:
    return None if value is None else PREFIX + appcrypto.encrypt_secret(value)


def reveal(value: str | None) -> str | None:
    if value is None:
        return None
    # Legacy rows are consumed once or encrypted by the root startup migration.
    return appcrypto.decrypt_secret(value[len(PREFIX):]) if value.startswith(PREFIX) else value


def migrate() -> int:
    """Run as the root daemon before accepting requests; never expose values."""
    changed = 0
    with write_session() as session:
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        for model, field in ((WordPressJob, "admin_password"), (AppInstallJob, "admin_password"),
                             (CommandRun, "revealed_secret")):
            for row in session.scalars(select(model).where(getattr(model, field).is_not(None))):
                if not getattr(row, field).startswith(PREFIX):
                    setattr(row, field, seal(getattr(row, field)))
                    changed += 1
    return changed
