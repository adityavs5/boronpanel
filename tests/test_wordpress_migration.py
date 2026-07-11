"""QA round 2, item 3: WordPressInstall used to allow exactly one row per
domain (a UNIQUE INDEX on `domain` alone). Multi-install + subdirectory
support needs (domain, path) uniqueness instead. Verifies both the fresh-
install schema (create_all only) and the migration path for a DB that
already has the old single-domain-unique schema with real data in it.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect, select

from shared.db import _apply_additive_migrations, make_engine
from shared.models import Base, WordPressInstall


def _old_schema_ddl() -> str:
    """The exact pre-migration DDL (Phase 3 feature 2), reproduced by hand
    to simulate a real already-deployed database -- domain UNIQUE, no
    path column."""
    return """
    CREATE TABLE wordpress_installs (
        id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        domain VARCHAR(253) NOT NULL,
        db_name VARCHAR(64) NOT NULL,
        db_user VARCHAR(64) NOT NULL,
        wp_version VARCHAR(32) NOT NULL,
        admin_user VARCHAR(64) NOT NULL,
        installed_at DATETIME NOT NULL,
        PRIMARY KEY (id)
    );
    CREATE UNIQUE INDEX ix_wordpress_installs_domain ON wordpress_installs (domain);
    """


def test_fresh_install_schema_has_composite_unique_not_single_column(tmp_path):
    engine = make_engine(str(tmp_path / "fresh.db"))
    Base.metadata.create_all(engine)
    _apply_additive_migrations(engine)

    inspector = inspect(engine)
    indexes = {ix["name"]: ix for ix in inspector.get_indexes("wordpress_installs")}
    assert "uq_wordpress_installs_domain_path" in indexes
    assert indexes["uq_wordpress_installs_domain_path"]["unique"]
    assert set(indexes["uq_wordpress_installs_domain_path"]["column_names"]) == {"domain", "path"}
    # No stray single-column unique index left behind on a fresh install.
    assert not any(ix["unique"] and ix["column_names"] == ["domain"] for ix in indexes.values())
    columns = {c["name"] for c in inspector.get_columns("wordpress_installs")}
    assert "path" in columns


def test_migrates_a_preexisting_old_schema_database_with_real_data(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_old_schema_ddl())
    conn.execute(
        "INSERT INTO wordpress_installs (account_id, domain, db_name, db_user, wp_version, admin_user, installed_at) "
        "VALUES (1, 'demo1.example', 'demo1_wp', 'demo1_wp', '6.4', 'admin', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    engine = make_engine(str(db_path))
    Base.metadata.create_all(engine)  # no-op for the pre-existing table, matches production init_db()
    _apply_additive_migrations(engine)

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("wordpress_installs")}
    assert "path" in columns

    with engine.connect() as conn:
        row = conn.execute(select(WordPressInstall.domain, WordPressInstall.path)).fetchone()
    assert row.domain == "demo1.example"
    assert row.path == ""  # backfilled by the ALTER TABLE ... DEFAULT ''

    indexes = {ix["name"]: ix for ix in inspector.get_indexes("wordpress_installs")}
    assert "uq_wordpress_installs_domain_path" in indexes
    assert indexes["uq_wordpress_installs_domain_path"]["unique"]
    # The old single-domain unique index must be gone (replaced by a plain,
    # non-unique one of the same name for query performance).
    assert not indexes["ix_wordpress_installs_domain"]["unique"]


def test_migrated_database_now_allows_multiple_installs_per_domain_at_different_paths(tmp_path):
    db_path = tmp_path / "old2.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_old_schema_ddl())
    conn.commit()
    conn.close()

    engine = make_engine(str(db_path))
    Base.metadata.create_all(engine)
    _apply_additive_migrations(engine)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.add(WordPressInstall(
            account_id=1, domain="demo1.example", path="", db_name="demo1_wp", db_user="demo1_wp",
            wp_version="6.4", admin_user="admin",
        ))
        session.add(WordPressInstall(
            account_id=1, domain="demo1.example", path="blog", db_name="demo1_wp2", db_user="demo1_wp2",
            wp_version="6.4", admin_user="admin",
        ))
        session.commit()

        rows = session.scalars(select(WordPressInstall).where(WordPressInstall.domain == "demo1.example")).all()
        assert {r.path for r in rows} == {"", "blog"}


def test_migrated_database_still_rejects_duplicate_domain_and_path(tmp_path):
    db_path = tmp_path / "old3.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_old_schema_ddl())
    conn.commit()
    conn.close()

    engine = make_engine(str(db_path))
    Base.metadata.create_all(engine)
    _apply_additive_migrations(engine)

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.add(WordPressInstall(
            account_id=1, domain="demo1.example", path="blog", db_name="a", db_user="a",
            wp_version="6.4", admin_user="admin",
        ))
        session.commit()

    with Session(engine) as session:
        session.add(WordPressInstall(
            account_id=1, domain="demo1.example", path="blog", db_name="b", db_user="b",
            wp_version="6.4", admin_user="admin",
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "old4.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_old_schema_ddl())
    conn.commit()
    conn.close()

    engine = make_engine(str(db_path))
    Base.metadata.create_all(engine)
    _apply_additive_migrations(engine)
    _apply_additive_migrations(engine)  # must not raise the second time
    _apply_additive_migrations(engine)  # or the third

    inspector = inspect(engine)
    indexes = {ix["name"] for ix in inspector.get_indexes("wordpress_installs")}
    assert "uq_wordpress_installs_domain_path" in indexes
