"""Phase 8 feature 2: admin account editor (identity + passwords + rename)."""
import pytest
from sqlalchemy import select

from daemon import identity_admin
from shared.db import write_session
from shared.models import Account, AccountNotificationPrefs, Domain, FtpAccount, NodeApp
from shared.validation import ValidationError


def _make_account(username="demo1", status="active", uid=5001):
    with write_session() as db:
        account = Account(username=username, status=status, uid=uid, gid=uid, primary_domain=None,
                          cpu_pct=25, mem_mb=512, io_mb=50, pids_max=50)
        db.add(account)
        db.flush()
        return account.id


def _add_domain(account_id, domain, kind, docroot):
    with write_session() as db:
        db.add(Domain(account_id=account_id, domain=domain, kind=kind, docroot=docroot))


# --- account password ------------------------------------------------------


def test_set_account_password(isolated_db, monkeypatch):
    _make_account("demo1")
    calls = {}
    monkeypatch.setattr(identity_admin.sysops, "set_initial_password", lambda u, p: calls.setdefault("set", (u, p)))
    monkeypatch.setattr(identity_admin.sysops, "lock_user", lambda u: calls.setdefault("lock", u))
    result = identity_admin.set_account_password({"username": "demo1", "password": "NewStrongPass1!"})
    assert result["status"] == "password_changed"
    assert calls["set"] == ("demo1", "NewStrongPass1!")
    assert "lock" not in calls  # active account, not re-locked


def test_set_account_password_relocks_suspended(isolated_db, monkeypatch):
    _make_account("demo1", status="suspended")
    locked = []
    monkeypatch.setattr(identity_admin.sysops, "set_initial_password", lambda u, p: None)
    monkeypatch.setattr(identity_admin.sysops, "lock_user", lambda u: locked.append(u))
    identity_admin.set_account_password({"username": "demo1", "password": "NewStrongPass1!"})
    assert locked == ["demo1"]  # suspend semantics preserved


def test_set_account_password_rejects_weak(isolated_db):
    _make_account("demo1")
    with pytest.raises(ValidationError):
        identity_admin.set_account_password({"username": "demo1", "password": "weak"})


# --- contact email ---------------------------------------------------------


def test_set_contact_email_creates_prefs(isolated_db):
    account_id = _make_account("demo1")
    identity_admin.set_contact_email({"username": "demo1", "contact_email": "owner@example.com"})
    with write_session() as db:
        prefs = db.scalar(select(AccountNotificationPrefs).where(AccountNotificationPrefs.account_id == account_id))
        assert prefs.customer_email == "owner@example.com"


def test_set_contact_email_clear(isolated_db):
    account_id = _make_account("demo1")
    identity_admin.set_contact_email({"username": "demo1", "contact_email": "owner@example.com"})
    identity_admin.set_contact_email({"username": "demo1", "contact_email": ""})
    with write_session() as db:
        prefs = db.scalar(select(AccountNotificationPrefs).where(AccountNotificationPrefs.account_id == account_id))
        assert prefs.customer_email is None


# --- primary domain --------------------------------------------------------


def test_set_primary_domain_renames(isolated_db, monkeypatch):
    account_id = _make_account("demo1")
    _add_domain(account_id, "old.example.com", "primary", "/home/demo1/public_html")
    monkeypatch.setattr(identity_admin.ols, "refresh_vhost", lambda a: None)
    monkeypatch.setattr(identity_admin, "_remove_vhost_dir", lambda d: None)

    result = identity_admin.set_primary_domain({"username": "demo1", "domain": "new.example.com"})
    assert result["primary_domain"] == "new.example.com"
    assert result["previous"] == "old.example.com"
    with write_session() as db:
        acc = db.get(Account, account_id)
        assert acc.primary_domain == "new.example.com"
        d = db.scalar(select(Domain).where(Domain.account_id == account_id, Domain.kind == "primary"))
        assert d.domain == "new.example.com"
        assert d.docroot == "/home/demo1/public_html"  # docroot unchanged


def test_set_primary_domain_rejects_used_domain(isolated_db):
    a1 = _make_account("demo1")
    _make_account("demo2", uid=5002)
    with write_session() as db:
        acc2 = db.scalar(select(Account).where(Account.username == "demo2"))
        db.add(Domain(account_id=acc2.id, domain="taken.example.com", kind="primary", docroot="/home/demo2/public_html"))
    with pytest.raises(RuntimeError, match="already in use"):
        identity_admin.set_primary_domain({"username": "demo1", "domain": "taken.example.com"})


# --- username rename (the xhigh saga) --------------------------------------


