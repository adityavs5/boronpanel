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
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard, **inodes: None)
    monkeypatch.setattr(ha.sysops, "lock_user", lambda username: None)
    monkeypatch.setattr(ha.sysops, "unlock_user", lambda username: None)
    monkeypatch.setattr(plans.sysops, "set_quota", lambda username, soft, hard, **inodes: None)


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


def test_delete_plan_in_use_is_refused(isolated_db, stub_sysops, stub_redis):
    """Deleting the plan silently dropped the assigned accounts to their
    legacy limits at the next reconcile."""
    from shared.validation import ConflictError
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(BASIC))
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
    with pytest.raises(ConflictError, match=r"assigned to 1 account\(s\) \(demo1\)"):
        plans.delete_plan({"plan_id": created["id"]})
    assert plans.get_plan({"plan_id": created["id"]})["id"] == created["id"]


def test_delete_plan_clears_terminated_account_plan_id(isolated_db, stub_sysops, stub_redis):
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(BASIC))
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert account.plan_id == created["id"]
        account.status = "terminated"

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



def test_plan_quota_failure_is_not_reported_as_success(isolated_db, stub_sysops, stub_redis, monkeypatch):
    ha.create_account({'username': 'demo1'})
    created = plans.create_plan({'name': 'Test quota'})
    def fail(*args, **kwargs):
        raise RuntimeError('quota unavailable')
    monkeypatch.setattr(plans.sysops, 'set_quota', fail)
    with pytest.raises(RuntimeError, match='quota unavailable'):
        plans.apply_plan({'username': 'demo1', 'plan_id': created['id']})


# --- plan edits reach assigned accounts --------------------------------------


def test_plan_update_propagates_quota_inode_and_usage_limits(isolated_db, stub_sysops, stub_redis, monkeypatch):
    quotas = []
    monkeypatch.setattr(plans.sysops, "set_quota", lambda username, soft, hard, **inodes: quotas.append((username, soft, hard)))
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    created = plans.create_plan(dict(BASIC))
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
    with write_session() as session:
        enforce_at = session.scalar(select(Account).where(Account.username == "demo1")).inode_enforce_at
    quotas.clear()

    result = plans.update_plan({"plan_id": created["id"], "quota_soft_mb": 3000, "quota_hard_mb": 3500,
                                "database_limit": 7})

    assert result["accounts_updated"] == 1 and result["propagation_failures"] == []
    assert quotas == [("demo1", 3000, 3500)]  # demo2 is not on the plan
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert (account.quota_soft_mb, account.quota_hard_mb) == (3000, 3500)
        # Unchanged inode limits do not restart the customer's warning period.
        assert account.inode_enforce_at == enforce_at
        limits = session.scalar(select(AccountResourceLimits).where(AccountResourceLimits.account_id == account.id))
        assert limits.database_limit == 7
    # Redis did not change, so no service is restarted.
    assert stub_redis["enabled"] == [] and stub_redis["disabled"] == []


def test_plan_update_reconciles_redis_only_when_it_changes(isolated_db, stub_sysops, stub_redis):
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(BASIC))
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
    plans.update_plan({"plan_id": created["id"], "redis_enabled": True})
    assert stub_redis["enabled"] == ["demo1"]
    plans.update_plan({"plan_id": created["id"], "quota_soft_mb": 2000})
    assert stub_redis["enabled"] == ["demo1"]


def test_apply_plan_mirrors_policy_into_account_columns_and_resets_override(isolated_db, stub_sysops, stub_redis, monkeypatch):
    from shared.models import ResourcePolicy
    monkeypatch.setattr("daemon.resource_manager.available_cpu_cores", lambda: 4)
    ha.create_account({"username": "demo1"})
    created = plans.create_plan({"name": "Pro", "cpu_pct": 25, "mem_mb": 512,
                                 "cpu_cores": 1.0, "memory_max_mb": 1024, "memory_high_mb": 900})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(ResourcePolicy(scope_type="account", scope_id=account.id, cpu_cores=2.0, memory_max_mb=2048))

    result = plans.apply_plan({"username": "demo1", "plan_id": created["id"]})

    assert (result["cpu_pct"], result["mem_mb"], result["limits_source"]) == (100, 1024, "plan")
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert (account.cpu_pct, account.mem_mb) == (100, 1024)
        assert session.scalar(select(ResourcePolicy).where(ResourcePolicy.scope_type == "account")) is None


