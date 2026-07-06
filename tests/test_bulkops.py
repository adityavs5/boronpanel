"""Phase 8 feature 12: bulk account operations (async, stop on first failure)."""
import pytest

from daemon import bulkops
from shared.db import write_session
from shared.models import Account, BulkActionJob
from shared.validation import ValidationError


def _accounts(*usernames):
    with write_session() as db:
        for i, u in enumerate(usernames):
            db.add(Account(username=u, status="active", uid=5000 + i, gid=5000 + i))


def _sync_executor(monkeypatch):
    class _Sync:
        def submit(self, fn, *a, **k):
            fn(*a, **k)
    monkeypatch.setattr(bulkops, "_executor", _Sync())


def test_trigger_rejects_bad_action(isolated_db):
    with pytest.raises(ValidationError):
        bulkops.trigger_bulk_action({"action": "explode", "usernames": ["a"]})


def test_trigger_rejects_empty_usernames(isolated_db):
    with pytest.raises(ValidationError):
        bulkops.trigger_bulk_action({"action": "suspend", "usernames": []})


def test_notify_requires_subject_and_body(isolated_db):
    with pytest.raises(ValidationError):
        bulkops.trigger_bulk_action({"action": "notify", "usernames": ["a"], "action_params": {"subject": "hi"}})


def test_bulk_suspend_two_accounts(isolated_db, monkeypatch):
    _accounts("acc1", "acc2")
    _sync_executor(monkeypatch)
    suspended = []
    monkeypatch.setattr(bulkops.handlers_account, "suspend_account", lambda p: suspended.append(p["username"]))

    job = bulkops.trigger_bulk_action({"action": "suspend", "usernames": ["acc1", "acc2"]})
    final = bulkops.get_bulk_action({"job_id": job["id"]})
    assert final["status"] == "completed"
    assert final["completed_count"] == 2
    assert suspended == ["acc1", "acc2"]
    assert all(r["ok"] for r in final["results"])


def test_bulk_stops_on_first_failure(isolated_db, monkeypatch):
    _accounts("acc1", "acc2", "acc3")
    _sync_executor(monkeypatch)

    def suspend(p):
        if p["username"] == "acc2":
            raise RuntimeError("acc2 boom")
    monkeypatch.setattr(bulkops.handlers_account, "suspend_account", suspend)

    job = bulkops.trigger_bulk_action({"action": "suspend", "usernames": ["acc1", "acc2", "acc3"]})
    final = bulkops.get_bulk_action({"job_id": job["id"]})
    assert final["status"] == "failed"
    # acc1 ok, acc2 failed, acc3 NEVER attempted (stop on first failure)
    assert [r["username"] for r in final["results"]] == ["acc1", "acc2"]
    assert final["results"][0]["ok"] is True
    assert final["results"][1]["ok"] is False
    assert "acc2" in final["error"]


def test_bulk_notify_uses_send_direct(isolated_db, monkeypatch):
    _accounts("acc1")
    _sync_executor(monkeypatch)
    sent = []
    monkeypatch.setattr(bulkops.notifications, "send_direct",
                        lambda account, subject, body: sent.append((account.username, subject)) or True)
    job = bulkops.trigger_bulk_action({
        "action": "notify", "usernames": ["acc1"],
        "action_params": {"subject": "Maintenance", "body": "Tonight at 2am"},
    })
    final = bulkops.get_bulk_action({"job_id": job["id"]})
    assert final["status"] == "completed"
    assert sent == [("acc1", "Maintenance")]


def test_bulk_update_limits(isolated_db, monkeypatch):
    _accounts("acc1")
    _sync_executor(monkeypatch)
    applied = []
    monkeypatch.setattr(bulkops.handlers_account, "set_limits", lambda p: applied.append(p))
    job = bulkops.trigger_bulk_action({
        "action": "update_limits", "usernames": ["acc1"], "action_params": {"mem_mb": 1024},
    })
    final = bulkops.get_bulk_action({"job_id": job["id"]})
    assert final["status"] == "completed"
    assert applied[0]["mem_mb"] == 1024
