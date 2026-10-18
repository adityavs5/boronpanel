import os
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_ftp as hf
from shared.config import settings
from shared.db import write_session
from shared.models import FtpAccount
from shared.validation import ValidationError


@pytest.fixture()
def fake_account_home(tmp_path, monkeypatch):
    home_base = tmp_path / "home"
    home_base.mkdir()
    account_home = home_base / "demo1"
    account_home.mkdir()
    (account_home / "public_html").mkdir()

    outside = tmp_path / "outside_the_jail"
    outside.mkdir()

    monkeypatch.setattr(settings, "home_base", str(home_base))

    import pwd as real_pwd

    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), "/usr/sbin/nologin"))
    monkeypatch.setattr(hf.pwd, "getpwnam", lambda name: fake_pw)
    monkeypatch.setattr(hf.os, "chown", lambda *a, **k: None)  # not running as root in tests
    return {"home": account_home, "outside": outside}


@pytest.fixture()
def stub_pure_ftpd(monkeypatch):
    calls = []
    accounts = {}

    def create_ftp_user(login, uid, gid, path, password):
        calls.append(("create", login, path))
        accounts[login] = path

    def set_path(login, uid, gid, path):
        calls.append(("set_path", login, path))
        accounts[login] = path

    def set_password(login, password):
        calls.append(("set_password", login))

    def delete_ftp_user(login):
        calls.append(("delete", login))
        accounts.pop(login, None)

    def suspend_ftp_user(login):
        calls.append(("suspend", login))

    def unsuspend_ftp_user(login, uid, gid, path):
        calls.append(("unsuspend", login, path))

    def disconnect_ftp_sessions(logins):
        calls.append(("disconnect", tuple(sorted(logins))))
        return len(logins)

    monkeypatch.setattr(hf.ftp, "create_ftp_user", create_ftp_user)
    monkeypatch.setattr(hf.ftp, "set_path", set_path)
    monkeypatch.setattr(hf.ftp, "set_password", set_password)
    monkeypatch.setattr(hf.ftp, "delete_ftp_user", delete_ftp_user)
    monkeypatch.setattr(hf.ftp, "suspend_ftp_user", suspend_ftp_user)
    monkeypatch.setattr(hf.ftp, "unsuspend_ftp_user", unsuspend_ftp_user)
    monkeypatch.setattr(hf.ftp, "disconnect_ftp_sessions", disconnect_ftp_sessions)
    return calls


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def account(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})


def test_create_ftp_account_happy_path(account, fake_account_home, stub_pure_ftpd):
    result = hf.create_ftp_account(
        {"username": "demo1", "label": "designer", "path": "public_html", "password": "Secret123!Pass"}
    )
    assert result["ftp_login"] == "demo1_designer"
    assert result["label"] == "designer"
    assert result["path"] == str(fake_account_home["home"] / "public_html")
    assert ("create", "demo1_designer", str(fake_account_home["home"] / "public_html")) in stub_pure_ftpd


def test_create_ftp_account_creates_missing_path(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "uploads", "path": "public_html/uploads", "password": "Secret123!Pass"})
    assert (fake_account_home["home"] / "public_html" / "uploads").is_dir()


def test_create_ftp_account_rejects_path_escape(account, fake_account_home, stub_pure_ftpd):
    with pytest.raises(ValidationError):
        hf.create_ftp_account({"username": "demo1", "label": "escape", "path": "../outside_the_jail", "password": "Secret123!Pass"})
    assert stub_pure_ftpd == []


def test_create_ftp_account_rejects_symlink_escape(account, fake_account_home, stub_pure_ftpd):
    (fake_account_home["home"] / "escape_link").symlink_to(fake_account_home["outside"])
    with pytest.raises(ValidationError):
        hf.create_ftp_account({"username": "demo1", "label": "escape", "path": "escape_link", "password": "Secret123!Pass"})
    assert stub_pure_ftpd == []


