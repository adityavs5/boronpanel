"""SQL import recovery copies can be listed, restored and released by an
administrator, and a rejected SQL statement is a caller error (400).

Before: after three imports whose automatic rollback failed, every import of
the account returned 409 until someone deleted the copies under
/var/lib/boron/snapshot-private by hand, and snapshot_databases reported a
rejected statement as an internal RuntimeError carrying the whole client
output (502).
"""
from contextlib import contextmanager
from pathlib import Path
import re

from fastapi import HTTPException
import pytest

from api.security import Identity
from daemon import handlers_account as ha
from daemon import handlers_database as hdb
from daemon import snapshot_databases, snapshot_jobs
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, DatabaseGrant
from shared.validation import ConflictError, NotFoundError, ValidationError
from tests.test_database_import_rollback import importer  # noqa: F401 - shared fixture
from tests.test_handlers_database import stub_mariadb, stub_sysops  # noqa: F401 - shared fixtures

COMPLETE_DUMP = "-- tables: keepme\n-- Dump completed\n"


@pytest.fixture()
def recovery(importer):  # noqa: F811
    with write_session() as session:
        session.add(DatabaseGrant(account_id=importer["account_id"], db_name="alpha_blog", db_user="alpha_blog"))
    return importer


def _fail_import_and_rollback(recovery):
    recovery["state"].update(fail_import=True, fail_rollback=True)
    with pytest.raises(RuntimeError, match="automatic rollback also failed"):
        hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    recovery["state"].update(fail_import=False, fail_rollback=False)


def _copies(username="alpha"):
    return hdb.list_import_recovery_copies({"username": username})


def _copy_directory(account_id, copy_id, files):
    path = snapshot_jobs.private_directory(hdb.IMPORT_SAFETY_KIND, f"account-{account_id}-{copy_id}")
    for name, content in files.items():
        (path / name).write_text(content)
    return path


def test_a_copy_whose_rollback_failed_is_listed_with_its_errors(recovery):
    _fail_import_and_rollback(recovery)
    listing = _copies()
    assert listing["limit"] == hdb.IMPORT_RECOVERY_MAX and listing["imports_blocked"] is False
    [copy] = listing["copies"]
    assert re.fullmatch(r"[0-9a-f]{32}", copy["id"])
    assert copy["origin"] == "rollback_failed" and copy["db_name"] == "alpha_blog"
    assert copy["restorable"] and copy["complete"] and copy["database_exists"] and copy["reason"] is None
    assert copy["import_error"].startswith("ERROR 1064 (42000) at line 2")
    assert "lost connection" in copy["rollback_error"]
    assert copy["size_bytes"] > 0 and copy["created_at"].endswith("+00:00")


def test_restore_replays_the_copy_and_keeps_what_it_replaced(recovery):
    _fail_import_and_rollback(recovery)
    # The failed import left a partial table behind.
    assert recovery["state"]["tables"] == {"keepme", "partial"}
    [copy] = _copies()["copies"]
    with pytest.raises(ValidationError, match="Confirm the restore"):
        hdb.restore_import_recovery_copy({"username": "alpha", "copy_id": copy["id"]})

    result = hdb.restore_import_recovery_copy({"username": "alpha", "copy_id": copy["id"], "confirm": True})

    assert result["status"] == "restored" and result["db_name"] == "alpha_blog"
    assert recovery["state"]["tables"] == {"keepme"}
    assert recovery["calls"][-1] == ("restore", "alpha_blog.sql", True)
    [previous] = _copies()["copies"]
    assert previous["id"] == result["previous_contents_copy_id"] != copy["id"]
    assert previous["origin"] == "before_restore" and previous["restorable"]
    assert result["remaining"] == 1 and result["imports_blocked"] is False
    assert previous["id"] in result["warning"]
    kept = snapshot_jobs.private_directory(hdb.IMPORT_SAFETY_KIND, f"account-{recovery['account_id']}-{previous['id']}")
    assert "partial" in (kept / "alpha_blog.sql").read_text()


