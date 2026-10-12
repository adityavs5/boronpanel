from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from shared.migrations import (
    CURRENT_SCHEMA_VERSION,
    SchemaCompatibilityError,
    apply_schema_migrations,
    rollback_compatibility,
    schema_state_from_engine,
)


def test_existing_database_is_baselined_upgraded_and_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE dns_cluster_peers (id INTEGER PRIMARY KEY)"))

    first = apply_schema_migrations(engine)
    second = apply_schema_migrations(engine)

    assert first["schema_version"] == CURRENT_SCHEMA_VERSION
    assert second["schema_version"] == CURRENT_SCHEMA_VERSION
    assert [row["revision"] for row in second["revisions"]] == list(range(CURRENT_SCHEMA_VERSION + 1))
    assert second["revisions"][1]["compatibility"] == "additive"
    assert second["app_version"]
    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(dns_cluster_peers)"
            )
        }
    assert {"protocol_version", "capabilities", "last_capability_check_at"} <= columns


def test_release_schema_manifest_matches_registry():
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "schema-compat.json").read_text()
    )
    assert manifest["schema_version"] == CURRENT_SCHEMA_VERSION
    assert manifest["maximum_supported_schema"] == CURRENT_SCHEMA_VERSION
    assert manifest["minimum_supported_schema"] <= CURRENT_SCHEMA_VERSION


def test_newer_database_revision_is_rejected_before_changes(tmp_path):
    path = tmp_path / "future.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    apply_schema_migrations(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO schema_migrations"
            "(revision, name, compatibility, checksum, app_version, applied_at) "
            "VALUES (99, 'future', 'breaking', 'x', '99.0.0', '2099-01-01T00:00:00Z')"
        ))
    with pytest.raises(SchemaCompatibilityError, match="newer than this panel supports"):
        apply_schema_migrations(engine)


def test_rollback_allows_unknown_additive_revision_but_refuses_breaking(tmp_path):
    db_path = tmp_path / "panel.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    apply_schema_migrations(engine)
    legacy_release = tmp_path / "legacy-release"
    legacy_release.mkdir()

    allowed = rollback_compatibility(legacy_release, str(db_path))
    assert allowed["compatible"] is True
    assert "additive" in allowed["reason"]

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO schema_migrations"
            "(revision, name, compatibility, checksum, app_version, applied_at) "
            f"VALUES ({CURRENT_SCHEMA_VERSION + 1}, 'contract_old_column', 'breaking', 'x', '4.0.0', '2099-01-01T00:00:00Z')"
        ))
        connection.execute(text(
            "UPDATE control_plane_metadata "
            f"SET value='{CURRENT_SCHEMA_VERSION + 1}' WHERE key='schema_version'"
        ))
    refused = rollback_compatibility(legacy_release, str(db_path))
    assert refused["compatible"] is False
    assert "breaking revision" in refused["reason"]


def test_previous_release_code_starts_against_additively_upgraded_fixture(
    tmp_path, monkeypatch
):
    """Use the actual Git HEAD tree as N-1, rather than a simulated reader."""
    from shared.config import settings
    from testsupport.seeded_panel import seed_panel_database

    db_path = tmp_path / "seeded.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    seed_panel_database(
        db_path,
        admin_password=os.urandom(24).hex(),
        customer_password=os.urandom(24).hex(),
    )
    assert schema_state_from_engine(create_engine(f"sqlite:///{db_path}", future=True))["schema_version"] == CURRENT_SCHEMA_VERSION

    source_tree = Path(os.environ.get("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[1])
    if not (source_tree / ".git").exists():
        pytest.skip("source checkout with Git history is required for the previous-release probe")
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=source_tree,
        check=True,
        capture_output=True,
    ).stdout
    old_tree = tmp_path / "previous-release"
    old_tree.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(old_tree, filter="data")

    config = tmp_path / "previous.toml"
    config.write_text(f'db_path = {json.dumps(str(db_path))}\n')
    probe = (
        "import shared.db as db_module; "
        "db_module._grant_api_group_read=lambda: None; "
        "from shared.db import init_db, write_session; "
        "from shared.models import Account; "
        "init_db(); "
        "ctx=write_session(); db=ctx.__enter__(); "
        "row=db.query(Account).filter_by(username='ciaccount').one(); "
        "assert row.primary_domain == 'ciaccount.example.test'; "
        "ctx.__exit__(None,None,None)"
    )
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(old_tree),
        "BORON_CONFIG": str(config),
        "BORON_SECRETS": str(tmp_path / "missing-root-secrets"),
        "BORON_API_SECRETS": str(tmp_path / "missing-api-secrets"),
    })
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=old_tree,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
