import pytest

from daemon import handlers_account as ha
from shared.validation import ValidationError


@pytest.fixture()
def stub_sysops(monkeypatch):
    calls = []

    def create_linux_user(username):
        calls.append(("create_linux_user", username))
        return 5001, 5001

    def set_initial_password(username, password):
        calls.append(("set_initial_password", username))

    def set_quota(username, soft, hard):
        calls.append(("set_quota", username, soft, hard))

    def lock_user(username):
        calls.append(("lock_user", username))

    def unlock_user(username):
        calls.append(("unlock_user", username))

    def delete_linux_user(username):
        calls.append(("delete_linux_user", username))

    def remove_quota(username):
        calls.append(("remove_quota", username))

    monkeypatch.setattr(ha.sysops, "create_linux_user", create_linux_user)
    monkeypatch.setattr(ha.sysops, "set_initial_password", set_initial_password)
    monkeypatch.setattr(ha.sysops, "set_quota", set_quota)
    monkeypatch.setattr(ha.sysops, "lock_user", lock_user)
    monkeypatch.setattr(ha.sysops, "unlock_user", unlock_user)
    monkeypatch.setattr(ha.sysops, "delete_linux_user", delete_linux_user)
    monkeypatch.setattr(ha.sysops, "remove_quota", remove_quota)
    return calls


def test_create_account_happy_path(isolated_db, stub_sysops):
    result = ha.create_account({"username": "demo1", "primary_domain": "demo1.example"})
    assert result["username"] == "demo1"
    assert result["status"] == "active"
    assert result["uid"] == 5001
    assert "initial_password" in result
    assert ("create_linux_user", "demo1") in stub_sysops