def _patch_system(monkeypatch, fail_on=None):
    """Patch every system-mutating call so the rename saga runs against the
    isolated DB only. `fail_on` names a step that should raise, to exercise
    rollback."""
    log = []

    def rec(name):
        def _fn(*a, **k):
            log.append((name, a))
            if fail_on == name:
                raise RuntimeError(f"boom in {name}")
        return _fn

    monkeypatch.setattr(identity_admin.sysops, "user_exists", lambda u: False)
    monkeypatch.setattr(identity_admin.sysops, "rename_login", rec("rename_login"))
    monkeypatch.setattr(identity_admin.sysops, "rename_group", rec("rename_group"))
    monkeypatch.setattr(identity_admin.sysops, "move_home", rec("move_home"))
    monkeypatch.setattr(identity_admin.sysops, "recycle_php_workers", rec("recycle"))
    monkeypatch.setattr(identity_admin.cgroups, "remove_slice", rec("remove_slice"))
    monkeypatch.setattr(identity_admin.cgroups, "apply_limits", rec("apply_limits"))
    monkeypatch.setattr(identity_admin.ols, "refresh_vhost", rec("refresh_vhost"))
    return log


def test_rename_happy_path(isolated_db, monkeypatch):
    account_id = _make_account("oldname")
    _add_domain(account_id, "site.com", "primary", "/home/oldname/public_html")
    _add_domain(account_id, "addon.com", "addon", "/home/oldname/addon.com")
    log = _patch_system(monkeypatch)

    result = identity_admin.rename_account({"username": "oldname", "new_username": "newname"})
    assert result == {"old_username": "oldname", "new_username": "newname", "status": "active"}

    with write_session() as db:
        acc = db.get(Account, account_id)
        assert acc.username == "newname"
        docroots = {d.domain: d.docroot for d in db.scalars(select(Domain).where(Domain.account_id == account_id)).all()}
        assert docroots["site.com"] == "/home/newname/public_html"
        assert docroots["addon.com"] == "/home/newname/addon.com"

    names = [c[0] for c in log]
    # Linux rename before home move before OLS refresh
    assert names.index("rename_login") < names.index("move_home") < names.index("refresh_vhost")


def test_rename_rejects_duplicate(isolated_db, monkeypatch):
    _make_account("oldname")
    _make_account("taken", uid=5002)
    _patch_system(monkeypatch)
    with pytest.raises(RuntimeError, match="already exists"):
        identity_admin.rename_account({"username": "oldname", "new_username": "taken"})


def test_rename_rejects_same_name(isolated_db):
    _make_account("oldname")
    with pytest.raises(ValidationError):
        identity_admin.rename_account({"username": "oldname", "new_username": "oldname"})


def test_rename_refuses_with_node_app(isolated_db, monkeypatch):
    account_id = _make_account("oldname")
    with write_session() as db:
        db.add(NodeApp(account_id=account_id, domain="app.com", name="app", entry_point="index.js",
                       port=30001, node_version="20"))
    _patch_system(monkeypatch)
    with pytest.raises(RuntimeError, match="NodeJS/Python"):
        identity_admin.rename_account({"username": "oldname", "new_username": "newname"})


def test_rename_refuses_with_ftp_subaccount(isolated_db, monkeypatch):
    account_id = _make_account("oldname")
    with write_session() as db:
        db.add(FtpAccount(account_id=account_id, ftp_login="oldname_sub", path="/home/oldname/pub"))
    _patch_system(monkeypatch)
    with pytest.raises(RuntimeError, match="FTP sub-accounts"):
        identity_admin.rename_account({"username": "oldname", "new_username": "newname"})


def test_rename_rolls_back_on_ols_failure(isolated_db, monkeypatch):
    """If the final OLS refresh fails, every earlier step must be undone and
    the account left with its ORIGINAL username -- the atomicity guarantee."""
    account_id = _make_account("oldname")
    _add_domain(account_id, "site.com", "primary", "/home/oldname/public_html")
    log = _patch_system(monkeypatch, fail_on="refresh_vhost")

    with pytest.raises(RuntimeError, match="rolled back"):
        identity_admin.rename_account({"username": "oldname", "new_username": "newname"})

    # DB restored to the original username + docroot
    with write_session() as db:
        acc = db.get(Account, account_id)
        assert acc.username == "oldname"
        d = db.scalar(select(Domain).where(Domain.account_id == account_id, Domain.kind == "primary"))
        assert d.docroot == "/home/oldname/public_html"

    names = [c[0] for c in log]
    # Compensations ran: rename_login called forward (old->new) AND back (new->old)
    rename_login_calls = [c for c in log if c[0] == "rename_login"]
    assert len(rename_login_calls) == 2
    assert rename_login_calls[0][1] == ("oldname", "newname")
    assert rename_login_calls[1][1] == ("newname", "oldname")
