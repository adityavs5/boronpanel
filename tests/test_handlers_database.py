import pytest

from daemon import handlers_account as ha
from daemon import handlers_database as hdb
from shared.validation import ValidationError


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_mariadb(monkeypatch):
    calls = []
    state = {"databases": set(), "users": set()}

    def database_exists(db_name):
        return db_name in state["databases"]

    def create_database(db_name):
        calls.append(("create_database", db_name))
        state["databases"].add(db_name)

    def drop_database(db_name):
        calls.append(("drop_database", db_name))
        state["databases"].discard(db_name)

    def create_db_user(db_user, password):
        calls.append(("create_db_user", db_user))
        state["users"].add(db_user)

    def drop_db_user(db_user):
        calls.append(("drop_db_user", db_user))
        state["users"].discard(db_user)

    def grant_all(db_name, db_user):
        calls.append(("grant_all", db_name, db_user))

    def set_password(db_user, password):
        calls.append(("set_password", db_user))

    monkeypatch.setattr(hdb.mariadb, "database_exists", database_exists)
    monkeypatch.setattr(hdb.mariadb, "create_database", create_database)
    monkeypatch.setattr(hdb.mariadb, "drop_database", drop_database)
    monkeypatch.setattr(hdb.mariadb, "create_db_user", create_db_user)
    monkeypatch.setattr(hdb.mariadb, "drop_db_user", drop_db_user)
    monkeypatch.setattr(hdb.mariadb, "grant_all", grant_all)
    monkeypatch.setattr(hdb.mariadb, "set_password", set_password)
    monkeypatch.setattr(hdb.mariadb, "generate_password", lambda: "generated-pw")
    return calls


def test_create_database_happy_path(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "demo1"})
    result = hdb.create_database({"username": "demo1", "name": "shop"})
    assert result["db_name"] == "demo1_shop"
    assert result["db_user"] == "demo1_shop"
    assert "password" in result
    assert ("create_database", "demo1_shop") in stub_mariadb
    assert ("grant_all", "demo1_shop", "demo1_shop") in stub_mariadb


def test_create_database_rejects_duplicate(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "demo1"})
    hdb.create_database({"username": "demo1", "name": "shop"})
    with pytest.raises(RuntimeError):
        hdb.create_database({"username": "demo1", "name": "shop"})


def test_create_database_unknown_account(isolated_db, stub_sysops, stub_mariadb):
    with pytest.raises(RuntimeError):
        hdb.create_database({"username": "ghost", "name": "shop"})


def test_create_database_rejects_unsafe_suffix(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "demo1"})
    with pytest.raises(ValidationError):
        hdb.create_database({"username": "demo1", "name": "bad name; DROP TABLE"})


def test_create_database_rolls_back_on_grant_failure(isolated_db, stub_sysops, stub_mariadb, monkeypatch):
    def boom(db_name, db_user):
        raise RuntimeError("grant failed")

    monkeypatch.setattr(hdb.mariadb, "grant_all", boom)
    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        hdb.create_database({"username": "demo1", "name": "shop"})
    assert ("drop_database", "demo1_shop") in stub_mariadb
    assert ("drop_db_user", "demo1_shop") in stub_mariadb

    result = hdb.list_databases({"username": "demo1"})
    assert result["databases"] == []


def test_drop_database(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "demo1"})
    hdb.create_database({"username": "demo1", "name": "shop"})
    result = hdb.drop_database({"username": "demo1", "name": "shop"})
    assert result["status"] == "dropped"
    assert hdb.list_databases({"username": "demo1"})["databases"] == []


def test_drop_database_not_found(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        hdb.drop_database({"username": "demo1", "name": "nope"})


def test_change_password(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "demo1"})
    hdb.create_database({"username": "demo1", "name": "shop"})
    result = hdb.change_password({"username": "demo1", "name": "shop", "password": "NewPassword123!"})
    assert result["password"] == "NewPassword123!"
    assert ("set_password", "demo1_shop") in stub_mariadb


def test_terminate_account_drops_all_databases(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "demo1"})
    hdb.create_database({"username": "demo1", "name": "shop"})
    hdb.create_database({"username": "demo1", "name": "blog"})

    with hdb.write_session() as session:
        from shared.models import Account

        account = session.scalar(hdb.select(Account).where(Account.username == "demo1"))
        hdb.terminate_account_databases(account)

    assert hdb.list_databases({"username": "demo1"})["databases"] == []
    assert ("drop_database", "demo1_shop") in stub_mariadb
    assert ("drop_database", "demo1_blog") in stub_mariadb


def test_same_suffix_different_accounts_get_distinct_db_names(isolated_db, stub_sysops, stub_mariadb):
    ha.create_account({"username": "alice"})
    ha.create_account({"username": "bob"})
    alice_db = hdb.create_database({"username": "alice", "name": "shop"})
    bob_db = hdb.create_database({"username": "bob", "name": "shop"})
    assert alice_db["db_name"] == "alice_shop"
    assert bob_db["db_name"] == "bob_shop"
