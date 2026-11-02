"""SQL import applies only the dump and rolls back on failure (live 3.3.1).

Before: every import ran with replace_tables=True, which dropped every table
the dump did not mention, and a failed import left the database wiped while
retention deleted the only safety dump holding the original tables.
"""
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from daemon import handlers_database as hdb
from daemon import mutation_locks, snapshot_databases, snapshot_jobs
from shared.db import write_session
from shared.models import Account
from shared.validation import ConflictError, ValidationError


@pytest.fixture()
def importer(isolated_db, tmp_path, monkeypatch):
    with write_session() as session:
        account = Account(username="alpha", status="active")
        session.add(account)
        session.flush()
        account_id = account.id
    calls = []
    state = {"tables": {"keepme"}, "fail_import": False, "fail_rollback": False}
    upload = tmp_path / "upload.sql"
    upload.write_text("CREATE TABLE added (a INT);")

    monkeypatch.setattr(hdb, "_owned_database", lambda username, name: (account_id, 1, "alpha_blog"))
    monkeypatch.setattr(hdb, "_verified_import", lambda path: upload)
    monkeypatch.setattr(hdb, "_require_safety_capacity", lambda *args, **kwargs: None)

    def copy(source, work):
        target = work / "uploaded.sql"
        target.write_bytes(source.read_bytes())
        return target

    def dump(name, output, work, **kwargs):
        calls.append(("dump", name))
        # mariadb-dump's own last line; recovery copies without it are incomplete.
        Path(output).write_text("-- tables: " + ",".join(sorted(state["tables"])) + "\n-- Dump completed\n")

    def restore(name, dump_path, work, *, replace_tables=False):
        calls.append(("restore", Path(dump_path).name, replace_tables))
        if Path(dump_path).name == "uploaded.sql":
            if replace_tables:
                state["tables"].clear()
            state["tables"].add("partial")
            if state["fail_import"]:
                raise RuntimeError("Database restore failed for alpha_blog: ERROR 1064 (42000) at line 2: "
                                   "You have an error in your SQL syntax\nsecond line")
            state["tables"].add("added")
        else:
            if state["fail_rollback"]:
                raise RuntimeError("Database restore failed for alpha_blog: lost connection")
            state["tables"] = {"keepme"}
        return {"database": name, "bytes_read": 1}

    monkeypatch.setattr(hdb, "_copy_import_source", copy)
    monkeypatch.setattr(snapshot_databases, "dump_database", dump)
    monkeypatch.setattr(snapshot_databases, "restore_database", restore)
    return {"account_id": account_id, "calls": calls, "state": state}


def _safety_dirs(account_id):
    root = snapshot_jobs.private_directory(hdb.IMPORT_SAFETY_KIND)
    return sorted(path for path in root.iterdir() if path.name.startswith(f"account-{account_id}-"))


def test_successful_import_keeps_tables_the_dump_does_not_mention(importer):
    result = hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    assert result["status"] == "imported"
    assert importer["state"]["tables"] >= {"keepme", "added"}
    assert ("restore", "uploaded.sql", False) in importer["calls"]
    assert not any(call[0] == "restore" and call[2] for call in importer["calls"])
    assert _safety_dirs(importer["account_id"]) == []


def test_failed_import_is_rolled_back_and_reports_the_mariadb_error(importer):
    importer["state"]["fail_import"] = True
    with pytest.raises(ValidationError) as error:
        hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    message = str(error.value)
    assert "restored to its previous contents" in message
    assert "ERROR 1064 (42000) at line 2" in message and "second line" not in message
    assert importer["state"]["tables"] == {"keepme"}
    assert importer["calls"][-1] == ("restore", "alpha_blog.sql", True)
    # The database is back in its known-good state, so nothing is retained.
    assert _safety_dirs(importer["account_id"]) == []


def test_repeated_failed_rollbacks_never_delete_a_recovery_copy(importer):
    importer["state"]["fail_import"] = True
    importer["state"]["fail_rollback"] = True
    for _ in range(hdb.IMPORT_RECOVERY_MAX):
        with pytest.raises(RuntimeError, match="automatic rollback also failed"):
            hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    kept = _safety_dirs(importer["account_id"])
    assert len(kept) == hdb.IMPORT_RECOVERY_MAX
    for directory in kept:
        assert (directory / "alpha_blog.sql").exists()
        assert (directory / hdb.RECOVERY_MARKER).exists()
        assert not (directory / "uploaded.sql").exists()
    # The first copy (the original contents) must survive later attempts.
    dumps = [directory.joinpath("alpha_blog.sql").read_text() for directory in kept]
    assert "-- tables: keepme\n-- Dump completed\n" in dumps
    with pytest.raises(ConflictError, match="administrator"):
        hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    assert len(_safety_dirs(importer["account_id"])) == hdb.IMPORT_RECOVERY_MAX


def test_termination_purges_safety_copies(importer, monkeypatch):
    importer["state"]["fail_import"] = True
    importer["state"]["fail_rollback"] = True
    with pytest.raises(RuntimeError):
        hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    rename_copy = snapshot_jobs.private_directory(hdb.RENAME_SAFETY_KIND, f"account-{importer['account_id']}-abc")
    other_account = snapshot_jobs.private_directory(hdb.IMPORT_SAFETY_KIND, "account-999-abc")
    assert _safety_dirs(importer["account_id"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "alpha"))
        session.expunge(account)
    hdb.terminate_account_databases(account)
    assert _safety_dirs(importer["account_id"]) == []
    assert not rename_copy.exists()
    assert other_account.exists()


def test_termination_waits_for_a_running_database_operation(importer):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "alpha"))
        session.expunge(account)
    finished = threading.Event()
    errors = []

    def terminate():
        try:
            # account.terminate runs as a control task, which waits like a
            # background worker (bounded by BACKGROUND_WAIT_SECONDS).
            with mutation_locks.background_wait():
                hdb.terminate_account_databases(account)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finished.set()

    with snapshot_jobs.lock("database-mutations"):
        worker = threading.Thread(target=terminate)
        worker.start()
        time.sleep(0.3)
        # Live 3.3.1: the hook failed at once and left databases behind.
        assert not finished.is_set()
    worker.join(10)
    assert finished.is_set() and errors == []


def test_termination_wait_for_the_database_lock_is_bounded(importer, monkeypatch):
    """A stuck backup must not hang the termination task forever: the hook
    fails with BusyError and the account stays retryable."""
    from shared.validation import BusyError
    monkeypatch.setattr(mutation_locks, "BACKGROUND_WAIT_SECONDS", 0.3)
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "alpha"))
        session.expunge(account)
    errors = []

    def terminate():
        try:
            with mutation_locks.background_wait():
                hdb.terminate_account_databases(account)
        except Exception as exc:
            errors.append(exc)

    with snapshot_jobs.lock("database-mutations"):
        worker = threading.Thread(target=terminate)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], BusyError)