def test_a_failed_restore_keeps_both_copies(recovery, monkeypatch):
    _fail_import_and_rollback(recovery)
    [copy] = _copies()["copies"]

    def rejecting(*_args, **_kwargs):
        raise snapshot_databases.SqlImportError("ERROR 1118 (42000) at line 9: Row size too large")

    monkeypatch.setattr(snapshot_databases, "restore_database", rejecting)
    with pytest.raises(ValidationError, match="Row size too large") as error:
        hdb.restore_import_recovery_copy({"username": "alpha", "copy_id": copy["id"], "confirm": True})
    assert not isinstance(error.value, ConflictError)
    ids = {item["id"] for item in _copies()["copies"]}
    assert copy["id"] in ids and len(ids) == 2
    [added] = ids - {copy["id"]}
    assert added in str(error.value)


def test_a_refused_restore_names_the_copy_it_added(recovery, monkeypatch):
    # A refusal before any statement runs (here a foreign key to another
    # database) still leaves the contents copy; the error must name it so the
    # administrator can release it instead of piling up copies on each retry.
    _fail_import_and_rollback(recovery)
    [copy] = _copies()["copies"]

    def refusing(*_args, **_kwargs):
        raise ValidationError("Database has foreign keys linked to another database; restore both "
                              "through a coordinated migration")

    monkeypatch.setattr(snapshot_databases, "restore_database", refusing)
    with pytest.raises(ValidationError, match="foreign keys linked to another database") as error:
        hdb.restore_import_recovery_copy({"username": "alpha", "copy_id": copy["id"], "confirm": True})
    [added] = {item["id"] for item in _copies()["copies"]} - {copy["id"]}
    assert f"(copy {added}) were both kept" in str(error.value)


def test_releasing_a_copy_unblocks_imports(recovery):
    for _ in range(hdb.IMPORT_RECOVERY_MAX):
        _fail_import_and_rollback(recovery)
    listing = _copies()
    assert listing["imports_blocked"] is True and len(listing["copies"]) == hdb.IMPORT_RECOVERY_MAX
    with pytest.raises(ConflictError, match="restore or release them"):
        hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    oldest = listing["copies"][-1]["id"]
    with pytest.raises(ValidationError, match="Confirm the release"):
        hdb.release_import_recovery_copy({"username": "alpha", "copy_id": oldest})

    result = hdb.release_import_recovery_copy({"username": "alpha", "copy_id": oldest, "confirm": True})

    assert result == {"status": "released", "copy_id": oldest, "remaining": 2, "imports_blocked": False}
    assert oldest not in {item["id"] for item in _copies()["copies"]}
    assert hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})["status"] == "imported"


def test_copy_ids_are_bound_to_their_account(recovery):
    _fail_import_and_rollback(recovery)
    [copy] = _copies()["copies"]
    with write_session() as session:
        session.add(Account(username="bravo", status="active"))
    with pytest.raises(ValidationError, match="Invalid recovery copy id"):
        hdb.release_import_recovery_copy({"username": "alpha", "copy_id": "../account-1-x", "confirm": True})
    with pytest.raises(NotFoundError):
        hdb.release_import_recovery_copy({"username": "bravo", "copy_id": copy["id"], "confirm": True})
    with pytest.raises(NotFoundError):
        hdb.restore_import_recovery_copy({"username": "bravo", "copy_id": copy["id"], "confirm": True})
    assert _copies("bravo")["copies"] == []
    with pytest.raises(NotFoundError):
        _copies("ghost")
    assert [item["id"] for item in _copies()["copies"]] == [copy["id"]]


