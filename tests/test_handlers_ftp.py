import os

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

    monkeypatch.setattr(hf.ftp, "create_ftp_user", create_ftp_user)
    monkeypatch.setattr(hf.ftp, "set_path", set_path)
    monkeypatch.setattr(hf.ftp, "set_password", set_password)
    monkeypatch.setattr(hf.ftp, "delete_ftp_user", delete_ftp_user)
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
        {"username": "demo1", "label": "designer", "path": "public_html", "password": "secret123"}
    )
    assert result["ftp_login"] == "demo1_designer"
    assert result["label"] == "designer"
    assert result["path"] == str(fake_account_home["home"] / "public_html")
    assert ("create", "demo1_designer", str(fake_account_home["home"] / "public_html")) in stub_pure_ftpd


def test_create_ftp_account_creates_missing_path(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "uploads", "path": "public_html/uploads", "password": "secret123"})
    assert (fake_account_home["home"] / "public_html" / "uploads").is_dir()


def test_create_ftp_account_rejects_path_escape(account, fake_account_home, stub_pure_ftpd):
    with pytest.raises(ValidationError):
        hf.create_ftp_account({"username": "demo1", "label": "escape", "path": "../outside_the_jail", "password": "secret123"})
    assert stub_pure_ftpd == []


def test_create_ftp_account_rejects_symlink_escape(account, fake_account_home, stub_pure_ftpd):
    (fake_account_home["home"] / "escape_link").symlink_to(fake_account_home["outside"])
    with pytest.raises(ValidationError):
        hf.create_ftp_account({"username": "demo1", "label": "escape", "path": "escape_link", "password": "secret123"})
    assert stub_pure_ftpd == []


def test_create_ftp_account_rejects_duplicate_label(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})
    with pytest.raises(RuntimeError):
        hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})


def test_list_ftp_accounts(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})
    result = hf.list_ftp_accounts({"username": "demo1"})["ftp_accounts"]
    assert len(result) == 1
    assert result[0]["label"] == "designer"


def test_set_ftp_path(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})
    result = hf.set_ftp_path({"username": "demo1", "label": "designer", "path": "public_html"})
    assert result["path"] == str(fake_account_home["home"] / "public_html")
    assert ("set_path", "demo1_designer", str(fake_account_home["home"] / "public_html")) in stub_pure_ftpd


def test_set_ftp_path_rejects_escape(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})
    with pytest.raises(ValidationError):
        hf.set_ftp_path({"username": "demo1", "label": "designer", "path": "../outside_the_jail"})


def test_change_ftp_password(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})
    result = hf.change_ftp_password({"username": "demo1", "label": "designer", "password": "newpass456"})
    assert result["status"] == "password_changed"
    assert ("set_password", "demo1_designer") in stub_pure_ftpd


def test_delete_ftp_account(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})
    result = hf.delete_ftp_account({"username": "demo1", "label": "designer"})
    assert result["status"] == "deleted"
    assert hf.list_ftp_accounts({"username": "demo1"})["ftp_accounts"] == []
    assert ("delete", "demo1_designer") in stub_pure_ftpd


def test_delete_ftp_account_not_found(account, fake_account_home, stub_pure_ftpd):
    with pytest.raises(RuntimeError):
        hf.delete_ftp_account({"username": "demo1", "label": "nope"})


def test_terminate_account_removes_ftp_accounts(account, fake_account_home, stub_pure_ftpd):
    hf.create_ftp_account({"username": "demo1", "label": "designer", "path": "", "password": "secret123"})
    hf.create_ftp_account({"username": "demo1", "label": "backup", "path": "", "password": "secret456"})

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

    hf.create_ftp_account({"username": "demo1", "label": "shared", "path": "", "password": "secret123"})
    result = hf.create_ftp_account({"username": "demo2", "label": "shared", "path": "", "password": "secret456"})
    assert result["ftp_login"] == "demo2_shared"