def test_create_ftp_account_rejects_duplicate_label(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    with pytest.raises(RuntimeError):
        hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})


def test_create_ftp_account_deletes_external_user_when_bookkeeping_fails(account, fake_account_home, stub_pure_ftpd, monkeypatch):
    original = hf.write_session
    calls = 0

    @contextmanager
    def fail_row_insert():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("control database unavailable")
        with original() as session:
            yield session

    monkeypatch.setattr(hf, "write_session", fail_row_insert)

    with pytest.raises(RuntimeError, match="control database"):
        hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})

    assert ("create", "demo1_designer", str(fake_account_home["home"])) in stub_pure_ftpd
    assert ("delete", "demo1_designer") in stub_pure_ftpd


def test_list_ftp_accounts(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    result = hf.list_ftp_accounts({"username": "demo1"})["ftp_accounts"]
    assert len(result) == 1
    assert result[0]["label"] == "designer"


def test_set_ftp_path(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    result = hf.set_ftp_path({"username": "demo1", "label": "designer", "path": "public_html"})
    assert result["path"] == str(fake_account_home["home"] / "public_html")
    assert ("set_path", "demo1_designer", str(fake_account_home["home"] / "public_html")) in stub_pure_ftpd


def test_set_ftp_path_rejects_escape(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    with pytest.raises(ValidationError):
        hf.set_ftp_path({"username": "demo1", "label": "designer", "path": "../outside_the_jail"})


def test_change_ftp_password(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    result = hf.change_ftp_password({"username": "demo1", "label": "designer", "password": "NewPassword456!"})
    assert result["status"] == "password_changed"
    assert ("set_password", "demo1_designer") in stub_pure_ftpd


def test_delete_ftp_account(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    result = hf.delete_ftp_account({"username": "demo1", "label": "designer"})
    assert result["status"] == "deleted"
    assert hf.list_ftp_accounts({"username": "demo1"})["ftp_accounts"] == []
    assert ("delete", "demo1_designer") in stub_pure_ftpd


def test_delete_ftp_account_keeps_row_when_external_delete_fails(account, fake_account_home, stub_pure_ftpd, monkeypatch):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})

    def fail_delete(login):
        raise RuntimeError("pure-ftpd unavailable")

    monkeypatch.setattr(hf.ftp, "delete_ftp_user", fail_delete)

    with pytest.raises(RuntimeError, match="pure-ftpd unavailable"):
        hf.delete_ftp_account({"username": "demo1", "label": "designer"})

    assert hf.list_ftp_accounts({"username": "demo1"})["ftp_accounts"][0]["ftp_login"] == "demo1_designer"


def test_delete_ftp_account_not_found(account, fake_account_home, stub_pure_ftpd):
    with pytest.raises(RuntimeError):
        hf.delete_ftp_account({"username": "demo1", "label": "nope"})


def test_terminate_account_removes_ftp_accounts(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    hf.create_ftp_account({"username": "demo1", "label": "backup", "path": "", "password": "Secret456!Pass"})

    from shared.models import Account

    with write_session() as session:
        acct = session.scalar(select(Account).where(Account.username == "demo1"))
        hf.terminate_account_ftp(acct)

    with write_session() as session:
        assert session.scalars(select(FtpAccount)).all() == []
    assert ("delete", "demo1_designer") in stub_pure_ftpd
    assert ("delete", "demo1_backup") in stub_pure_ftpd


def test_another_accounts_ftp_login_does_not_collide(isolated_db, stub_sysops, fake_account_home, stub_pure_ftpd, monkeypatch, tmp_path):
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})

    import pwd as real_pwd

    home_base = tmp_path / "home"
    demo2_home = home_base / "demo2"
    demo2_home.mkdir()

    real_getpwnam = hf.pwd.getpwnam

    def fake_getpwnam(name):
        if name == "demo2":
            return real_pwd.struct_passwd(("demo2", "x", os.getuid(), os.getgid(), "", str(demo2_home), "/usr/sbin/nologin"))
        return real_getpwnam(name)

    monkeypatch.setattr(hf.pwd, "getpwnam", fake_getpwnam)

    hf.create_ftp_account({"username": "demo1", "label": "shared", "path": "", "password": "Secret123!Pass"})
    result = hf.create_ftp_account({"username": "demo2", "label": "shared", "path": "", "password": "Secret456!Pass"})
    assert result["ftp_login"] == "demo2_shared"


def test_ftp_account_suspension_disables_credentials_and_active_sessions(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "Secret123!Pass"})
    from shared.models import Account
    with write_session() as session:
        acct = session.scalar(select(Account).where(Account.username == "demo1"))
        session.expunge(acct)

    hf.set_account_ftp_suspended(acct, True)
    hf.set_account_ftp_suspended(acct, False)

    assert ("suspend", "demo1_designer") in stub_pure_ftpd
    assert ("disconnect", ("demo1", "demo1_designer")) in stub_pure_ftpd
    assert ("unsuspend", "demo1_designer", str(fake_account_home["home"])) in stub_pure_ftpd


def test_create_ftp_account_rejects_suspended_owner(account, fake_account_home, stub_pure_ftpd):
    from shared.models import Account
    with write_session() as session:
        session.scalar(select(Account).where(Account.username == "demo1")).status = "suspended"
    with pytest.raises(RuntimeError, match="suspended"):
        hf.create_ftp_account({"username": "demo1", "label": "late", "path": "", "password": "Secret123!Pass"})
    assert not any(call[:2] == ("create", "demo1_late") for call in stub_pure_ftpd)


def test_puredb_suspension_preserves_existing_ip_restrictions(tmp_path, monkeypatch):
    from daemon import ftp
    calls = []
    monkeypatch.setattr(ftp, "SUSPENSION_STATE_DIR", tmp_path / "suspension")
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = (
            "Allowed local IPs : 192.0.2.4\nAllowed client IPs : 198.51.100.0/24\n"
            if argv[1] == "show" else ""
        )
        return type("Result", (), {"ok": True, "stderr": "", "stdout": output})()
    monkeypatch.setattr(ftp, "run", fake_run)

    ftp.suspend_ftp_user("demo1_designer")
    ftp.unsuspend_ftp_user("demo1_designer", 5001, 5001, "/home/demo1")

    assert calls[0][0] == [ftp.PURE_PW_BIN, "show", "demo1_designer"]
    assert calls[1][0] == [ftp.PURE_PW_BIN, "usermod", "demo1_designer", "-r", ftp.SUSPENDED_IP, "-i", ftp.SUSPENDED_IP, "-m"]
    assert calls[2][0] == [
        ftp.PURE_PW_BIN, "usermod", "demo1_designer",
        "-u", "5001", "-g", "5001", "-d", "/home/demo1",
        "-r", "198.51.100.0/24", "-i", "192.0.2.4", "-m",
    ]
    assert not any((tmp_path / "suspension").iterdir())


def test_disconnect_ftp_sessions_targets_only_valid_matching_processes(monkeypatch):
    from daemon import ftp
    from daemon.procutil import ProcResult
    killed = []
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return ProcResult(argv, 0,
            "101|demo1_one|0|IDLE\n102|other_two|0|IDLE\n103|demo1_one|0|IDLE\n", "")
    monkeypatch.setattr(ftp, "run", fake_run)
    monkeypatch.setattr(ftp, "_is_pure_ftp_process", lambda pid: pid == 101)
    monkeypatch.setattr(ftp.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert ftp.disconnect_ftp_sessions({"demo1_one"}) == 1
    assert calls == [(["/usr/sbin/pure-ftpwho", "-s", "-H"], {"timeout": 10, "output_limit": 256 * 1024})]
    assert killed == [(101, ftp.signal.SIGTERM)]
