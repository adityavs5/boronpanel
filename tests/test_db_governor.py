from pathlib import Path
import datetime as dt

import pytest

from daemon import db_governor
from shared.db import write_session
from shared.models import Account, DatabaseUser, DbGovernorPolicy, FeatureControl, PmaToken, utcnow


def _owned_user():
    with write_session() as db:
        account = Account(username="dbowner", status="active")
        db.add(account)
        db.flush()
        db.add(DatabaseUser(account_id=account.id, db_user="dbowner_wp", host="localhost"))
        return account.id


def test_monitor_policy_never_applies_and_enforce_is_explicit(isolated_db, monkeypatch):
    _owned_user()
    calls = []
    monkeypatch.setattr(db_governor, "_apply_user", lambda user, host, policy: calls.append((user, host, policy)))
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="enforce"))
    monitored = db_governor.save_policy({
        "username": "dbowner", "mode": "monitor", "max_user_connections": 10,
    })
    assert monitored["applied"] is False and calls == []
    enforced = db_governor.save_policy({
        "username": "dbowner", "mode": "enforce", "max_user_connections": 10,
    })
    assert enforced["applied"] is True
    assert calls[-1][0:2] == ("dbowner_wp", "localhost")
    assert calls[-1][2].max_user_connections == 10


def test_global_pause_removes_previously_enforced_limits(isolated_db, monkeypatch):
    _owned_user()
    calls = []
    monkeypatch.setattr(db_governor, "_apply_user", lambda user, host, policy: calls.append((user, policy)))
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="enforce"))
    db_governor.save_policy({"username": "dbowner", "mode": "enforce", "max_user_connections": 5})
    calls.clear()
    result = db_governor.set_global_mode({"mode": "paused"})
    assert result["global_mode"] == "paused"
    assert calls == [("dbowner_wp", None)]


def test_userstat_enable_is_persisted_only_after_live_success(isolated_db, tmp_path, monkeypatch):
    target = tmp_path / "90-boron-userstat.cnf"
    monkeypatch.setattr(db_governor, "USERSTAT_CONFIG", target)
    statements = []

    class Cursor:
        def execute(self, sql): statements.append(sql)
    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    monkeypatch.setattr(db_governor.mariadb, "_connect", Connection)
    monkeypatch.setattr(db_governor, "userstat_status", lambda params=None: {"enabled": True, "version": "10.11"})
    result = db_governor.enable_userstat({"confirm": True})
    assert result["enabled"] is True
    assert statements == ["SET GLOBAL userstat = ON"]
    assert target.read_text().endswith("userstat=1\n")
    assert target.stat().st_mode & 0o777 == 0o644


def test_new_database_user_inherits_enforced_policy(isolated_db, monkeypatch):
    account_id = _owned_user()
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="enforce"))
        db.add(DbGovernorPolicy(
            scope_type="account", scope_id=account_id, mode="enforce",
            max_user_connections=7, max_queries_per_hour=100,
        ))
    calls = []
    monkeypatch.setattr(db_governor, "_apply_user", lambda user, host, policy: calls.append((user, host, policy)))
    result = db_governor.apply_new_user(account_id, "dbowner_new", "localhost")
    assert result["applied"] is True
    assert calls[0][0:2] == ("dbowner_new", "localhost")
    assert calls[0][2].max_user_connections == 7


def test_new_database_user_stays_unlimited_outside_enforce_mode(isolated_db, monkeypatch):
    account_id = _owned_user()
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="monitor"))
        db.add(DbGovernorPolicy(
            scope_type="account", scope_id=account_id, mode="enforce", max_user_connections=7,
        ))
    calls = []
    monkeypatch.setattr(db_governor, "_apply_user", lambda *args: calls.append(args))
    assert db_governor.apply_new_user(account_id, "dbowner_new", "localhost") == {"applied": False}
    assert calls == []


def test_retained_pma_login_is_included_in_governor_transitions(isolated_db, monkeypatch):
    account_id = _owned_user()
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="enforce"))
        db.add(DbGovernorPolicy(
            scope_type="account", scope_id=account_id, mode="enforce",
            max_user_connections=4,
        ))
        db.add(PmaToken(
            token_hash="a" * 64,
            account_id=account_id,
            db_name="dbowner_shop",
            ephemeral_db_user="boronphpmyadminlogin_retained",
            expires_at=utcnow() - dt.timedelta(minutes=1),
        ))
    calls = []
    monkeypatch.setattr(db_governor, "_apply_user", lambda user, host, policy: calls.append((user, host, policy)))

    db_governor.set_global_mode({"mode": "paused"})

    assert {(user, host) for user, host, _policy in calls} == {
        ("dbowner_wp", "localhost"),
        ("boronphpmyadminlogin_retained", "localhost"),
    }
    assert all(policy is None for _user, _host, policy in calls)


