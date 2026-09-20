"""Replace legacy wildcard database grants with exact hosted DB grants.

Run once after upgrading from a version whose ``grant_all`` did not escape
underscores in MariaDB database grant patterns. An exact grant is installed
and verified before the old pattern is revoked, preserving access if the
reconciliation fails partway through. The command is idempotent.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import select

from daemon import mariadb
from shared.db import read_session
from shared.models import DatabaseGrant
from shared.validation import validate_db_identifier


def _db_patterns(cursor, user: str) -> set[str]:
    cursor.execute("SELECT Db FROM mysql.db WHERE User = %s AND Host = %s", (user, "localhost"))
    return {str(row[0]) for row in cursor.fetchall()}


def _backup_grants(cursor, grants: list[tuple[str, str]], path: Path) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite existing grant evidence: {path}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            for db_name, user in grants:
                cursor.execute("SHOW GRANTS FOR %s@%s", (user, "localhost"))
                output.write(json.dumps({"database": db_name, "user": user,
                                         "grants": [row[0] for row in cursor.fetchall()]}) + "\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise


def reconcile(*, apply: bool, evidence_path: Path | None = None) -> dict[str, int]:
    if apply and evidence_path is None:
        raise ValueError("apply mode requires a protected evidence path")
    with read_session() as session:
        grants = [(row.db_name, row.db_user) for row in session.scalars(select(DatabaseGrant)).all()]
    for database, user in grants:
        validate_db_identifier(database)
        validate_db_identifier(user)
    connection = mariadb._connect()
    legacy = repaired = 0
    try:
        with connection.cursor() as cursor:
            if apply:
                assert evidence_path is not None
                _backup_grants(cursor, grants, evidence_path)
            for database, user in grants:
                exact_pattern = database.replace("_", r"\_")
                patterns = _db_patterns(cursor, user)
                if database not in patterns:
                    continue
                legacy += 1
                if not apply:
                    continue
                # Install and verify the narrow grant before revoking the
                # broad pattern. A crash before revocation leaves access
                # unchanged; rerunning completes the migration.
                if exact_pattern not in patterns:
                    cursor.execute(
                        f"GRANT {mariadb.HOSTED_DB_PRIVILEGES} ON "
                        f"`{exact_pattern}`.* TO '{user}'@'localhost'"
                    )
                if exact_pattern not in _db_patterns(cursor, user):
                    raise RuntimeError("exact grant did not appear; refusing legacy revocation")
                # boron_daemon is deliberately not SUPER and cannot use
                # REVOKE ALL here; revoke the privilege set it originally
                # granted through mariadb.grant_all.
                cursor.execute(
                    f"REVOKE {mariadb.HOSTED_DB_PRIVILEGES} ON "
                    f"`{database}`.* FROM '{user}'@'localhost'"
                )
                patterns = _db_patterns(cursor, user)
                if database in patterns or exact_pattern not in patterns:
                    raise RuntimeError("grant reconciliation did not reach the expected state")
                repaired += 1
    finally:
        connection.close()
    return {"database_grants": len(grants), "legacy_grants": legacy, "repaired": repaired}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence-path", type=Path)
    args = parser.parse_args()
    print(json.dumps(reconcile(apply=args.apply, evidence_path=args.evidence_path)))


if __name__ == "__main__":
    main()
