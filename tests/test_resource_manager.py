import datetime as dt

import pytest

from sqlalchemy import select

from daemon import resource_manager
from shared.db import write_session
from shared.models import Account, Plan, ResourcePolicy, ResourceSample
from shared.validation import ValidationError


def test_cpu_policy_uses_effective_host_capacity(monkeypatch):
    monkeypatch.setattr(resource_manager, "available_cpu_cores", lambda: 4)
    assert resource_manager.validate_policy({"cpu_cores": 4})["cpu_cores"] == 4
    with pytest.raises(ValidationError, match="between 0.25 and 4"):
        resource_manager.validate_policy({"cpu_cores": 4.25})


def _account_and_plan():
    with write_session() as db:
        plan = Plan(
            name="Resource plan",
            cpu_pct=25,
            mem_mb=512,
            io_mb=50,
            pids_max=50,
            quota_soft_mb=1024,
            quota_hard_mb=2048,
            bandwidth_limit_mb=None,
            database_limit=None,
            email_account_limit=None,
            subdomain_limit=None,
            ftp_account_limit=None,
            app_limit=None,
            redis_enabled=False,
        )
        db.add(plan)
        db.flush()
        account = Account(
            username="resource1", status="active", plan_id=plan.id,
            cpu_pct=25, mem_mb=512, io_mb=50, pids_max=50,
        )
        db.add(account)
        db.flush()
        db.add(ResourcePolicy(
            scope_type="plan", scope_id=plan.id, cpu_cores=2, cpu_weight=100,
            memory_high_mb=768, memory_max_mb=1024,
            io_read_bps=10_000_000, io_write_bps=20_000_000,
            io_read_iops=500, io_write_iops=250, nproc=90, entry_processes=12,
        ))
        return account.id, plan.id


def test_effective_policy_tracks_plan_and_account_override(isolated_db):
    account_id, _ = _account_and_plan()
    with write_session() as db:
        account = db.get(Account, account_id)
        inherited = resource_manager.effective_for_account(db, account)
        assert inherited["values"]["cpu_cores"] == 2
        assert inherited["sources"]["cpu_cores"].startswith("plan:")
        db.add(ResourcePolicy(
            scope_type="account", scope_id=account.id, cpu_cores=.5,
            cpu_weight=50, memory_high_mb=None, memory_max_mb=None,
            io_read_bps=None, io_write_bps=None, io_read_iops=None,
            io_write_iops=None, nproc=40, entry_processes=5,
        ))
    with write_session() as db:
        effective = resource_manager.effective_for_account(db, db.get(Account, account_id))
        assert effective["values"]["cpu_cores"] == .5
        assert effective["values"]["memory_max_mb"] is None
        assert effective["sources"]["nproc"] == f"account:{account_id}"


def test_expired_override_reapplies_inherited_policy_once(isolated_db, monkeypatch):
    account_id, _ = _account_and_plan()
    applied = []
    monkeypatch.setattr(resource_manager.cgroups, "apply_policy", lambda username, values: applied.append((username, values.copy())))
    with write_session() as db:
        db.add(ResourcePolicy(
            scope_type="account", scope_id=account_id, cpu_cores=8,
            expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1),
        ))
    assert resource_manager.reconcile_expired_policies() == 1
    assert applied[-1][0] == "resource1"
    assert applied[-1][1]["cpu_cores"] == 2
    assert resource_manager.reconcile_expired_policies() == 0
    with write_session() as db:
        expired = db.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "account", ResourcePolicy.scope_id == account_id,
        ))
        assert expired.last_apply_status == "expired"


def test_expired_override_retries_after_transient_apply_failure(isolated_db, monkeypatch):
    account_id, _ = _account_and_plan()
    attempts = []

    def flaky_apply(username, values):
        attempts.append((username, values.copy()))
        if len(attempts) == 1:
            raise RuntimeError("systemd temporarily unavailable")

    monkeypatch.setattr(resource_manager.cgroups, "apply_policy", flaky_apply)
    with write_session() as db:
        db.add(ResourcePolicy(
            scope_type="account", scope_id=account_id, cpu_cores=8,
            expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1),
        ))

    assert resource_manager.reconcile_expired_policies() == 0
    with write_session() as db:
        row = db.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "account", ResourcePolicy.scope_id == account_id,
        ))
        assert row.last_apply_status == "expire_failed"
    assert resource_manager.reconcile_expired_policies() == 1
    assert attempts[-1][1]["cpu_cores"] == 2
    with write_session() as db:
        row = db.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "account", ResourcePolicy.scope_id == account_id,
        ))
        assert row.last_apply_status == "expired"


def test_old_samples_are_compacted_to_one_average_per_hour(isolated_db):
    account_id, _ = _account_and_plan()
    now = dt.datetime.now(dt.timezone.utc)
    hour = now - dt.timedelta(days=2)
    with write_session() as db:
        for minute, cpu in ((1, 10), (10, 20), (45, 30)):
            db.add(ResourceSample(
                account_id=account_id, cpu_pct=cpu, memory_bytes=cpu * 100,
                sampled_at=hour.replace(minute=minute, second=0, microsecond=0),
            ))
    resource_manager._last_compaction = None
    assert resource_manager.compact_samples(now) == 2
    with write_session() as db:
        rows = db.scalars(select(ResourceSample).where(ResourceSample.account_id == account_id)).all()
        assert len(rows) == 1
        assert rows[0].period_seconds == 3600
        assert rows[0].cpu_pct == 20


def test_compaction_preserves_newer_sample_in_boundary_hour(isolated_db):
    account_id, _ = _account_and_plan()
    now = dt.datetime(2026, 9, 26, 18, 30, tzinfo=dt.timezone.utc)
    with write_session() as db:
        for minute in (5, 10, 45):
            db.add(ResourceSample(
                account_id=account_id, cpu_pct=minute, memory_bytes=minute * 100,
                sampled_at=dt.datetime(2026, 9, 25, 18, minute, tzinfo=dt.timezone.utc),
            ))
    resource_manager._last_compaction = None
    assert resource_manager.compact_samples(now) == 1
    with write_session() as db:
        rows = db.scalars(select(ResourceSample).where(
            ResourceSample.account_id == account_id,
        ).order_by(ResourceSample.sampled_at)).all()
        assert len(rows) == 2
        assert rows[0].period_seconds == 3600
        assert rows[1].sampled_at.minute == 45
