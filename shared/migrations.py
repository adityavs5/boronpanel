"""Ordered, ledger-backed control-plane schema migrations.

The project deliberately keeps migrations dependency-free: Boron's updater
must be able to run a staged release before the live symlink moves, and the
only supported database is SQLite.  Each revision is explicit, ordered and
classified.  Revisions above a rollback target's declared maximum may be
ignored only when every one is additive.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from sqlalchemy import inspect, text

from version import BORON_VERSION

Compatibility = Literal["additive", "breaking"]
BASELINE_REVISION = 0
CURRENT_SCHEMA_VERSION = 5
SCHEMA_MANIFEST = "schema-compat.json"


class SchemaCompatibilityError(RuntimeError):
    """The database and a code release cannot safely run together."""


@dataclass(frozen=True)
class Revision:
    revision: int
    name: str
    compatibility: Compatibility
    upgrade: Callable

    @property
    def checksum(self) -> str:
        material = f"{self.revision}:{self.name}:{self.compatibility}".encode()
        return hashlib.sha256(material).hexdigest()


def _utcnow_text() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_ledger(conn) -> None:
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            revision INTEGER PRIMARY KEY,
            name VARCHAR(128) NOT NULL UNIQUE,
            compatibility VARCHAR(16) NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            app_version VARCHAR(32) NOT NULL,
            applied_at DATETIME NOT NULL,
            CHECK (compatibility IN ('additive', 'breaking'))
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS control_plane_metadata (
            key VARCHAR(64) PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """
    )


def _metadata_set(conn, key: str, value: str) -> None:
    conn.execute(
        text(
            "INSERT INTO control_plane_metadata(key, value, updated_at) "
            "VALUES (:key, :value, :updated_at) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
        ),
        {"key": key, "value": value, "updated_at": _utcnow_text()},
    )


def _upgrade_legacy_additive_schema(conn) -> None:
    """Adopt all pre-ledger additive changes as revision 1.

    Imports are intentionally local: ``shared.db`` invokes this module after
    it has finished importing.  Keeping the old declarations in one place
    also lets previous internal callers use the compatibility wrapper while
    the startup path is fully ledger-driven.
    """
    from shared.db import _ADDITIVE_COLUMNS, _ADDITIVE_EXTRA_INDEXES, _ADDITIVE_INDEXES

    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table, columns in _ADDITIVE_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {column["name"] for column in inspect(conn).get_columns(table)}
        for name, column_type in columns.items():
            if name not in present:
                # Table and column identifiers come only from the static,
                # reviewed declarations above; no request/config value can
                # enter this DDL string.
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {column_type}'))
                present.add(name)
    for table, index_name in _ADDITIVE_INDEXES.items():
        if table in existing_tables and "request_id" in {
            column["name"] for column in inspect(conn).get_columns(table)
        }:
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}"(request_id)'))
    for table, indexes in _ADDITIVE_EXTRA_INDEXES.items():
        if table not in existing_tables:
            continue
        present = {column["name"] for column in inspect(conn).get_columns(table)}
        for required_columns, statement in indexes.values():
            if required_columns <= present:
                conn.execute(text(statement))

    if "wordpress_installs" not in existing_tables:
        return
    indexes = {item["name"]: item for item in inspect(conn).get_indexes("wordpress_installs")}
    old_unique = indexes.get("ix_wordpress_installs_domain")
    if old_unique is not None and old_unique.get("unique"):
        conn.execute(text("DROP INDEX ix_wordpress_installs_domain"))
        conn.execute(text("CREATE INDEX ix_wordpress_installs_domain ON wordpress_installs (domain)"))
    if "uq_wordpress_installs_domain_path" not in indexes:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_wordpress_installs_domain_path "
                "ON wordpress_installs (domain, path)"
            )
        )


