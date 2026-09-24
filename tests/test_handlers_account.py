import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError


@pytest.fixture()
def stub_sysops(monkeypatch):
    calls = []
    # Importing the RPC registry during collection registers real system
    # hooks. This module tests hook dispatch with its own synthetic callbacks.
    for name in ("CREATE_HOOKS", "LIMITS_HOOKS", "SUSPEND_HOOKS", "UNSUSPEND_HOOKS", "TERMINATE_HOOKS"):
        monkeypatch.setattr(ha, name, [])

    def create_linux_user(username):
        calls.append(("create_linux_user", username))
        return 5001, 5001

    def set_initial_password(username, password):
        calls.append(("set_initial_password", username, password))

    def set_quota(username, soft, hard):
        calls.append(("set_quota", username, soft, hard))

    def lock_user(username):
        calls.append(("lock_user", username))

    def unlock_user(username):
        calls.append(("unlock_user", username))

    def delete_linux_user(username, *, remove_home=True):
        calls.append(("delete_linux_user", username))
        if not remove_home:
            calls.append(("preserve_home", username))

    def remove_quota(username):
        calls.append(("remove_quota", username))

    monkeypatch.setattr(ha.sysops, "create_linux_user", create_linux_user)
    monkeypatch.setattr(ha.sysops, "set_initial_password", set_initial_password)
    monkeypatch.setattr(ha.sysops, "set_quota", set_quota)
    monkeypatch.setattr(ha.sysops, "lock_user", lock_user)
    monkeypatch.setattr(ha.sysops, "unlock_user", unlock_user)
    monkeypatch.setattr(ha.sysops, "delete_linux_user", delete_linux_user)
    monkeypatch.setattr(ha.sysops, "remove_quota", remove_quota)
    monkeypatch.setattr(ha.handlers_domain, "ensure_docroot", lambda username, docroot, domain_name=None: None)
    monkeypatch.setattr(ha.ols, "provision_vhost", lambda account: None)
    return calls


def test_create_account_happy_path(isolated_db, stub_sysops):
    result = ha.create_account({"username": "demo1", "primary_domain": "demo1.example"})
    assert result["username"] == "demo1"
    assert result["status"] == "active"
    assert result["uid"] == 5001
    assert "initial_password" in result
    assert ("create_linux_user", "demo1") in stub_sysops

    with write_session() as session:
        domain = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert domain is not None
        assert domain.account_id == result["id"]
        assert domain.kind == "primary"
        assert domain.docroot == "/home/demo1/public_html"


def test_create_account_compensates_primary_domain_when_ols_fails(isolated_db, stub_sysops, monkeypatch):
    def boom(account):
        raise RuntimeError("openlitespeed -t failed")

    monkeypatch.setattr(ha.ols, "provision_vhost", boom)

    with pytest.raises(RuntimeError, match="openlitespeed -t failed"):
        ha.create_account({"username": "demo1", "primary_domain": "demo1.example"})

    with write_session() as session:
        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        assert account is not None
        assert account.primary_domain is None
        assert session.scalar(select(Domain).where(Domain.domain == "demo1.example")) is None


