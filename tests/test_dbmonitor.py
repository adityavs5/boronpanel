import pytest

from daemon import dbmonitor as dm


class _FakeCursor:
    def __init__(self, responses):
        self._responses = responses
        self._current = None
        self.description = None
        self.executed = []

    def execute(self, sql, args=None):
        self.executed.append((sql, args))
        key = next(k for k in self._responses if k in sql)
        self.description, self._current = self._responses[key]

    def fetchall(self):
        return self._current

    def fetchone(self):
        return self._current[0] if self._current else None


class _FakeConn:
    def __init__(self, responses):
        self._cursor = _FakeCursor(responses)
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.fixture()
def db_grants(isolated_db):
    from shared.db import write_session
    from shared.models import Account, DatabaseGrant

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(DatabaseGrant(account_id=account.id, db_name="demo1_app", db_user="demo1_app"))
    return "demo1"


def test_get_processlist_attributes_to_account(db_grants, monkeypatch):
    responses = {
        "SHOW FULL PROCESSLIST": (
            [("Id",), ("User",), ("Host",), ("db",), ("Command",), ("Time",), ("State",), ("Info",)],
            [(42, "demo1_app", "localhost", "demo1_app", "Query", 3, "executing", "SELECT * FROM x")],
        ),
    }
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: _FakeConn(responses))
    result = dm.get_processlist()
    assert len(result["processes"]) == 1
    proc = result["processes"][0]
    assert proc["id"] == 42
    assert proc["username"] == "demo1"


def test_get_processlist_truncates_long_info(db_grants, monkeypatch):
    long_query = "SELECT " + "x" * 3000
    responses = {
        "SHOW FULL PROCESSLIST": (
            [("Id",), ("User",), ("Host",), ("db",), ("Command",), ("Time",), ("State",), ("Info",)],
            [(1, "u", "h", None, "Query", 0, "", long_query)],
        ),
    }
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: _FakeConn(responses))
    result = dm.get_processlist()
    assert len(result["processes"][0]["info"]) < len(long_query)
    assert result["processes"][0]["info"].endswith("(truncated)")


def test_get_recent_slow_queries(monkeypatch):
    import datetime as dt

    responses = {
        "FROM mysql.slow_log": (
            None,
            [(dt.datetime(2026, 7, 10, 12, 0, 0), "root[root] @ localhost", dt.timedelta(seconds=2.5), dt.timedelta(seconds=0), 1, 100, "demo1_app", "SELECT SLEEP(2)", 7)],
        ),
    }
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: _FakeConn(responses))
    result = dm.get_recent_slow_queries()
    assert result["lookback_hours"] == 1
    assert len(result["queries"]) == 1
    assert result["queries"][0]["thread_id"] == 7
    assert result["queries"][0]["query_time_seconds"] == 2.5


def test_get_db_sizes_groups_by_account(db_grants, monkeypatch):
    responses = {
        "FROM information_schema.tables": (None, [("demo1_app", 123456), ("orphan_db", 999)]),
    }
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: _FakeConn(responses))
    result = dm.get_db_sizes()
    assert result["accounts"][0]["username"] == "demo1"
    assert result["accounts"][0]["total_bytes"] == 123456
    assert result["unattributed_schemas"] == ["orphan_db"]


def test_get_connection_summary(db_grants, monkeypatch):
    responses = {
        "Threads_connected": (None, [("Threads_connected", "5")]),
        "max_connections": (None, [("max_connections", "151")]),
        "SHOW FULL PROCESSLIST": (
            [("Id",), ("User",), ("Host",), ("db",), ("Command",), ("Time",), ("State",), ("Info",)],
            [(1, "demo1_app", "localhost", "demo1_app", "Sleep", 0, "", None), (2, "demo1_app", "localhost", "demo1_app", "Query", 1, "", "SELECT 1")],
        ),
    }
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: _FakeConn(responses))
    result = dm.get_connection_summary()
    assert result["total_connections"] == 5
    assert result["max_connections"] == 151
    assert result["per_account"] == [{"username": "demo1", "connections": 2}]


def test_kill_query_executes_kill_with_int_thread_id(db_grants, monkeypatch):
    responses = {
        "SHOW FULL PROCESSLIST": (
            [("Id",), ("User",), ("Host",), ("db",), ("Command",), ("Time",), ("State",), ("Info",)],
            [(42, "demo1_app", "localhost", "demo1_app", "Query", 3, "executing", "SELECT 1")],
        ),
        "KILL": (None, []),
    }
    fake_conn = _FakeConn(responses)
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: fake_conn)
    result = dm.kill_query("42")
    assert result == {"thread_id": 42, "status": "killed", "db": "demo1_app"}
    assert fake_conn._cursor.executed[-1][0] == "KILL 42"


def test_kill_query_rejects_non_integer():
    with pytest.raises(ValueError):
        dm.kill_query("42; DROP TABLE users")


def test_kill_query_rejects_missing_thread(db_grants, monkeypatch):
    responses = {
        "SHOW FULL PROCESSLIST": (
            [("Id",), ("User",), ("Host",), ("db",), ("Command",), ("Time",), ("State",), ("Info",)],
            [],
        ),
    }
    fake_conn = _FakeConn(responses)
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: fake_conn)
    with pytest.raises(RuntimeError, match="not currently active"):
        dm.kill_query("123")


def test_kill_query_rejects_thread_with_no_database(db_grants, monkeypatch):
    # Audit 3 A3-6: a thread with no `db` (e.g. the daemon's own connection,
    # or a system thread) is not a hosted-account query and must be refused.
    responses = {
        "SHOW FULL PROCESSLIST": (
            [("Id",), ("User",), ("Host",), ("db",), ("Command",), ("Time",), ("State",), ("Info",)],
            [(7, "boron_daemon", "localhost", None, "Sleep", 0, "", None)],
        ),
    }
    fake_conn = _FakeConn(responses)
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: fake_conn)
    with pytest.raises(RuntimeError, match="no associated database"):
        dm.kill_query("7")
    assert not any(sql.startswith("KILL") for sql, _ in fake_conn._cursor.executed)


def test_kill_query_rejects_database_not_owned_by_any_account(db_grants, monkeypatch):
    # Audit 3 A3-6: a thread whose db isn't in DatabaseGrant (e.g. `mysql`,
    # a replication thread, another admin tool) must be refused even though
    # it's a syntactically valid integer thread id.
    responses = {
        "SHOW FULL PROCESSLIST": (
            [("Id",), ("User",), ("Host",), ("db",), ("Command",), ("Time",), ("State",), ("Info",)],
            [(99, "root", "localhost", "mysql", "Query", 1, "", "SELECT 1")],
        ),
    }
    fake_conn = _FakeConn(responses)
    monkeypatch.setattr(dm.mariadb, "_connect", lambda: fake_conn)
    with pytest.raises(RuntimeError, match="does not belong to any hosted account"):
        dm.kill_query("99")
    assert not any(sql.startswith("KILL") for sql, _ in fake_conn._cursor.executed)