def test_incomplete_or_orphaned_copies_are_listed_but_not_restorable(recovery):
    account_id = recovery["account_id"]
    # Copies from 3.3.1 (or an interrupted import) have no record; one was
    # cut short while its dump was being written, before the import began.
    _copy_directory(account_id, "a" * 32, {"alpha_blog.sql": "-- partial dump without its footer\n"})
    _copy_directory(account_id, "b" * 32, {"alpha_blog.sql": COMPLETE_DUMP, "uploaded.sql": "INSERT ...;\n"})
    _copy_directory(account_id, "c" * 32, {"alpha_gone.sql": COMPLETE_DUMP,
                                           hdb.RECOVERY_MARKER: "database=alpha_gone\n"})
    _copy_directory(account_id, "d" * 32, {"alpha_blog.sql": COMPLETE_DUMP,
                                           hdb.RECOVERY_MARKER: "database=alpha_other\n"})
    copies = {item["id"]: item for item in _copies()["copies"]}

    partial, legacy, orphan, mismatch = (copies[char * 32] for char in "abcd")
    assert partial["origin"] == "incomplete_import" and not partial["restorable"]
    assert not partial["complete"] and "incomplete" in partial["reason"]
    assert legacy["origin"] == "incomplete_import" and legacy["restorable"]
    assert not orphan["restorable"] and not orphan["database_exists"] and "no longer exists" in orphan["reason"]
    assert not mismatch["restorable"] and "different database" in mismatch["reason"]
    with pytest.raises(ConflictError, match="incomplete"):
        hdb.restore_import_recovery_copy({"username": "alpha", "copy_id": "a" * 32, "confirm": True})
    assert recovery["state"]["tables"] == {"keepme"}
    assert len(_copies()["copies"]) == 4


def test_recovery_records_stay_one_line_per_field(tmp_path):
    # Records written before values were flattened continue an error on the next line.
    (tmp_path / hdb.RECOVERY_MARKER).write_text(
        "database=alpha_blog\nimport_error=ERROR 1064 (42000) at line 2: bad\n"
        "rollback_error=Database restore failed for alpha_blog: ERROR 2013\nLost connection\n")
    assert hdb._read_recovery_marker(tmp_path) == {
        "database": "alpha_blog", "import_error": "ERROR 1064 (42000) at line 2: bad",
        "rollback_error": "Database restore failed for alpha_blog: ERROR 2013 Lost connection",
    }
    hdb._write_recovery_marker(tmp_path, origin="rollback_failed", rollback_error="first\nsecond")
    assert (tmp_path / hdb.RECOVERY_MARKER).read_text() == "origin=rollback_failed\nrollback_error=first second\n"


def test_recovery_operations_are_registered_for_administrators_only():
    from daemon import server
    from daemon.rpc_policy import POLICY_BY_OPERATION
    from shared.token_policy import READ_ONLY_OPERATIONS
    handlers = {
        "db.recovery.list": hdb.list_import_recovery_copies,
        "db.recovery.restore": hdb.restore_import_recovery_copy,
        "db.recovery.release": hdb.release_import_recovery_copy,
    }
    for operation, handler in handlers.items():
        assert POLICY_BY_OPERATION[operation] == "admin_account_username"
        assert server.OP_TABLE[operation] is handler
    # Listing is a read; restoring and releasing are audited mutations.
    assert "db.recovery.list" in READ_ONLY_OPERATIONS
    assert not {"db.recovery.restore", "db.recovery.release"} & READ_ONLY_OPERATIONS