def test_create_account_rejects_duplicate_primary_domain(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1", "primary_domain": "shared.example"})

    with pytest.raises(RuntimeError, match="domain 'shared.example' is already in use"):
        ha.create_account({"username": "demo2", "primary_domain": "shared.example"})


def test_create_account_stores_contact_email_in_notification_prefs(isolated_db, stub_sysops):
    """QA round 2, item 15: an admin-supplied contact email at creation
    lands in AccountNotificationPrefs.customer_email -- the same field the
    account.created welcome email (and every other notification) reads
    from -- rather than a duplicate email column on Account itself."""
    from daemon import notifications
    from shared.models import Account

    result = ha.create_account({"username": "demo1", "email": "owner@example.com"})
    assert result["email"] == "owner@example.com"

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        prefs = notifications.get_prefs({"username": "demo1"})
        assert prefs["customer_email"] == "owner@example.com"
        assert account is not None  # sanity: the account row itself still exists


def test_create_account_without_email_leaves_notification_prefs_unset(isolated_db, stub_sysops):
    from daemon import notifications

    ha.create_account({"username": "demo1"})
    prefs = notifications.get_prefs({"username": "demo1"})
    assert prefs["customer_email"] is None


def test_create_account_rejects_invalid_email(isolated_db, stub_sysops):
    with pytest.raises(ValidationError):
        ha.create_account({"username": "demo1", "email": "not-an-email"})


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
        # Preserve the identity until every service has been removed.
        assert ("delete_linux_user", "demo1") not in stub_sysops
        assert ("remove_quota", "demo1") not in stub_sysops
    finally:
        ha.TERMINATE_HOOKS.clear()


def test_terminate_unknown_account_raises(isolated_db, stub_sysops):
    with pytest.raises(RuntimeError):
        ha.terminate_account({"username": "ghost"})


# Security audit finding F3: terminate_account revokes that account's
# panel login(s) -- flagged twice before (Phase 4-0b, Phase 4-12) as an
# observed gap and never fixed until this audit.
def _setup_panel_access_for(username: str) -> dict:
    from daemon import handlers_auth as hauth
    from shared.models import Account

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        account_id = account.id

    panel_user = hauth.create_panel_user(
        {"username": f"{username}-login", "password": "SuperSecret123!", "role": "customer", "account_id": account_id}
    )
    session_result = hauth.create_session({"panel_user_id": panel_user["id"]})
    token_result = hauth.create_api_token({"label": f"{username}-token", "role": "customer", "account_id": account_id})
    return {"panel_user": panel_user, "session": session_result, "token": token_result}


def test_terminate_disables_panel_user_and_revokes_sessions_and_tokens(isolated_db, stub_sysops):
    from shared.models import ApiToken, PanelUser
    from shared.models import Session as SessionModel

    ha.create_account({"username": "demo1"})
    access = _setup_panel_access_for("demo1")

    ha.terminate_account({"username": "demo1"})

    with write_session() as session:
        panel_user = session.get(PanelUser, access["panel_user"]["id"])
        assert panel_user.disabled is True
        from shared.session_ids import session_digest
        session_row = session.scalar(select(SessionModel).where(SessionModel.session_id == session_digest(access["session"]["session_id"])))
        assert session_row.revoked is True
        token_row = session.get(ApiToken, access["token"]["id"])
        assert token_row.revoked_at is not None


def test_terminate_revokes_panel_access_even_on_partial_failure(isolated_db, stub_sysops):
    def failing_hook(account):
        raise RuntimeError("vhost teardown exploded")

    ha.TERMINATE_HOOKS.append(failing_hook)
    try:
        ha.create_account({"username": "demo1"})
        access = _setup_panel_access_for("demo1")
        result = ha.terminate_account({"username": "demo1"})
        assert result["status"] == "error"

        from shared.models import PanelUser

        with write_session() as session:
            panel_user = session.get(PanelUser, access["panel_user"]["id"])
            assert panel_user.disabled is True
    finally:
        ha.TERMINATE_HOOKS.clear()


def test_reactivate_account_re_enables_panel_user(isolated_db, stub_sysops):
    from shared.models import PanelUser

    ha.create_account({"username": "demo1"})
    access = _setup_panel_access_for("demo1")
    ha.terminate_account({"username": "demo1"})

    with write_session() as session:
        panel_user = session.get(PanelUser, access["panel_user"]["id"])
        assert panel_user.disabled is True

    ha.reactivate_account({"username": "demo1"})

    with write_session() as session:
        panel_user = session.get(PanelUser, access["panel_user"]["id"])
        assert panel_user.disabled is False


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
    result = ha.create_account({"username": "demo1", "cpu_pct": 200, "mem_mb": 1024, "io_mb": 100, "pids_max": 100})
    assert result["cpu_pct"] == 200
    assert result["cpu_cores"] == 2
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


def test_create_account_stashes_initial_password_for_create_hooks(isolated_db, stub_sysops):
    """Phase 7b feature 3: the "account created" notification needs the
    plaintext initial password, which exists only in this function's local
    scope (never persisted) -- confirms CREATE_HOOKS callables can read it
    off the account row passed to them, and that a fixed custom password
    (not just an auto-generated one) is stashed identically."""
    captured = []
    ha.CREATE_HOOKS.append(lambda account: captured.append(getattr(account, "initial_password", "MISSING")))
    try:
        result = ha.create_account({"username": "demo1", "password": "Str0ng!Passw0rdXY"})
        assert captured == ["Str0ng!Passw0rdXY"]
        assert result["initial_password"] == "Str0ng!Passw0rdXY"
    finally:
        ha.CREATE_HOOKS.clear()


def test_reactivate_account_stashes_initial_password_for_create_hooks(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})

    captured = []
    ha.CREATE_HOOKS.append(lambda account: captured.append(getattr(account, "initial_password", "MISSING")))
    try:
        result = ha.reactivate_account({"username": "demo1"})
        assert captured == [result["initial_password"]]
        assert captured[0] != "MISSING"
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
        ha.set_limits({"username": "demo1", "cpu_pct": 25601})


