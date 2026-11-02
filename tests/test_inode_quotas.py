"""Inode limits: warning period, what the filesystem quota carries, upgrade."""
import datetime as dt

from daemon import inode_quotas
from shared.db import write_session
from shared.models import Account, utcnow


def _account(session, **values):
    row = Account(username=values.pop("username", "demo1"), status="active", uid=5001, gid=5001,
                  quota_soft_mb=1024, quota_hard_mb=2048, **values)
    session.add(row)
    session.flush()
    return row


def test_quota_carries_inode_limits_only_once_enforced(isolated_db):
    with write_session() as session:
        enforced = _account(session, username="demo1", inode_soft_limit=90, inode_hard_limit=100)
        warning = _account(session, username="demo2", inode_soft_limit=90, inode_hard_limit=100,
                           inode_enforce_at=utcnow() + dt.timedelta(days=3))
        unlimited = _account(session, username="demo3")
        assert inode_quotas.in_force(enforced) == (90, 100)
        assert inode_quotas.in_force(warning) == (0, 0)
        assert inode_quotas.in_force(unlimited) == (0, 0)


def test_raising_enforced_limits_skips_the_warning_period(isolated_db):
    with write_session() as session:
        account = _account(session, inode_soft_limit=90, inode_hard_limit=100)
        inode_quotas.schedule(account, 180, 200)
        assert inode_quotas.enforcement_state(account) == "enforced"
        inode_quotas.schedule(account, 50, 60)
        assert inode_quotas.enforcement_state(account) == "warning"
        # Limits that were never enforced keep the warning period on a raise.
        inode_quotas.schedule(account, 500, 600)
        assert inode_quotas.enforcement_state(account) == "warning"


def test_enforce_due_can_target_one_account(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(inode_quotas.sysops, "set_quota", lambda *args, **kwargs: calls.append(args[0]))
    with write_session() as session:
        for name in ("demo1", "demo2"):
            _account(session, username=name, inode_soft_limit=90, inode_hard_limit=100, inode_enforce_at=utcnow())
    assert inode_quotas.enforce_due({"username": "demo2"})["applied"] == ["demo2"]
    assert calls == ["demo2"]


def test_upgrade_puts_enforced_inode_limits_back_on_the_quota(isolated_db, monkeypatch):
    """Older releases reactivated restored accounts with a block-only quota."""
    calls = []
    monkeypatch.setattr(inode_quotas.sysops, "set_quota", lambda *args, **kwargs: calls.append((args, kwargs)))
    with write_session() as session:
        _account(session, username="demo1", inode_soft_limit=90, inode_hard_limit=100)
        _account(session, username="demo2", inode_soft_limit=90, inode_hard_limit=100,
                 inode_enforce_at=utcnow() + dt.timedelta(days=3))
        gone = _account(session, username="demo3", inode_soft_limit=90, inode_hard_limit=100)
        gone.status = "terminated"
    assert inode_quotas.reapply_enforced() == 1
    assert calls == [(("demo1", 1024, 2048), {"inode_soft": 90, "inode_hard": 100})]