@pytest.mark.usefixtures("immediate_asyncio_executor")
def test_release_through_the_daemon_is_audited_and_refused_to_the_customer(recovery, monkeypatch):
    import asyncio
    from sqlalchemy import select
    from daemon import server
    from daemon.rpc_authority import AuthorizationError, Principal
    from shared.models import AuditLog
    _fail_import_and_rollback(recovery)
    [copy] = _copies()["copies"]
    principals = {"admin": Principal("admin", "root", None, 1, "session"),
                  "customer": Principal("customer", "alpha-login", recovery["account_id"], 2, "session")}
    monkeypatch.setattr(server, "resolve_principal", lambda credential: principals[credential])
    monkeypatch.setattr(server.handlers_auth, "touch_session", lambda *_args: None)
    params = {"username": "alpha", "copy_id": copy["id"], "confirm": True}

    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("db.recovery.release", dict(params), "customer"))
    assert [item["id"] for item in _copies()["copies"]] == [copy["id"]]
    assert asyncio.run(server.dispatch("db.recovery.release", dict(params), "admin"))["status"] == "released"

    with write_session() as session:
        rows = session.scalars(select(AuditLog).where(AuditLog.op == "db.recovery.release")
                               .order_by(AuditLog.id)).all()
        assert [(row.actor, row.result) for row in rows] == [("alpha-login", "failed"), ("root", "ok")]


@pytest.mark.parametrize("role", ["customer", "reseller"])
def test_recovery_routes_refuse_non_administrators(monkeypatch, role):
    from api.routers import databases
    monkeypatch.setattr(databases, "call_daemon", lambda *args, **kwargs: pytest.fail("reached the daemon"))
    identity = Identity(1, "alpha-login", role, 1, "session")
    for call in (
        lambda: databases.list_recovery_copies("alpha", identity),
        lambda: databases.restore_recovery_copy("alpha", "a" * 32, databases.RestoreRecoveryCopyBody(confirm=True), identity),
        lambda: databases.release_recovery_copy("alpha", "a" * 32, True, identity),
    ):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 403


def test_recovery_routes_forward_the_confirmation(monkeypatch):
    from api.routers import databases
    calls = []
    monkeypatch.setattr(databases, "call_daemon", lambda op, identity, **params: calls.append((op, params)) or {})
    admin = Identity(1, "root", "admin", None, "session")
    databases.list_recovery_copies("alpha", admin)
    databases.restore_recovery_copy("alpha", "a" * 32, databases.RestoreRecoveryCopyBody(confirm=True), admin)
    databases.release_recovery_copy("alpha", "b" * 32, False, admin)
    assert calls == [
        ("db.recovery.list", {"username": "alpha"}),
        ("db.recovery.restore", {"username": "alpha", "copy_id": "a" * 32, "confirm": True}),
        ("db.recovery.release", {"username": "alpha", "copy_id": "b" * 32, "confirm": False}),
    ]


def test_restore_rpc_gets_the_long_database_timeout(monkeypatch):
    from api import rpc as api_rpc
    used = []

    class Client:
        def __init__(self, name):
            self.name = name

        def call(self, op, **_kwargs):
            used.append((self.name, op))
            return {}

    monkeypatch.setattr(api_rpc, "_client", Client("default"))
    monkeypatch.setattr(api_rpc, "_database_import_client", Client("long"))
    identity = Identity(1, "root", "admin", None, "session", rpc_credential="opaque")
    api_rpc.call_daemon("db.recovery.restore", identity, username="alpha", copy_id="a" * 32, confirm=True)
    api_rpc.call_daemon("db.recovery.list", identity, username="alpha")
    assert used == [("long", "db.recovery.restore"), ("default", "db.recovery.list")]


# --- a rejected SQL statement is the caller's error -------------------------

@pytest.mark.parametrize("stderr,expected", [
    ("ERROR 1064 (42000) at line 2: You have an error in your SQL syntax\nsecond line",
     "ERROR 1064 (42000) at line 2: You have an error in your SQL syntax"),
    ("ERROR at line 1: Not allowed in the sandbox mode.", "ERROR at line 1: Not allowed in the sandbox mode."),
    ("ERROR 1142 (42000) at line 1: DROP command denied to user", "ERROR 1142 (42000) at line 1: DROP command denied to user"),
    # Client error codes and errors before any statement are not about the SQL.
    ("ERROR 2013 (HY000) at line 5: Lost connection to server during query", None),
    ("ERROR 1045 (28000): Access denied for user 'boronrestoreworker_0'@'localhost'", None),
    ("", None),
])
def test_statement_rejections_are_told_apart_from_load_failures(stderr, expected):
    assert snapshot_databases._statement_error(stderr) == expected