def test_set_limits_rejects_unknown_account(isolated_db, stub_sysops):
    with pytest.raises(RuntimeError):
        ha.set_limits({"username": "ghost", "cpu_pct": 50})


def test_set_limits_rejects_terminated_account(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        ha.set_limits({"username": "demo1", "cpu_pct": 50})


def test_reactivate_account_recreates_linux_user_and_flips_status(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})

    result = ha.reactivate_account({"username": "demo1", "php_version": "8.2", "cpu_pct": 40})
    assert result["status"] == "active"
    assert result["php_version"] == "8.2"
    assert result["cpu_pct"] == 40
    assert ("create_linux_user", "demo1") in stub_sysops

    with write_session() as session:
        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        assert account.status == "active"
        assert account.terminated_at is None


def test_reactivate_account_fires_create_hooks(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})
    calls = []
    ha.CREATE_HOOKS.append(lambda account: calls.append(account.username))
    try:
        ha.reactivate_account({"username": "demo1"})
        assert calls == ["demo1"]
    finally:
        ha.CREATE_HOOKS.clear()


def test_reactivate_account_rejects_unknown_account(isolated_db, stub_sysops):
    with pytest.raises(RuntimeError):
        ha.reactivate_account({"username": "ghost"})


def test_reactivate_account_rejects_active_account(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        ha.reactivate_account({"username": "demo1"})


# --- password strength enforcement (Phase 4 feature 12: codebase-wide audit) --
# Real gap found: a custom `password` param was previously used as-is with no
# strength check at all -- only the auto-generated fallback was safe by
# construction. Fixed in daemon/handlers_account.py; covered here.


def test_create_account_rejects_weak_custom_password(isolated_db, stub_sysops):
    with pytest.raises(ValidationError):
        ha.create_account({"username": "demo1", "password": "short1!"})


def test_create_account_rejects_weak_password_before_creating_linux_user(isolated_db, stub_sysops):
    """Real ordering bug found live: create_linux_user ran before password
    validation, so a rejected weak password still left an orphaned Linux
    user behind (with no corresponding account DB row). Fixed by moving
    validation ahead of create_linux_user -- covered here by asserting
    create_linux_user is never even called when the password is rejected."""
    with pytest.raises(ValidationError):
        ha.create_account({"username": "demo1", "password": "short1!"})
    assert not any(c[0] == "create_linux_user" for c in stub_sysops)


def test_create_account_accepts_strong_custom_password(isolated_db, stub_sysops):
    result = ha.create_account({"username": "demo1", "password": "MyStr0ngPass!word"})
    assert result["username"] == "demo1"


def test_create_account_auto_generated_password_is_strong(isolated_db, stub_sysops):
    from shared.validation import validate_password_strength

    ha.create_account({"username": "demo1"})
    generated = [c for c in stub_sysops if c[0] == "set_initial_password"]
    assert len(generated) == 1
    validate_password_strength(generated[0][2])  # must not raise


def test_reactivate_account_rejects_weak_custom_password(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})
    with pytest.raises(ValidationError):
        ha.reactivate_account({"username": "demo1", "password": "weak"})