def test_plan_update_keeps_per_account_override(isolated_db, stub_sysops, stub_redis, monkeypatch):
    from shared.models import ResourcePolicy
    monkeypatch.setattr("daemon.resource_manager.available_cpu_cores", lambda: 4)
    ha.create_account({"username": "demo1"})
    created = plans.create_plan(dict(BASIC))
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
    ha.set_limits({"username": "demo1", "mem_mb": 768})
    plans.update_plan({"plan_id": created["id"], "mem_mb": 2048, "memory_max_mb": 2048})
    assert ha.get_account({"username": "demo1"})["mem_mb"] == 768
    with write_session() as session:
        assert session.scalar(select(ResourcePolicy).where(ResourcePolicy.scope_type == "account")) is not None


# --- round 2: limit conversion, inode limits on apply, propagation locking ---


def test_plan_legacy_columns_mirror_the_enforced_policy(isolated_db, monkeypatch):
    """[UW L10] cpu_cores 1.0 was saved next to the cpu_pct 25 default."""
    from shared.models import Plan, ResourcePolicy
    monkeypatch.setattr("daemon.resource_manager.available_cpu_cores", lambda: 4)
    created = plans.create_plan({"name": "Core", "cpu_cores": 1.0, "memory_max_mb": 2048, "nproc": 80})
    assert (created["cpu_pct"], created["cpu_cores"]) == (100, 1.0)
    assert (created["mem_mb"], created["pids_max"]) == (2048, 80)
    # The soft memory limit follows the hard one, not the legacy 512 MB default.
    assert created["memory_high_mb"] == int(2048 * .9)
    with write_session() as session:
        plan = session.get(Plan, created["id"])
        assert (plan.cpu_pct, plan.mem_mb, plan.pids_max) == (100, 2048, 80)
        plan.cpu_pct = 25  # a row saved by an older release
        session.scalar(select(ResourcePolicy).where(ResourcePolicy.scope_id == plan.id)).cpu_cores = 1.0
    assert plans.get_plan({"plan_id": created["id"]})["cpu_pct"] == 100


def test_unlimited_policy_keeps_the_legacy_column(isolated_db, monkeypatch):
    monkeypatch.setattr("daemon.resource_manager.available_cpu_cores", lambda: 4)
    created = plans.create_plan({"name": "Open", "cpu_pct": 40, "cpu_cores": None})
    assert (created["cpu_pct"], created["cpu_cores"]) == (40, None)


def test_legacy_only_plan_edit_changes_what_accounts_enforce(isolated_db, monkeypatch):
    """Editing only cpu_pct changed the column and left the policy as it was."""
    monkeypatch.setattr("daemon.resource_manager.available_cpu_cores", lambda: 4)
    created = plans.create_plan({"name": "Core", "cpu_cores": 1.0, "memory_max_mb": 2048, "memory_high_mb": 1800})
    updated = plans.update_plan({"plan_id": created["id"], "cpu_pct": 50, "mem_mb": 1024, "io_mb": 20})
    assert (updated["cpu_cores"], updated["cpu_pct"]) == (0.5, 50)
    assert (updated["memory_max_mb"], updated["mem_mb"]) == (1024, 1024)
    assert updated["memory_high_mb"] <= 1024
    assert updated["io_read_bps"] == updated["io_write_bps"] == 20 * 1048576
    # A policy field named in the same edit wins over its legacy form.
    updated = plans.update_plan({"plan_id": created["id"], "cpu_pct": 75, "cpu_cores": 2.0})
    assert (updated["cpu_cores"], updated["cpu_pct"]) == (2.0, 200)


def test_create_route_derives_the_limit_form_the_request_left_out():
    from api.routers.plans import PlanBody, _create_fields
    fields = _create_fields(PlanBody(name="Core", cpu_cores=1.0))
    assert "cpu_pct" not in fields and fields["cpu_cores"] == 1.0
    fields = _create_fields(PlanBody(name="Small", mem_mb=512))
    assert fields["mem_mb"] == 512 and "memory_max_mb" not in fields and "memory_high_mb" not in fields
    # Untouched limits keep both documented defaults.
    assert fields["cpu_pct"] == 25 and fields["cpu_cores"] == .5


def _recording_quota(monkeypatch):
    calls = []

    def set_quota(username, soft, hard, *, inode_soft=0, inode_hard=0):
        calls.append((username, soft, hard, inode_soft, inode_hard))

    monkeypatch.setattr(plans.sysops, "set_quota", set_quota)
    return calls


