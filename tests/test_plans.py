import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import plans
from daemon import redisacct
from shared.db import write_session
from shared.models import Account, AccountResourceLimits


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    monkeypatch.setattr(ha.sysops, "lock_user", lambda username: None)
    monkeypatch.setattr(ha.sysops, "unlock_user", lambda username: None)
    monkeypatch.setattr(plans.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_redis(monkeypatch):
    calls = {"enabled": [], "disabled": []}

    def enable(params):
        calls["enabled"].append(params["username"])
        return {"username": params["username"], "enabled": True, "provisioned": True}

    def disable(params):
        calls["disabled"].append(params["username"])
        return {"username": params["username"], "enabled": False, "provisioned": True}

    def get_status(params):
        username = params["username"]
        if username in calls["enabled"] and username not in calls["disabled"]:
            return {"username": username, "enabled": True, "provisioned": True}
        return {"username": username, "enabled": False, "provisioned": username in calls["enabled"]}

    monkeypatch.setattr(plans.redisacct, "enable_redis", enable)
    monkeypatch.setattr(plans.redisacct, "disable_redis", disable)
    monkeypatch.setattr(plans.redisacct, "get_status", get_status)
    return calls


BASIC = dict(
    name="Basic", cpu_pct=25, mem_mb=512, io_mb=50, pids_max=50,
    quota_soft_mb=2048, quota_hard_mb=3072, bandwidth_limit_mb=10240,
    database_limit=2, email_account_limit=5, subdomain_limit=3,
    ftp_account_limit=1, app_limit=1, redis_enabled=False,
)
PRO = dict(
    name="Pro", cpu_pct=50, mem_mb=1024, io_mb=100, pids_max=100,
    quota_soft_mb=10240, quota_hard_mb=12288, bandwidth_limit_mb=51200,
    database_limit=10, email_account_limit=25, subdomain_limit=10,
    ftp_account_limit=5, app_limit=5, redis_enabled=True,
)


# --- CRUD --------------------------------------------------------------------


def test_create_and_get_plan(isolated_db):
    created = plans.create_plan(dict(BASIC))
    assert created["name"] == "Basic"
    assert created["redis_enabled"] is False
    fetched = plans.get_plan({"plan_id": created["id"]})
    # created_at/updated_at legitimately render with/without a "+00:00" tz
    # suffix depending on whether the value was read within the same flush
    # or from a fresh SELECT after a real SQLite round-trip -- a pre-existing
    # quirk of this project's DateTime(timezone=True) + SQLite combination,
    # not specific to plans. Compare everything else exactly.
    assert {k: v for k, v in fetched.items() if not k.endswith("_at")} == {
        k: v for k, v in created.items() if not k.endswith("_at")
    }


def test_list_plans_sorted_by_name(isolated_db):
    plans.create_plan(dict(PRO))
    plans.create_plan(dict(BASIC))
    result = plans.list_plans({})["plans"]
    assert [p["name"] for p in result] == ["Basic", "Pro"]


def test_create_plan_rejects_duplicate_name(isolated_db):
    plans.create_plan(dict(BASIC))
    with pytest.raises(Exception):
        plans.create_plan(dict(BASIC))


def test_create_plan_rejects_invalid_cpu_pct(isolated_db):
    bad = dict(BASIC)
    bad["cpu_pct"] = 25601
    with pytest.raises(Exception):
        plans.create_plan(bad)


def test_create_plan_rejects_inverted_quota(isolated_db):
    bad = dict(BASIC)
    bad["quota_soft_mb"] = 4096
    bad["quota_hard_mb"] = 1024
    with pytest.raises(Exception):
        plans.create_plan(bad)


def test_update_plan_partial(isolated_db):
    created = plans.create_plan(dict(BASIC))
    updated = plans.update_plan({"plan_id": created["id"], "mem_mb": 2048})
    assert updated["mem_mb"] == 2048
    assert updated["cpu_pct"] == BASIC["cpu_pct"]  # untouched


def test_update_plan_unknown_raises(isolated_db):
    with pytest.raises(Exception):
        plans.update_plan({"plan_id": 999, "mem_mb": 2048})


def test_delete_plan_clears_account_plan_id(isolated_db, stub_sysops, stub_redis):
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(BASIC))
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert account.plan_id == created["id"]

    plans.delete_plan({"plan_id": created["id"]})

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert account.plan_id is None
    with pytest.raises(Exception):
        plans.get_plan({"plan_id": created["id"]})


