"""A failed update must leave a database the previous release can open."""
import json
import sqlite3

import pytest
from sqlalchemy import create_engine, text

from scripts import upgrade_runtime
from shared.migrations import CURRENT_SCHEMA_VERSION, apply_schema_migrations, rollback_compatibility
from version import BORON_VERSION


def _strict_release(parent, version="3.3.1", maximum=5):
    release = parent / f"boron-{version}"
    release.mkdir()
    (release / "schema-compat.json").write_text(json.dumps({
        "schema_version": maximum, "minimum_supported_schema": 0,
        "maximum_supported_schema": maximum,
    }))
    return release


def _migrated_database(path):
    from shared.models import Base
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    apply_schema_migrations(engine)
    engine.dispose()


def _old_backup(path, maximum=5):
    _migrated_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE revision > ?", (maximum,))
        connection.execute("UPDATE control_plane_metadata SET value = ? WHERE key = 'schema_version'",
                           (str(maximum),))


def _job(path, backup_dir, steps_after=()):
    steps = [{"step": "backup", "status": "ok", "detail": str(backup_dir)}, *steps_after]
    from sqlalchemy.orm import Session
    from shared.models import UpdateJob
    engine = create_engine(f"sqlite:///{path}", future=True)
    with Session(engine) as session, session.begin():
        session.add(UpdateJob(kind="update", status="finalizing", from_version="3.3.1",
                              to_version=BORON_VERSION, initiated_by="admin", steps=steps))
    engine.dispose()


@pytest.fixture
def layout(tmp_path):
    releases = tmp_path / "opt"
    releases.mkdir()
    backups = tmp_path / "backups"
    backup_dir = backups / f"pre-update-{BORON_VERSION}-1"
    backup_dir.mkdir(parents=True)
    live = tmp_path / "boron.db"
    _old_backup(backup_dir / "boron.db")
    _job(backup_dir / "boron.db", backup_dir)
    _migrated_database(live)
    _job(live, backup_dir, [{"step": "swap", "status": "ok", "detail": "x"}])
    return {"releases": releases, "backups": backups, "backup_dir": backup_dir, "live": live}


def _restore(layout):
    return upgrade_runtime.restore_database_for_previous_release(
        str(layout["live"]), str(layout["backups"]), str(layout["releases"]))


def test_strict_previous_release_gets_the_pre_update_database_back(layout):
    _strict_release(layout["releases"])
    assert "restored" in _restore(layout)
    with sqlite3.connect(layout["live"]) as connection:
        assert connection.execute("SELECT max(revision) FROM schema_migrations").fetchone()[0] == 5
        steps = json.loads(connection.execute("SELECT steps FROM update_jobs").fetchone()[0])
    # The job keeps the progress made after the backup, plus the restore.
    assert [step["step"] for step in steps] == ["backup", "swap", "database-rollback"]
    kept = layout["backup_dir"] / "boron.db.after-failed-update"
    with sqlite3.connect(kept) as connection:
        assert connection.execute("SELECT max(revision) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_tolerant_previous_release_keeps_the_migrated_database(layout):
    release = _strict_release(layout["releases"])
    manifest = json.loads((release / "schema-compat.json").read_text())
    manifest["ignores_newer_additive"] = True
    (release / "schema-compat.json").write_text(json.dumps(manifest))
    assert _restore(layout) == "previous release can open the database"
    with sqlite3.connect(layout["live"]) as connection:
        assert connection.execute("SELECT max(revision) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_backup_outside_the_backup_root_is_refused(layout, tmp_path):
    _strict_release(layout["releases"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with sqlite3.connect(layout["live"]) as connection:
        connection.execute("UPDATE update_jobs SET steps = ?",
                           (json.dumps([{"step": "backup", "status": "ok", "detail": str(elsewhere)}]),))
    (elsewhere / "boron.db").write_bytes((layout["backup_dir"] / "boron.db").read_bytes())
    with pytest.raises(RuntimeError, match="not usable"):
        _restore(layout)


def test_strict_target_refuses_newer_additive_revisions(tmp_path):
    db = tmp_path / "panel.db"
    _migrated_database(db)
    release = _strict_release(tmp_path, maximum=CURRENT_SCHEMA_VERSION - 1)
    refused = rollback_compatibility(release, str(db))
    assert refused["compatible"] is False
    assert "pre-update database" in refused["reason"]


def test_this_release_starts_on_a_newer_additive_revision(tmp_path):
    db = tmp_path / "panel.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    apply_schema_migrations(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO schema_migrations"
            "(revision, name, compatibility, checksum, app_version, applied_at) "
            f"VALUES ({CURRENT_SCHEMA_VERSION + 1}, 'future', 'additive', 'x', '9.0.0', '2099-01-01T00:00:00Z')"
        ))
    apply_schema_migrations(engine)
