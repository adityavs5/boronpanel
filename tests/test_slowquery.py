import datetime as dt

import pytest

from daemon import slowquery
from daemon.procutil import ProcResult
from shared.validation import ValidationError


class FakeCursor:
    def __init__(self, responses):
        self._responses = responses
        self._last = None
        self.executed = []

    def execute(self, query, args=None):
        self.executed.append((query, args))
        self._last = self._responses.pop(0)

    def fetchone(self):
        return self._last[0]

    def fetchall(self):
        return self._last


class FakeConn:
    def __init__(self, responses):
        self.cursor_obj = FakeCursor(responses)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


def test_get_status_parses_show_variables(monkeypatch):
    responses = [[("slow_query_log", "ON")], [("long_query_time", "1.000000")], [("log_output", "TABLE")]]
    fake_conn = FakeConn(responses)
    monkeypatch.setattr(slowquery.mariadb, "_connect", lambda: fake_conn)
    status = slowquery.get_status({})
    assert status["enabled"] is True
    assert status["long_query_time"] == 1.0
    assert status["log_output"] == "TABLE"


def test_get_status_disabled(monkeypatch):
    responses = [[("slow_query_log", "OFF")], [("long_query_time", "10.000000")], [("log_output", "FILE")]]
    monkeypatch.setattr(slowquery.mariadb, "_connect", lambda: FakeConn(responses))
    status = slowquery.get_status({})
    assert status["enabled"] is False


def test_bootstrap_requires_confirm():
    with pytest.raises(ValidationError):
        slowquery.bootstrap_slow_query_log({})


def test_bootstrap_writes_config_restarts_and_verifies(monkeypatch, tmp_path):
    conf_path = tmp_path / "60-forgehost-slowlog.cnf"
    monkeypatch.setattr(slowquery, "SLOWLOG_CONF_PATH", str(conf_path))

    calls = []

    def fake_run(args, timeout=60):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(slowquery, "run", fake_run)

    responses = [[("slow_query_log", "ON")], [("long_query_time", "1.000000")], [("log_output", "TABLE")]]
    monkeypatch.setattr(slowquery.mariadb, "_connect", lambda: FakeConn(responses))

    result = slowquery.bootstrap_slow_query_log({"confirm": True})
    assert result["enabled"] is True
    assert conf_path.exists()
    assert "slow_query_log = 1" in conf_path.read_text()
    assert ["systemctl", "restart", "mariadb.service"] in calls


def test_bootstrap_rolls_back_on_restart_failure(monkeypatch, tmp_path):
    conf_path = tmp_path / "60-forgehost-slowlog.cnf"
    conf_path.write_text("# original content\n")
    monkeypatch.setattr(slowquery, "SLOWLOG_CONF_PATH", str(conf_path))
    monkeypatch.setattr(slowquery, "run", lambda args, timeout=60: ProcResult(args=args, returncode=1, stdout="", stderr="failed to restart"))

    with pytest.raises(RuntimeError):
        slowquery.bootstrap_slow_query_log({"confirm": True})
    assert conf_path.read_text() == "# original content\n"


def test_bootstrap_rolls_back_when_setting_never_takes_effect(monkeypatch, tmp_path):
    conf_path = tmp_path / "60-forgehost-slowlog.cnf"
    monkeypatch.setattr(slowquery, "SLOWLOG_CONF_PATH", str(conf_path))
    monkeypatch.setattr(slowquery, "run", lambda args, timeout=60: ProcResult(args=args, returncode=0, stdout="", stderr=""))

    responses = [[("slow_query_log", "OFF")], [("long_query_time", "10.000000")], [("log_output", "FILE")]]
    monkeypatch.setattr(slowquery.mariadb, "_connect", lambda: FakeConn(responses))

    with pytest.raises(RuntimeError):
        slowquery.bootstrap_slow_query_log({"confirm": True})
    assert not conf_path.exists()  # rolled back to "no file existed before"


def test_row_to_dict_converts_timedelta_and_datetime():
    row = (
        dt.datetime(2026, 7, 3, 18, 0, 0),
        "root[root] @ localhost []",
        dt.timedelta(seconds=2, microseconds=500000),
        dt.timedelta(seconds=0),
        1,
        1000,
        "demo1_wp",
        "SELECT * FROM wp_posts",
        42,
    )
    result = slowquery._row_to_dict(row)
    assert result["query_time_seconds"] == 2.5
    assert result["rows_examined"] == 1000
    assert result["db"] == "demo1_wp"
    assert result["sql_text"] == "SELECT * FROM wp_posts"


def test_list_slow_queries_builds_correct_filter_query(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, query, args):
            captured["query"] = query
            captured["args"] = args

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(slowquery.mariadb, "_connect", lambda: Conn())
    slowquery.list_slow_queries({"db": "demo1_wp", "q": "posts", "limit": 10})
    assert "db = %s" in captured["query"]
    assert "sql_text LIKE %s" in captured["query"]
    assert "ORDER BY query_time DESC" in captured["query"]
    assert captured["args"] == ("demo1_wp", "%posts%", "%posts%", 10)


def test_list_slow_queries_no_filters(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, query, args):
            captured["query"] = query
            captured["args"] = args

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(slowquery.mariadb, "_connect", lambda: Conn())
    slowquery.list_slow_queries({})
    assert "WHERE" not in captured["query"]
    assert captured["args"] == (100,)


def test_list_slow_queries_caps_limit(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, query, args):
            captured["args"] = args

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(slowquery.mariadb, "_connect", lambda: Conn())
    slowquery.list_slow_queries({"limit": 999999})
    assert captured["args"] == (1000,)