def test_create_account_rejects_duplicate(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        ha.create_account({"username": "demo1"})


def test_create_account_rejects_bad_username(isolated_db, stub_sysops):
    with pytest.raises(ValidationError):
        ha.create_account({"username": "Bad_Name"})


def test_create_account_rejects_inverted_quota(isolated_db, stub_sysops):
    with pytest.raises(ValidationError):
        ha.create_account({"username": "demo1", "quota_soft_mb": 5000, "quota_hard_mb": 1000})


def test_suspend_then_unsuspend(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    suspended = ha.suspend_account({"username": "demo1"})
    assert suspended["status"] == "suspended"
    assert ("lock_user", "demo1") in stub_sysops

    active = ha.unsuspend_account({"username": "demo1"})
    assert active["status"] == "active"
    assert ("unlock_user", "demo1") in stub_sysops


def test_suspend_is_idempotent(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.suspend_account({"username": "demo1"})
    result = ha.suspend_account({"username": "demo1"})
    assert result["status"] == "suspended"
    assert stub_sysops.count(("lock_user", "demo1")) == 1


def test_unsuspend_rejects_active_account_silently_idempotent(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    result = ha.unsuspend_account({"username": "demo1"})
    assert result["status"] == "active"
    assert ("unlock_user", "demo1") not in stub_sysops


def test_terminate_runs_hooks_in_order_and_tears_down_linux_user(isolated_db, stub_sysops):
    order = []
    ha.TERMINATE_HOOKS.append(lambda account: order.append(("hook_one", account.username)))
    try:
        ha.create_account({"username": "demo1"})
        result = ha.terminate_account({"username": "demo1"})
        assert result["status"] == "terminated"
        assert order == [("hook_one", "demo1")]
        assert ("delete_linux_user", "demo1") in stub_sysops
        assert ("remove_quota", "demo1") in stub_sysops
    finally:
        ha.TERMINATE_HOOKS.clear()


def test_terminate_continues_past_failing_hook_and_marks_error(isolated_db, stub_sysops):
    def failing_hook(account):
        raise RuntimeError("vhost teardown exploded")

    ha.TERMINATE_HOOKS.append(failing_hook)
    try:
        ha.create_account({"username": "demo1"})
        result = ha.terminate_account({"username": "demo1"})
        assert result["status"] == "error"
        assert "vhost teardown exploded" in result["last_error"]
        # linux user teardown must still have been attempted despite the hook failure
        assert ("delete_linux_user", "demo1") in stub_sysops
    finally:
        ha.TERMINATE_HOOKS.clear()


def test_terminate_unknown_account_raises(isolated_db, stub_sysops):
    with pytest.raises(RuntimeError):
        ha.terminate_account({"username": "ghost"})


def test_list_accounts(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    result = ha.list_accounts({})
    usernames = {a["username"] for a in result["accounts"]}
    assert usernames == {"demo1", "demo2"}


def test_set_php_version_happy_path(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    calls = []
    ha.PHP_VERSION_HOOKS.append(lambda account: calls.append(account.username))
    try:
        result = ha.set_php_version({"username": "demo1", "php_version": "8.2"})
        assert result["php_version"] == "8.2"
        assert calls == ["demo1"]
    finally:
        ha.PHP_VERSION_HOOKS.clear()


def test_set_php_version_rejects_unknown_version(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    with pytest.raises(ValidationError):
        ha.set_php_version({"username": "demo1", "php_version": "7.4"})


def test_set_php_version_is_noop_when_unchanged(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    calls = []
    ha.PHP_VERSION_HOOKS.append(lambda account: calls.append(account.username))
    try:
        ha.set_php_version({"username": "demo1", "php_version": "8.3"})  # default is already 8.3
        assert calls == []
    finally:
        ha.PHP_VERSION_HOOKS.clear()


def test_set_php_version_rejects_terminated_account(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        ha.set_php_version({"username": "demo1", "php_version": "8.2"})


def test_set_php_version_unknown_account_raises(isolated_db, stub_sysops):
    with pytest.raises(RuntimeError):
        ha.set_php_version({"username": "ghost", "php_version": "8.2"})


def test_create_account_uses_default_limits(isolated_db, stub_sysops):
    result = ha.create_account({"username": "demo1"})
    assert result["cpu_pct"] == 25
    assert result["mem_mb"] == 512
    assert result["io_mb"] == 50
    assert result["pids_max"] == 50


def test_create_account_accepts_custom_limits(isolated_db, stub_sysops):
    result = ha.create_account({"username": "demo1", "cpu_pct": 50, "mem_mb": 1024, "io_mb": 100, "pids_max": 100})
    assert result["cpu_pct"] == 50
    assert result["mem_mb"] == 1024
    assert result["io_mb"] == 100
    assert result["pids_max"] == 100


def test_create_account_rejects_invalid_limits(isolated_db, stub_sysops):
    with pytest.raises(ValidationError):
        ha.create_account({"username": "demo1", "cpu_pct": 0})
    with pytest.raises(ValidationError):
        ha.create_account({"username": "demo2", "mem_mb": 32})
    with pytest.raises(ValidationError):
        ha.create_account({"username": "demo3", "pids_max": 1})


def test_create_account_fires_create_hooks(isolated_db, stub_sysops):
    calls = []
    ha.CREATE_HOOKS.append(lambda account: calls.append(account.username))
    try:
        ha.create_account({"username": "demo1"})
        assert calls == ["demo1"]
    finally:
        ha.CREATE_HOOKS.clear()


def test_set_limits_updates_and_fires_hooks(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    calls = []
    ha.LIMITS_HOOKS.append(lambda account: calls.append((account.username, account.cpu_pct)))
    try:
        result = ha.set_limits({"username": "demo1", "cpu_pct": 75, "mem_mb": 2048, "io_mb": 200, "pids_max": 150})
        assert result["cpu_pct"] == 75
        assert result["mem_mb"] == 2048
        assert calls == [("demo1", 75)]
    finally:
        ha.LIMITS_HOOKS.clear()


def test_set_limits_partial_update_keeps_other_fields(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1", "cpu_pct": 25, "mem_mb": 512, "io_mb": 50, "pids_max": 50})
    result = ha.set_limits({"username": "demo1", "cpu_pct": 80})
    assert result["cpu_pct"] == 80
    assert result["mem_mb"] == 512
    assert result["io_mb"] == 50
    assert result["pids_max"] == 50


def test_set_limits_rejects_invalid_values(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    with pytest.raises(ValidationError):
        ha.set_limits({"username": "demo1", "cpu_pct": 200})


def test_set_limits_rejects_unknown_account(isolated_db, stub_sysops):
    with pytest.raises(RuntimeError):
        ha.set_limits({"username": "ghost", "cpu_pct": 50})


def test_set_limits_rejects_terminated_account(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        ha.set_limits({"username": "demo1", "cpu_pct": 50})