def _upgrade_update_safety_schema(conn) -> None:
    """Add durable P6 update evidence without contracting the N-1 schema."""
    job_columns = {
        "release_metadata": "JSON NOT NULL DEFAULT '{}'",
        "preflight_report": "JSON NOT NULL DEFAULT '{}'",
        "advisories": "JSON NOT NULL DEFAULT '[]'",
        "runtime_changes": "JSON NOT NULL DEFAULT '[]'",
        "acknowledged_data_plane": "JSON NOT NULL DEFAULT '[]'",
        "override_active_tasks": "BOOLEAN NOT NULL DEFAULT 0",
        "automatic_rollback_attempted": "BOOLEAN NOT NULL DEFAULT 0",
        "backup_path": "VARCHAR(512)",
        "finalizer_token_hash": "VARCHAR(64)",
    }
    state_columns = {
        "release_metadata": "JSON NOT NULL DEFAULT '{}'",
        "release_asset_bytes": "INTEGER",
    }
    tables = set(inspect(conn).get_table_names())
    for table, columns in (("update_jobs", job_columns), ("update_state", state_columns)):
        if table not in tables:
            continue
        present = {item["name"] for item in inspect(conn).get_columns(table)}
        for name, declaration in columns.items():
            if name not in present:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}'))


def _upgrade_control_tasks_schema(conn) -> None:
    """Add the durable P7 control-task queue without changing older tables."""
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS control_tasks (
            id INTEGER PRIMARY KEY,
            request_id VARCHAR(64),
            operation VARCHAR(48) NOT NULL,
            account_id INTEGER REFERENCES accounts(id),
            username VARCHAR(64),
            status VARCHAR(16) NOT NULL DEFAULT 'queued',
            progress_pct INTEGER NOT NULL DEFAULT 0,
            progress_message VARCHAR(256) NOT NULL DEFAULT 'Queued',
            safe_params JSON NOT NULL DEFAULT '{}',
            secret_params_enc TEXT,
            result JSON NOT NULL DEFAULT '{}',
            one_time_secret_enc TEXT,
            error_reference VARCHAR(32),
            initiated_by VARCHAR(64) NOT NULL,
            initiated_role VARCHAR(16) NOT NULL,
            source_ip VARCHAR(45),
            created_at DATETIME NOT NULL,
            started_at DATETIME,
            completed_at DATETIME
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_control_tasks_request_id ON control_tasks(request_id)",
        "CREATE INDEX IF NOT EXISTS ix_control_tasks_operation ON control_tasks(operation)",
        "CREATE INDEX IF NOT EXISTS ix_control_tasks_account_id ON control_tasks(account_id)",
        "CREATE INDEX IF NOT EXISTS ix_control_tasks_username ON control_tasks(username)",
        "CREATE INDEX IF NOT EXISTS ix_control_tasks_status ON control_tasks(status)",
        "CREATE INDEX IF NOT EXISTS ix_control_tasks_error_reference ON control_tasks(error_reference)",
        "CREATE INDEX IF NOT EXISTS ix_control_tasks_created_at ON control_tasks(created_at)",
    ):
        conn.exec_driver_sql(statement)