# --- apply_plan ----------------------------------------------------------------


def test_apply_plan_sets_every_field_atomically(isolated_db, stub_sysops, stub_redis):
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(PRO))

    result = plans.apply_plan({"username": "demo1", "plan_id": created["id"]})

    assert result["cpu_pct"] == PRO["cpu_pct"]
    assert result["mem_mb"] == PRO["mem_mb"]
    assert result["io_mb"] == PRO["io_mb"]
    assert result["pids_max"] == PRO["pids_max"]
    assert result["quota_soft_mb"] == PRO["quota_soft_mb"]
    assert result["quota_hard_mb"] == PRO["quota_hard_mb"]
    assert result["plan_id"] == created["id"]

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert account.cpu_pct == PRO["cpu_pct"]
        assert account.mem_mb == PRO["mem_mb"]
        assert account.io_mb == PRO["io_mb"]
        assert account.pids_max == PRO["pids_max"]
        assert account.quota_soft_mb == PRO["quota_soft_mb"]
        assert account.quota_hard_mb == PRO["quota_hard_mb"]
        assert account.plan_id == created["id"]

        limits = session.scalar(select(AccountResourceLimits).where(AccountResourceLimits.account_id == account.id))
        assert limits.bandwidth_limit_mb == PRO["bandwidth_limit_mb"]
        assert limits.database_limit == PRO["database_limit"]
        assert limits.email_account_limit == PRO["email_account_limit"]
        assert limits.subdomain_limit == PRO["subdomain_limit"]
        assert limits.ftp_account_limit == PRO["ftp_account_limit"]
        assert limits.app_limit == PRO["app_limit"]

    assert stub_redis["enabled"] == ["demo1"]


def test_apply_plan_with_redis_off_is_noop_when_never_enabled(isolated_db, stub_sysops, stub_redis):
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(BASIC))  # redis_enabled=False
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
    assert stub_redis["enabled"] == []
    assert stub_redis["disabled"] == []


def test_reapplying_different_plan_overwrites_not_additive(isolated_db, stub_sysops, stub_redis):
    ha.create_account({"username": "demo1"})
    basic = plans.create_plan(dict(BASIC))
    pro = plans.create_plan(dict(PRO))

    plans.apply_plan({"username": "demo1", "plan_id": basic["id"]})
    result = plans.apply_plan({"username": "demo1", "plan_id": pro["id"]})

    assert result["cpu_pct"] == PRO["cpu_pct"]
    assert result["plan_id"] == pro["id"]
    with write_session() as session:
        limits = session.scalar(
            select(AccountResourceLimits).where(
                AccountResourceLimits.account_id == session.scalar(select(Account.id).where(Account.username == "demo1"))
            )
        )
        assert limits.database_limit == PRO["database_limit"]
    # Basic had redis off, Pro has it on -- enable_redis called once, for Pro.
    assert stub_redis["enabled"] == ["demo1"]


def test_apply_plan_unknown_account_raises(isolated_db, stub_redis):
    created = plans.create_plan(dict(BASIC))
    with pytest.raises(Exception):
        plans.apply_plan({"username": "ghost", "plan_id": created["id"]})


def test_apply_plan_unknown_plan_raises(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    with pytest.raises(Exception):
        plans.apply_plan({"username": "demo1", "plan_id": 999})


def test_apply_plan_rejects_terminated_account(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(BASIC))
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        account.status = "terminated"
    with pytest.raises(Exception):
        plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
