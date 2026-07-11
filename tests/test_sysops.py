import os
import pwd as real_pwd
import stat

import pytest

from daemon import sysops


@pytest.fixture()
def account_with_home(tmp_path, monkeypatch):
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(sysops.settings, "home_base", str(home_base))
    account_home = home_base / "demo1"
    account_home.mkdir()
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), "/usr/sbin/nologin"))
    monkeypatch.setattr(sysops.pwd, "getpwnam", lambda name: fake_pw)
    return account_home


def test_ensure_tmp_dir_creates_with_correct_owner_and_mode(account_with_home):
    """Security fix (Phase 6a research finding): this is the account's
    private replacement for the shared system /tmp that used to be in
    every account's open_basedir -- 0750 (not world-readable), owned by
    the account, mirroring handlers_domain.ensure_docroot's own tmp_dir
    creation exactly (this is the shared implementation the two callers
    -- account creation and domain-add -- both now use)."""
    tmp_dir = sysops.ensure_tmp_dir("demo1")
    assert tmp_dir == str(account_with_home / "tmp")
    st = os.stat(tmp_dir)
    assert stat.S_IMODE(st.st_mode) == 0o750
    assert st.st_uid == os.getuid()
    assert st.st_gid == os.getgid()


def test_ensure_tmp_dir_is_idempotent(account_with_home):
    first = sysops.ensure_tmp_dir("demo1")
    (account_with_home / "tmp" / "marker.txt").write_text("keep me")
    second = sysops.ensure_tmp_dir("demo1")
    assert first == second
    assert (account_with_home / "tmp" / "marker.txt").read_text() == "keep me"


def test_recycle_php_workers_does_not_raise_for_nonexistent_user():
    """No test-environment Linux user exists to actually recycle -- pkill
    finding zero matches is the expected, harmless outcome here; the
    real assertion is that this never raises (set_php_ini/reset_php_ini
    call it unconditionally after every change, never inside a try/except,
    see daemon/handlers_php_ini.py)."""
    sysops.recycle_php_workers("no-such-boron-test-user")


def test_recycle_php_workers_scopes_to_username_and_lsphp(monkeypatch):
    calls = []
    monkeypatch.setattr(sysops, "run", lambda args, **kw: calls.append(args))
    sysops.recycle_php_workers("demo1")
    assert calls == [["pkill", "-u", "demo1", "-f", "lsphp"]]