def _upgrade_retention_alerts_sessions_schema(conn) -> None:
    """Add P8 retention, alert-policy and session-observability state."""
    tables = set(inspect(conn).get_table_names())
    if "sessions" in tables:
        present = {item["name"] for item in inspect(conn).get_columns("sessions")}
        for name, declaration in {
            "ip_address": "VARCHAR(45)",
            "user_agent": "VARCHAR(256)",
            "last_seen_at": "DATETIME",
        }.items():
            if name not in present:
                conn.exec_driver_sql(f'ALTER TABLE "sessions" ADD COLUMN "{name}" {declaration}')
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_sessions_last_seen_at ON sessions(last_seen_at)"
        )
    if "monitoring_settings" in tables:
        present = {item["name"] for item in inspect(conn).get_columns("monitoring_settings")}
        for name, declaration in {
            "channels": "JSON NOT NULL DEFAULT '[\"email\"]'",
            "disk_free_pct": "INTEGER NOT NULL DEFAULT 10",
            "inode_free_pct": "INTEGER NOT NULL DEFAULT 10",
            "memory_used_pct": "INTEGER NOT NULL DEFAULT 95",
            "load_per_cpu": "FLOAT NOT NULL DEFAULT 2.0",
            "failed_login_threshold": "INTEGER NOT NULL DEFAULT 20",
            "failed_login_window_minutes": "INTEGER NOT NULL DEFAULT 10",
        }.items():
            if name not in present:
                conn.exec_driver_sql(
                    f'ALTER TABLE "monitoring_settings" ADD COLUMN "{name}" {declaration}'
                )
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS login_failure_events (
            id INTEGER PRIMARY KEY,
            bucket VARCHAR(80) NOT NULL,
            source_ip VARCHAR(45),
            occurred_at DATETIME NOT NULL
        )
        """
    )
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_login_failure_events_bucket ON login_failure_events(bucket)")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_login_failure_events_occurred_at ON login_failure_events(occurred_at)")
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS usage_monthly_aggregates (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            month VARCHAR(7) NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            disk_peak_bytes INTEGER NOT NULL DEFAULT 0,
            inode_peak INTEGER NOT NULL DEFAULT 0,
            process_peak INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            CONSTRAINT uq_usage_monthly_account UNIQUE(account_id, month)
        )
        """
    )
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_usage_monthly_aggregates_account_id ON usage_monthly_aggregates(account_id)")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_usage_monthly_aggregates_month ON usage_monthly_aggregates(month)")
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS retention_settings (
            id INTEGER PRIMARY KEY,
            audit_days INTEGER NOT NULL DEFAULT 400,
            action_step_days INTEGER NOT NULL DEFAULT 90,
            task_output_days INTEGER NOT NULL DEFAULT 90,
            usage_raw_days INTEGER NOT NULL DEFAULT 90,
            usage_monthly_days INTEGER NOT NULL DEFAULT 395,
            bandwidth_days INTEGER NOT NULL DEFAULT 400,
            daemon_log_days INTEGER NOT NULL DEFAULT 30,
            support_bundle_days INTEGER NOT NULL DEFAULT 7,
            investigation_hold BOOLEAN NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS security_policy_settings (
            id INTEGER PRIMARY KEY,
            require_admin_2fa BOOLEAN NOT NULL DEFAULT 0,
            admin_2fa_grace_days INTEGER NOT NULL DEFAULT 7,
            admin_2fa_enabled_at DATETIME,
            updated_at DATETIME NOT NULL
        )
        """
    )


def _upgrade_php_lifecycle_isolation_schema(conn) -> None:
    """Add P10 state without changing the meaning of existing rows."""
    tables = set(inspect(conn).get_table_names())
    for table, columns in (
        ("accounts", {
            "inode_soft_limit": "INTEGER NOT NULL DEFAULT 0",
            "inode_hard_limit": "INTEGER NOT NULL DEFAULT 0",
            "inode_enforce_at": "DATETIME",
            "php_private_sessions": "BOOLEAN NOT NULL DEFAULT 0",
        }),
        ("plans", {
            "inode_soft_limit": "INTEGER NOT NULL DEFAULT 450000",
            "inode_hard_limit": "INTEGER NOT NULL DEFAULT 500000",
        }),
        ("waf_settings", {
            "detect_started_at": "DATETIME",
            "detect_review_due_at": "DATETIME",
            "detect_reviewed_at": "DATETIME",
        }),
    ):
        if table not in tables:
            continue
        present = {item["name"] for item in inspect(conn).get_columns(table)}
        for name, declaration in columns.items():
            if name not in present:
                conn.exec_driver_sql(
                    f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}'
                )
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS php_lifecycle_settings (
            id INTEGER PRIMARY KEY,
            hide_eol_for_new_domains_after VARCHAR(10),
            updated_at DATETIME NOT NULL
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS isolation_test_results (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            status VARCHAR(24) NOT NULL,
            checks JSON NOT NULL DEFAULT '[]',
            result JSON NOT NULL DEFAULT '{}',
            config_fingerprint VARCHAR(64) NOT NULL,
            tested_at DATETIME NOT NULL,
            CONSTRAINT uq_isolation_test_results_account_id UNIQUE(account_id)
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_isolation_test_results_account_id "
        "ON isolation_test_results(account_id)"
    )


REVISIONS: tuple[Revision, ...] = (
    Revision(1, "adopt_preledger_additive_schema", "additive", _upgrade_legacy_additive_schema),
    Revision(2, "update_safety_evidence", "additive", _upgrade_update_safety_schema),
    Revision(3, "control_tasks", "additive", _upgrade_control_tasks_schema),
    Revision(4, "retention_alerts_sessions", "additive", _upgrade_retention_alerts_sessions_schema),
    Revision(5, "php_lifecycle_isolation", "additive", _upgrade_php_lifecycle_isolation_schema),
)


def _known_revisions() -> dict[int, Revision]:
    revisions = {revision.revision: revision for revision in REVISIONS}
    if sorted(revisions) != list(range(1, CURRENT_SCHEMA_VERSION + 1)):
        raise RuntimeError("schema revisions must be unique and contiguous from 1")
    return revisions


def apply_schema_migrations(engine) -> dict:
    """Baseline an existing database and apply every pending revision.

    Each revision and its ledger row commit together.  Additive DDL is
    idempotent so an interpreter/process failure around SQLite DDL can be
    retried safely.  A database newer than this code is rejected before any
    migration runs.
    """
    revisions = _known_revisions()
    with engine.begin() as conn:
        _ensure_ledger(conn)
        applied_rows = conn.execute(
            text("SELECT revision, name, compatibility, checksum FROM schema_migrations ORDER BY revision")
        ).mappings().all()
        if not applied_rows:
            baseline_checksum = hashlib.sha256(b"0:baseline:additive").hexdigest()
            conn.execute(
                text(
                    "INSERT INTO schema_migrations"
                    "(revision, name, compatibility, checksum, app_version, applied_at) "
                    "VALUES (0, 'baseline', 'additive', :checksum, :app_version, :applied_at)"
                ),
                {"checksum": baseline_checksum, "app_version": BORON_VERSION, "applied_at": _utcnow_text()},
            )
            applied_rows = [{
                "revision": 0, "name": "baseline", "compatibility": "additive",
                "checksum": baseline_checksum,
            }]

        applied = {int(row["revision"]): row for row in applied_rows}
        if max(applied) > CURRENT_SCHEMA_VERSION:
            raise SchemaCompatibilityError(
                f"database schema v{max(applied)} is newer than this panel supports "
                f"(maximum v{CURRENT_SCHEMA_VERSION})"
            )
        for revision_number, row in applied.items():
            if revision_number == 0:
                continue
            expected = revisions.get(revision_number)
            if expected is None:
                raise SchemaCompatibilityError(f"database contains unknown schema revision {revision_number}")
            if (
                row["name"] != expected.name
                or row["compatibility"] != expected.compatibility
                or row["checksum"] != expected.checksum
            ):
                raise SchemaCompatibilityError(
                    f"schema revision {revision_number} does not match this release; restore matching code"
                )

    for revision in REVISIONS:
        if revision.revision in applied:
            continue
        with engine.begin() as conn:
            _ensure_ledger(conn)
            revision.upgrade(conn)
            conn.execute(
                text(
                    "INSERT INTO schema_migrations"
                    "(revision, name, compatibility, checksum, app_version, applied_at) "
                    "VALUES (:revision, :name, :compatibility, :checksum, :app_version, :applied_at)"
                ),
                {
                    "revision": revision.revision,
                    "name": revision.name,
                    "compatibility": revision.compatibility,
                    "checksum": revision.checksum,
                    "app_version": BORON_VERSION,
                    "applied_at": _utcnow_text(),
                },
            )
            _metadata_set(conn, "schema_version", str(revision.revision))
            _metadata_set(conn, "app_version", BORON_VERSION)
        applied[revision.revision] = {
            "revision": revision.revision,
            "name": revision.name,
            "compatibility": revision.compatibility,
            "checksum": revision.checksum,
        }

    with engine.begin() as conn:
        _ensure_ledger(conn)
        _metadata_set(conn, "schema_version", str(CURRENT_SCHEMA_VERSION))
        _metadata_set(conn, "app_version", BORON_VERSION)
    return schema_state_from_engine(engine)


def schema_state_from_engine(engine) -> dict:
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        if "schema_migrations" not in tables:
            return {"schema_version": 0, "app_version": None, "revisions": []}
        rows = conn.execute(
            text(
                "SELECT revision, name, compatibility, checksum, app_version, applied_at "
                "FROM schema_migrations ORDER BY revision"
            )
        ).mappings().all()
        metadata = {}
        if "control_plane_metadata" in tables:
            metadata = dict(conn.execute(text("SELECT key, value FROM control_plane_metadata")).all())
    return {
        "schema_version": int(metadata.get("schema_version", rows[-1]["revision"] if rows else 0)),
        "app_version": metadata.get("app_version"),
        "revisions": [dict(row) for row in rows],
    }


def schema_state_from_path(db_path: str) -> dict:
    path = Path(db_path)
    if not path.is_file():
        return {"schema_version": 0, "app_version": None, "revisions": []}
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "schema_migrations" not in tables:
            return {"schema_version": 0, "app_version": None, "revisions": []}
        rows = connection.execute(
            "SELECT revision, name, compatibility, checksum, app_version, applied_at "
            "FROM schema_migrations ORDER BY revision"
        ).fetchall()
        metadata = {}
        if "control_plane_metadata" in tables:
            metadata = dict(connection.execute("SELECT key, value FROM control_plane_metadata"))
    revisions = [
        {
            "revision": row[0], "name": row[1], "compatibility": row[2],
            "checksum": row[3], "app_version": row[4], "applied_at": row[5],
        }
        for row in rows
    ]
    return {
        "schema_version": int(metadata.get("schema_version", revisions[-1]["revision"] if revisions else 0)),
        "app_version": metadata.get("app_version"),
        "revisions": revisions,
    }


def load_schema_manifest(release_dir: str | Path) -> dict:
    path = Path(release_dir) / SCHEMA_MANIFEST
    if not path.is_file() or path.is_symlink():
        # Releases before the ledger are schema v0.  They can safely ignore
        # later additive tables/columns but never a breaking revision.
        return {"minimum": 0, "maximum": 0, "legacy": True}
    try:
        data = json.loads(path.read_text())
        minimum = int(data["minimum_supported_schema"])
        maximum = int(data["maximum_supported_schema"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaCompatibilityError(f"invalid schema compatibility manifest in {release_dir}") from exc
    if minimum < 0 or maximum < minimum:
        raise SchemaCompatibilityError(f"invalid schema compatibility range in {release_dir}")
    return {"minimum": minimum, "maximum": maximum, "legacy": False}


def rollback_compatibility(release_dir: str | Path, db_path: str) -> dict:
    manifest = load_schema_manifest(release_dir)
    state = schema_state_from_path(db_path)
    current = int(state["schema_version"])
    if current < manifest["minimum"]:
        return {
            "compatible": False,
            "reason": (
                f"target code requires schema v{manifest['minimum']} or newer, "
                f"but the database is v{current}"
            ),
            "schema_version": current,
            "target": manifest,
        }
    if current <= manifest["maximum"]:
        return {"compatible": True, "reason": "schema is within the target release range", "schema_version": current, "target": manifest}
    newer = [row for row in state["revisions"] if int(row["revision"]) > manifest["maximum"]]
    if newer and all(row["compatibility"] == "additive" for row in newer):
        return {
            "compatible": True,
            "reason": "newer database revisions are additive and safe for the target to ignore",
            "schema_version": current,
            "target": manifest,
        }
    return {
        "compatible": False,
        "reason": (
            f"database schema v{current} contains a breaking revision newer than "
            f"the target release maximum v{manifest['maximum']}"
        ),
        "schema_version": current,
        "target": manifest,
    }


def assert_rollback_compatible(release_dir: str | Path, db_path: str) -> dict:
    result = rollback_compatibility(release_dir, db_path)
    if not result["compatible"]:
        raise SchemaCompatibilityError(result["reason"])
    return result
