import sqlite3

import pytest

from shared import db
from shared.models import Base


def test_schema_initialization_preserves_existing_data(isolated_db):
    with sqlite3.connect(isolated_db) as connection:
        connection.execute('CREATE TABLE preserved_marker (value TEXT)')
        connection.execute("INSERT INTO preserved_marker VALUES ('keep')")
    db.init_db()
    with sqlite3.connect(isolated_db) as connection:
        assert connection.execute('SELECT value FROM preserved_marker').fetchall() == [('keep',)]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(Base.metadata.tables) <= tables


def test_failed_schema_creation_rolls_back_ddl_and_writes(isolated_db, monkeypatch):
    with sqlite3.connect(isolated_db) as connection:
        connection.execute('CREATE TABLE preserved_marker (value TEXT)')
        connection.execute("INSERT INTO preserved_marker VALUES ('keep')")

    def interrupted_creation(connection):
        connection.exec_driver_sql('CREATE TABLE incomplete_schema (value TEXT)')
        connection.exec_driver_sql("UPDATE preserved_marker SET value='changed'")
        raise RuntimeError('schema creation interrupted')

    monkeypatch.setattr(Base.metadata, 'create_all', interrupted_creation)
    with pytest.raises(RuntimeError, match='schema creation interrupted'):
        db.init_db()
    with sqlite3.connect(isolated_db) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='incomplete_schema'").fetchall() == []
        assert connection.execute('SELECT value FROM preserved_marker').fetchall() == [('keep',)]