def test_reactivate_account_rejects_weak_password_before_creating_linux_user(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})
    calls_before = len(stub_sysops)
    with pytest.raises(ValidationError):
        ha.reactivate_account({"username": "demo1", "password": "weak"})
    assert not any(c[0] == "create_linux_user" for c in stub_sysops[calls_before:])


@pytest.mark.parametrize('model_name', ['BackupJob', 'RestoreJob', 'SnapshotRun', 'SnapshotRestore'])
@pytest.mark.parametrize('status', ['pending', 'running'])
def test_termination_retains_resources_during_backup_recovery(isolated_db, stub_sysops, model_name, status):
    from shared import models
    ha.create_account({'username': 'demo1'})
    with write_session() as session:
        account = session.scalar(select(models.Account).where(models.Account.username == 'demo1'))
        destination = models.BackupDestination(name='local', kind='local', local_path='/tmp/fixture')
        snapshot_destination = models.SnapshotDestination(name='snapshots', kind='local', path='/tmp/fixture', namespace='fixture')
        session.add_all([destination, snapshot_destination]); session.flush()
        policy = models.SnapshotPolicy(name='test', destination_id=snapshot_destination.id)
        backup = models.BackupJob(account_id=account.id, destination_id=destination.id, kind='full', status='completed')
        session.add_all([policy, backup]); session.flush()
        run = models.SnapshotRun(account_id=account.id, destination_id=snapshot_destination.id, policy_id=policy.id, status='completed')
        session.add(run); session.flush()
        restore = models.RestoreJob(account_id=account.id, backup_job_id=backup.id, kind='full', status='completed')
        snapshot_restore = models.SnapshotRestore(account_id=account.id, run_id=run.id, selection={'kind':'mail'}, status='completed')
        session.add_all([restore, snapshot_restore]); session.flush()
        rows = {type(row).__name__: row for row in (backup, restore, run, snapshot_restore)}
        target = rows[model_name]
        target.status = status
        ident = target.id
    with pytest.raises(ValidationError, match='recovery before termination'):
        ha.terminate_account({'username': 'demo1'})
    assert ('delete_linux_user', 'demo1') not in stub_sysops
    assert ('remove_quota', 'demo1') not in stub_sysops
    with write_session() as session:
        assert session.scalar(select(models.Account).where(models.Account.username == 'demo1')).status == 'active'
        session.get(getattr(models, model_name), ident).status = 'completed'
    assert ha.terminate_account({'username': 'demo1'})['status'] == 'terminated'


def test_termination_waits_for_live_account_worker(isolated_db, stub_sysops):
    from daemon import snapshot_jobs as jobs
    from shared.models import Account
    ha.create_account({'username': 'demo1'})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == 'demo1'))
    with jobs.lock(f'account-{account.id}'):
        with pytest.raises(ValidationError, match='using this account'):
            ha.terminate_account({'username': 'demo1'})
    assert ('delete_linux_user', 'demo1') not in stub_sysops
    assert ha.terminate_account({'username': 'demo1'})['status'] == 'terminated'


@pytest.mark.parametrize('reactivate', [False, True])
def test_quota_failure_never_activates_account(isolated_db, stub_sysops, monkeypatch, reactivate):
    if reactivate:
        with write_session() as session:
            session.add(Account(username='demo1', status='terminated', uid=5001, gid=5001))
    def fail(*args):
        raise RuntimeError('quota unavailable')
    monkeypatch.setattr(ha.sysops, 'set_quota', fail)
    with pytest.raises(RuntimeError, match='quota unavailable'):
        (ha.reactivate_account if reactivate else ha.create_account)({'username': 'demo1'})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == 'demo1'))
        assert account is None or account.status == 'terminated'
    assert ('delete_linux_user', 'demo1') in stub_sysops

    assert ('preserve_home', 'demo1') in stub_sysops
