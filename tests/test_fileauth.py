import pytest

from daemon import fileauth
from daemon.safeio import UnsafePathError
from shared.validation import ValidationError


@pytest.fixture()
def account_with_docroot(isolated_db, tmp_path, monkeypatch):
    """A real account + Domain row + real docroot directory on disk (with
    a real subdirectory to protect) -- fileauth's own jail check
    (daemon/filemanager.py's _resolve) needs a real filesystem, and
    enable_protection creates a real .htpasswd file, so this can't be
    faked with pure DB rows alone."""
    from shared.db import write_session
    from shared.models import Account, Domain

    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(fileauth.filemanager.settings, "home_base", str(home_base))
    monkeypatch.setattr(fileauth.ols, "refresh_vhost", lambda account: None)

    account_home = home_base / "demo1"
    docroot = account_home / "public_html"
    (docroot / "members").mkdir(parents=True)
    (docroot / "members" / "index.html").write_text("secret")

    def fake_getpwnam(name):
        import collections
        pw = collections.namedtuple("pw", ["pw_uid", "pw_gid"])
        return pw(pw_uid=5001, pw_gid=5001)

    monkeypatch.setattr(fileauth.pwd, "getpwnam", fake_getpwnam)
    monkeypatch.setattr(fileauth.os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(fileauth.os, "fchown", lambda *a, **k: None)

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(docroot)))

    return {"docroot": docroot, "home": account_home}


def test_enable_protection_creates_row_and_htpasswd(account_with_docroot):
    result = fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})
    assert result["status"] == "protected"
    assert result["path"] == "public_html/members"
    htpasswd = account_with_docroot["docroot"] / "members" / ".htpasswd"
    assert htpasswd.exists()
    assert htpasswd.read_text() == ""


def test_enable_protection_rejects_duplicate(account_with_docroot):
    fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})
    with pytest.raises(ValidationError):
        fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})


def test_enable_protection_rejects_docroot_itself(account_with_docroot):
    with pytest.raises(fileauth.FileAuthError):
        fileauth.enable_protection({"username": "demo1", "path": "public_html"})


def test_enable_protection_rejects_dir_outside_any_docroot(account_with_docroot):
    (account_with_docroot["home"] / "logs").mkdir(exist_ok=True)
    with pytest.raises(fileauth.FileAuthError):
        fileauth.enable_protection({"username": "demo1", "path": "logs"})


def test_enable_protection_rejects_nonexistent_dir(account_with_docroot):
    with pytest.raises(fileauth.FileAuthError):
        fileauth.enable_protection({"username": "demo1", "path": "public_html/does-not-exist"})


def test_enable_protection_rejects_preplanted_htpasswd_symlink_without_row(account_with_docroot):
    target = account_with_docroot["home"] / "target"
    target.write_text("do-not-touch")
    htpasswd = account_with_docroot["docroot"] / "members" / ".htpasswd"
    htpasswd.symlink_to(target)

    with pytest.raises(UnsafePathError):
        fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})

    assert target.read_text() == "do-not-touch"
    assert fileauth.list_protected_dirs({"username": "demo1"})["protected"] == []


def test_disable_protection_removes_row_but_keeps_htpasswd(account_with_docroot):
    fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})
    htpasswd = account_with_docroot["docroot"] / "members" / ".htpasswd"
    htpasswd.write_text("alice:somehash\n")

    result = fileauth.disable_protection({"username": "demo1", "path": "public_html/members"})
    assert result["status"] == "unprotected"
    assert fileauth.list_protected_dirs({"username": "demo1"})["protected"] == []
    assert htpasswd.exists()  # content never destroyed


def test_disable_protection_rejects_not_currently_protected(account_with_docroot):
    with pytest.raises(fileauth.FileAuthError):
        fileauth.disable_protection({"username": "demo1", "path": "public_html/members"})


def test_add_user_requires_protection_enabled_first(account_with_docroot):
    with pytest.raises(fileauth.FileAuthError):
        fileauth.add_user({"username": "demo1", "path": "public_html/members", "htuser": "alice", "password": "Whatever123!Pass"})


def test_add_user_requires_enabled_row_even_if_htpasswd_exists(account_with_docroot):
    htpasswd = account_with_docroot["docroot"] / "members" / ".htpasswd"
    htpasswd.write_text("")

    with pytest.raises(fileauth.FileAuthError):
        fileauth.add_user({"username": "demo1", "path": "public_html/members", "htuser": "alice", "password": "Whatever123!Pass"})

    assert htpasswd.read_text() == ""


def test_add_list_delete_user_real_htpasswd_cycle(account_with_docroot):
    fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})

    fileauth.add_user({"username": "demo1", "path": "public_html/members", "htuser": "alice", "password": "AlicePass123!"})
    users = fileauth.list_users({"username": "demo1", "path": "public_html/members"})["users"]
    assert users == ["alice"]

    htpasswd = account_with_docroot["docroot"] / "members" / ".htpasswd"
    content = htpasswd.read_text()
    assert "alice:" in content
    assert "AlicePass123" not in content  # never stored in plaintext

    fileauth.add_user({"username": "demo1", "path": "public_html/members", "htuser": "bob", "password": "BobPassword456!"})
    assert set(fileauth.list_users({"username": "demo1", "path": "public_html/members"})["users"]) == {"alice", "bob"}

    fileauth.delete_user({"username": "demo1", "path": "public_html/members", "htuser": "alice"})
    assert fileauth.list_users({"username": "demo1", "path": "public_html/members"})["users"] == ["bob"]


def test_add_user_rejects_htpasswd_symlink_swapped_after_enable(account_with_docroot):
    fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})
    target = account_with_docroot["home"] / "target"
    target.write_text("do-not-touch")
    htpasswd = account_with_docroot["docroot"] / "members" / ".htpasswd"
    htpasswd.unlink()
    htpasswd.symlink_to(target)

    with pytest.raises(fileauth.FileAuthError):
        fileauth.add_user({"username": "demo1", "path": "public_html/members", "htuser": "alice", "password": "AlicePass123!"})

    assert target.read_text() == "do-not-touch"


def test_add_user_rejects_invalid_username(account_with_docroot):
    fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})
    with pytest.raises(ValidationError):
        fileauth.add_user({"username": "demo1", "path": "public_html/members", "htuser": "bad user", "password": "Whatever123!Pass"})


def test_enable_protection_calls_refresh_vhost(account_with_docroot, monkeypatch):
    calls = []
    monkeypatch.setattr(fileauth.ols, "refresh_vhost", lambda account: calls.append(account.username))
    fileauth.enable_protection({"username": "demo1", "path": "public_html/members"})
    assert calls == ["demo1"]