@pytest.fixture()
def loader(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    dump = work / "dump.sql"
    dump.write_text("INSRT INTO posts VALUES (1);\n")

    @contextmanager
    def login(_name):
        yield "boronrestoreworker_0", "secret"

    monkeypatch.setattr(snapshot_databases, "import_login", login)
    outcome = {}
    monkeypatch.setattr(snapshot_databases, "run",
                        lambda args, **kwargs: ProcResult(args=args, returncode=1, stdout="", stderr=outcome["stderr"]))
    return work, dump, outcome


def test_restore_reports_a_rejected_statement_as_a_caller_error(loader):
    from daemon import server
    work, dump, outcome = loader
    outcome["stderr"] = "ERROR 1064 (42000) at line 1: You have an error in your SQL syntax\n"
    with pytest.raises(snapshot_databases.SqlImportError) as error:
        snapshot_databases.restore_database("alpha_blog", dump, work)
    assert str(error.value) == "ERROR 1064 (42000) at line 1: You have an error in your SQL syntax"
    assert isinstance(error.value, ValidationError) and isinstance(error.value, RuntimeError)
    assert server.rpc_error_code(error.value) == "bad_request"
    assert not list(work.glob("client-*.cnf"))


def test_restore_keeps_load_failures_internal(loader):
    from daemon import server
    work, dump, outcome = loader
    outcome["stderr"] = "ERROR 2002 (HY000): Can't connect to local server through socket '/run/mysqld/mysqld.sock'\n"
    with pytest.raises(RuntimeError, match="Database restore failed for alpha_blog") as error:
        snapshot_databases.restore_database("alpha_blog", dump, work)
    assert not isinstance(error.value, ValidationError)
    assert server.rpc_error_code(error.value) == "internal_error"


def test_import_reports_only_the_rejected_statement(recovery, monkeypatch):
    def rejecting(name, dump_path, work, *, replace_tables=False):
        if Path(dump_path).name == "uploaded.sql":
            raise snapshot_databases.SqlImportError("ERROR 1064 (42000) at line 3: You have an error")
        return {"database": name, "bytes_read": 1}

    monkeypatch.setattr(snapshot_databases, "restore_database", rejecting)
    with pytest.raises(ValidationError) as error:
        hdb.import_database({"username": "alpha", "name": "alpha_blog", "path": "/x.sql"})
    assert str(error.value) == ("SQL import failed; the database was restored to its previous contents. "
                                "MariaDB reported: ERROR 1064 (42000) at line 3: You have an error")
    assert _copies()["copies"] == []


def test_rename_rejected_by_mariadb_leaves_the_original_and_says_why(isolated_db, stub_sysops, stub_mariadb,  # noqa: F811
                                                                      monkeypatch):
    ha.create_account({"username": "demo1"})
    hdb.create_database({"username": "demo1", "name": "shop"})
    monkeypatch.setattr(hdb, "_require_safety_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(snapshot_databases, "dump_database",
                        lambda name, output, work, **kwargs: Path(output).write_text(COMPLETE_DUMP))

    def rejecting(*_args, **_kwargs):
        raise snapshot_databases.SqlImportError("ERROR 1118 (42000) at line 9: Row size too large")

    monkeypatch.setattr(snapshot_databases, "restore_database", rejecting)
    with pytest.raises(ValidationError, match=r"'demo1_shop' is unchanged\. MariaDB reported: ERROR 1118") as error:
        hdb.rename_database({"username": "demo1", "name": "shop", "new_name": "store"})
    assert not isinstance(error.value, snapshot_databases.SqlImportError)
    assert ("drop_database", "demo1_store") in stub_mariadb
    assert [row["db_name"] for row in hdb.list_databases({"username": "demo1"})["databases"]] == ["demo1_shop"]