def test_policy_update_rolls_back_native_state_and_durable_policy(isolated_db, monkeypatch):
    account_id = _owned_user()
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="enforce"))
        db.add(DbGovernorPolicy(
            scope_type="account", scope_id=account_id, mode="enforce",
            max_user_connections=3,
        ))
    calls = []

    def fail_target(user, host, policy):
        calls.append((user, host, None if policy is None else policy.max_user_connections))
        if policy is not None and policy.max_user_connections == 9:
            raise RuntimeError("native apply failed")

    monkeypatch.setattr(db_governor, "_apply_user", fail_target)
    with pytest.raises(RuntimeError, match="native apply failed"):
        db_governor.save_policy({
            "username": "dbowner", "mode": "enforce", "max_user_connections": 9,
        })

    assert calls == [
        ("dbowner_wp", "localhost", 9),
        ("dbowner_wp", "localhost", 3),
    ]
    with write_session() as db:
        policy = db.scalar(db_governor.select(DbGovernorPolicy).where(
            DbGovernorPolicy.scope_id == account_id,
        ))
        assert policy.max_user_connections == 3


def test_global_transition_rolls_back_current_partially_applied_login(isolated_db, monkeypatch):
    account_id = _owned_user()
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="monitor"))
        db.add(DbGovernorPolicy(
            scope_type="account", scope_id=account_id, mode="enforce",
            max_user_connections=6,
        ))
    calls = []

    def partial_failure(user, host, policy):
        calls.append((user, None if policy is None else policy.max_user_connections))
        if len(calls) == 1:
            raise RuntimeError("statement-time clause failed")

    monkeypatch.setattr(db_governor, "_apply_user", partial_failure)
    with pytest.raises(RuntimeError, match="statement-time clause failed"):
        db_governor.set_global_mode({"mode": "enforce"})

    assert calls == [("dbowner_wp", 6), ("dbowner_wp", None)]
    with write_session() as db:
        assert db.get(FeatureControl, 1).db_governor_mode == "monitor"


def test_statement_time_reset_ignores_only_explicitly_unsupported_clause(monkeypatch):
    class DatabaseError(Exception):
        pass

    class Cursor:
        def __init__(self, code):
            self.code = code
            self.calls = 0

        def execute(self, _sql):
            self.calls += 1
            if self.calls == 2:
                raise DatabaseError(self.code, "database response")

    class Connection:
        def __init__(self, code):
            self.cursor_instance = Cursor(code)

        def escape(self, value):
            return repr(value)

        def cursor(self):
            return self.cursor_instance

        def close(self):
            pass

    monkeypatch.setattr(db_governor.mariadb, "_connect", lambda: Connection(1064))
    db_governor._apply_user("dbowner_wp", "localhost", None)

    monkeypatch.setattr(db_governor.mariadb, "_connect", lambda: Connection(1045))
    with pytest.raises(DatabaseError) as exc_info:
        db_governor._apply_user("dbowner_wp", "localhost", None)
    assert exc_info.value.args[0] == 1045


def test_policy_update_reports_failed_native_rollback(isolated_db, monkeypatch):
    account_id = _owned_user()
    with write_session() as db:
        db.add(FeatureControl(id=1, db_governor_mode="enforce"))
        db.add(DbGovernorPolicy(
            scope_type="account", scope_id=account_id, mode="enforce",
            max_user_connections=3,
        ))
    calls = 0

    def fail_apply_and_rollback(_user, _host, _policy):
        nonlocal calls
        calls += 1
        raise RuntimeError("apply failed" if calls == 1 else "rollback failed")

    monkeypatch.setattr(db_governor, "_apply_user", fail_apply_and_rollback)
    with pytest.raises(RuntimeError, match="restoring prior limits also failed"):
        db_governor.save_policy({
            "username": "dbowner", "mode": "enforce", "max_user_connections": 9,
        })


def test_userstat_temp_file_is_removed_when_live_enable_fails(isolated_db, tmp_path, monkeypatch):
    target = tmp_path / "90-boron-userstat.cnf"
    monkeypatch.setattr(db_governor, "USERSTAT_CONFIG", target)
    monkeypatch.setattr(db_governor.mariadb, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    import pytest
    with pytest.raises(RuntimeError, match="offline"):
        db_governor.enable_userstat({"confirm": True})
    assert not target.exists()
    assert list(tmp_path.glob(f".{target.name}.tmp.*")) == []