def test_plan_apply_keeps_enforced_inode_limits_on_the_quota(isolated_db, stub_sysops, stub_redis, monkeypatch):
    """setquota writes inode and block limits together; plan apply used to
    rewrite the block quota with inode limits 0 (unlimited) for good."""
    calls = _recording_quota(monkeypatch)
    ha.create_account({"username": "demo1"})
    assert ha.get_account({"username": "demo1"})["inode_enforcement_state"] == "enforced"
    created = plans.create_plan(dict(BASIC))
    calls.clear()
    plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
    assert calls == [("demo1", BASIC["quota_soft_mb"], BASIC["quota_hard_mb"], 450_000, 500_000)]


def test_raised_inode_limits_need_no_warning_period(isolated_db, stub_sysops, stub_redis, monkeypatch):
    calls = _recording_quota(monkeypatch)
    ha.create_account({"username": "demo1"})
    created = plans.create_plan({**BASIC, "inode_soft_limit": 900_000, "inode_hard_limit": 1_000_000})
    calls.clear()
    result = plans.apply_plan({"username": "demo1", "plan_id": created["id"]})
    assert result["inode_enforcement_state"] == "enforced"
    assert calls[-1][3:] == (900_000, 1_000_000)
    # Lowering them still gives the customer the warning period first.
    lower = plans.create_plan({**BASIC, "name": "Lower", "inode_soft_limit": 1000, "inode_hard_limit": 2000})
    assert plans.apply_plan({"username": "demo1", "plan_id": lower["id"]})["inode_enforcement_state"] == "warning"


def _two_accounts_on(plan_values):
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    created = plans.create_plan(plan_values)
    for username in ("demo1", "demo2"):
        plans.apply_plan({"username": username, "plan_id": created["id"]})
    return created


def test_plan_update_takes_the_database_lock_per_account(isolated_db, stub_sysops, stub_redis, monkeypatch):
    """The lock was held across the whole propagation, so a large plan made
    every other database operation wait and fail with BusyError."""
    from contextlib import contextmanager
    created = _two_accounts_on(dict(BASIC))
    events = []
    real_lock = plans.mutation_lock

    @contextmanager
    def lock(**kwargs):
        with real_lock(**kwargs):
            events.append("hold")
            yield
            events.append("release")

    real_apply = plans._apply_to_account
    monkeypatch.setattr(plans, "mutation_lock", lock)
    monkeypatch.setattr(plans, "_apply_to_account",
                        lambda username, *args, **kwargs: events.append(username) or real_apply(username, *args, **kwargs))

    result = plans.update_plan({"plan_id": created["id"], "database_limit": 9})

    assert result["accounts_updated"] == 2 and result["propagation_failures"] == []
    assert events == ["hold", "release", "hold", "demo1", "release", "hold", "demo2", "release"]


def test_busy_lock_is_reported_per_account_without_waiting_for_each(isolated_db, stub_sysops, stub_redis, monkeypatch):
    import threading
    import time
    from contextlib import contextmanager
    from daemon import database_operations, mutation_locks
    created = _two_accounts_on(dict(BASIC))
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 1.0)
    held, release = threading.Event(), threading.Event()

    def backup():
        with database_operations.mutation_lock(blocking=True):
            held.set()
            release.wait(10)

    real_lock = plans.mutation_lock
    calls = []

    @contextmanager
    def lock(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:  # the plan is saved; a backup takes the lock now
            threading.Thread(target=backup, daemon=True).start()
            held.wait(5)
        with real_lock(**kwargs):
            yield

    monkeypatch.setattr(plans, "mutation_lock", lock)
    started = time.monotonic()
    try:
        result = plans.update_plan({"plan_id": created["id"], "database_limit": 9})
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert result["accounts_updated"] == 0
    assert [failure["username"] for failure in result["propagation_failures"]] == ["demo1", "demo2"]
    assert all("in progress" in failure["error"] for failure in result["propagation_failures"])
    assert elapsed < 1.9  # one bounded wait for the whole loop, not one per account


def test_propagation_skips_an_account_that_left_the_plan(isolated_db, stub_sysops, stub_redis, monkeypatch):
    created = _two_accounts_on(dict(BASIC))
    other = plans.create_plan(dict(PRO))
    real_apply = plans._apply_to_account

    def apply(username, plan_id, **kwargs):
        if username == "demo1":  # moved to another plan while the edit runs
            real_apply("demo2", other["id"])
        return real_apply(username, plan_id, **kwargs)

    monkeypatch.setattr(plans, "_apply_to_account", apply)
    result = plans.update_plan({"plan_id": created["id"], "database_limit": 9})
    assert result["accounts_updated"] == 1 and result["propagation_failures"] == []
    with write_session() as session:
        assert session.scalar(select(Account.plan_id).where(Account.username == "demo2")) == other["id"]
