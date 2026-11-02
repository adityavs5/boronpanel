import os
import pwd as real_pwd
import stat
import subprocess
import time

import pytest

from daemon import sysops
from shared.validation import ValidationError


@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n", "\x00"])
def test_password_sink_rejects_extra_records_before_command(monkeypatch, separator):
    calls = []
    monkeypatch.setattr(sysops, "run", lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(ValidationError, match="line breaks or null"):
        sysops.set_initial_password("demo1", "TenantPass123!" + separator + "root:OtherPass456!")
    assert calls == []


def test_password_sink_encodes_one_record_without_changing_password(monkeypatch):
    calls = []
    monkeypatch.setattr(sysops, "run", lambda *a, **kw: calls.append((a, kw)))
    password = " Tenant:Pass123!ü "
    sysops.set_initial_password("demo1", password)
    assert calls == [((["chpasswd"],), {"input_text": "demo1:" + password + "\n", "check": True})]


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


@pytest.mark.parametrize("remove_home", [False, True])
def test_delete_linux_user_preserves_home_during_provisioning_rollback(monkeypatch, remove_home):
    calls = []
    monkeypatch.setattr(sysops, "user_exists", lambda username: True)
    monkeypatch.setattr(sysops, "run", lambda args, **kw: calls.append(args))
    sysops.delete_linux_user("demo1", remove_home=remove_home)
    assert calls[0] == ["pkill", "-9", "-u", "demo1"]
    assert calls[1] == ["userdel", *(["--remove"] if remove_home else []), "--force", "demo1"]
    assert calls[1][0] == "userdel"


def test_delete_linux_user_finishes_home_after_userdel_timeout(monkeypatch, tmp_path):
    home = tmp_path / "demo1"
    home.mkdir()
    exists = iter([True, False])
    calls = []
    monkeypatch.setattr(sysops.settings, "home_base", str(tmp_path))
    monkeypatch.setattr(sysops, "user_exists", lambda username: next(exists))
    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "userdel":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(sysops, "run", fake_run)
    sysops.delete_linux_user("demo1")
    assert calls[1][0] == ["userdel", "--remove", "--force", "demo1"]
    assert calls[1][1]["timeout"] == 600
    assert calls[2][0] == ["find", str(home), "-xdev", "-depth", "-delete"]


def test_retry_cleans_home_when_linux_identity_is_already_gone(monkeypatch, tmp_path):
    home = tmp_path / "demo1"
    home.mkdir()
    calls = []
    monkeypatch.setattr(sysops.settings, "home_base", str(tmp_path))
    monkeypatch.setattr(sysops, "user_exists", lambda username: False)
    monkeypatch.setattr(sysops, "run", lambda args, **kwargs: calls.append(args))
    sysops.delete_linux_user("demo1")
    assert calls == [["find", str(home), "-xdev", "-depth", "-delete"]]


@pytest.fixture()
def vhost_log_root(tmp_path, monkeypatch):
    root = tmp_path / "vhost-logs"
    root.mkdir(mode=0o711)
    monkeypatch.setattr(sysops, "VHOST_LOG_ROOT", str(root))
    monkeypatch.setattr(sysops, "_vhost_log_root", lambda: str(root))
    return root


def test_ensure_web_logs_puts_vhost_logs_outside_the_home(account_with_home, vhost_log_root, monkeypatch):
    """Regression: OLS wrote vhost logs as nobody into the tenant's ~/logs,
    following a tenant-planted symlink into another tenant's logs. The
    vhost log directory is now server-owned, writable by nobody only and
    readable by the account; the web server loses its ~/logs access."""
    calls = []
    monkeypatch.setattr(sysops, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    target = sysops.ensure_web_logs("demo1")
    assert target == str(vhost_log_root / "demo1") == sysops.vhost_log_dir("demo1")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o750
    assert os.stat(target).st_uid == os.geteuid()
    logs = account_with_home / "logs"
    assert stat.S_IMODE(os.stat(logs).st_mode) == 0o750
    uid = os.getuid()
    assert calls == [
        (["setfacl", "-m", f"u:nobody:rwx,u:{uid}:r-x", "-m", f"d:u:nobody:rw-,d:u:{uid}:r--", target],
         {"check": True}),
        (["setfacl", "-x", "u:nobody", "-x", "d:u:nobody", str(logs)], {"uid": uid, "gid": os.getgid()}),
    ]
    calls.clear()
    sysops.ensure_web_logs("demo1")  # idempotent: the ACL is set once
    assert [c[0][1] for c in calls] == ["-x"]


def test_ensure_web_logs_migrates_only_web_server_written_logs(account_with_home, vhost_log_root, monkeypatch, tmp_path):
    """Existing accounts: OLS-written logs (owned by nobody, single link)
    move to the new directory; tenant-created files, symlinks and hard
    links stay where they are, so a tenant cannot inject a file into the
    server-owned directory."""
    monkeypatch.setattr(sysops, "run", lambda args, **kwargs: None)
    logs = account_with_home / "logs"
    logs.mkdir()
    (logs / "example_com-access.log").write_text("ols line\n")
    (logs / "example_com-error.log.2026_10_01.gz").write_bytes(b"gz")
    (logs / "php-error.log").write_text("php\n")
    victim = tmp_path / "other-tenant-access.log"
    victim.write_text("victim lines")
    (logs / "planted_com-access.log").symlink_to(victim)
    (logs / "linked_com-access.log").write_text("x")
    os.link(logs / "linked_com-access.log", tmp_path / "second-link")
    monkeypatch.setattr(sysops.pwd, "getpwnam", lambda name: real_pwd.struct_passwd(
        (name, "x", os.getuid(), os.getgid(), "", str(account_with_home), "/usr/sbin/nologin")))

    sysops.ensure_web_logs("demo1")

    moved = vhost_log_root / "demo1"
    assert sorted(p.name for p in moved.iterdir()) == ["example_com-access.log", "example_com-error.log.2026_10_01.gz"]
    assert (moved / "example_com-access.log").read_text() == "ols line\n"
    assert sorted(p.name for p in logs.iterdir()) == ["linked_com-access.log", "php-error.log", "planted_com-access.log"]
    assert victim.read_text() == "victim lines"


def test_migration_skips_files_not_written_by_the_web_server(account_with_home, vhost_log_root, monkeypatch):
    monkeypatch.setattr(sysops, "run", lambda args, **kwargs: None)
    logs = account_with_home / "logs"
    logs.mkdir()
    (logs / "example_com-access.log").write_text("tenant-forged\n")
    real_getpwnam = sysops.pwd.getpwnam
    other = real_pwd.struct_passwd(("nobody", "x", os.getuid() + 1, os.getgid(), "", "/", "/usr/sbin/nologin"))
    monkeypatch.setattr(sysops.pwd, "getpwnam", lambda name: other if name == "nobody" else real_getpwnam(name))
    sysops.ensure_web_logs("demo1")
    assert list((vhost_log_root / "demo1").iterdir()) == []
    assert (logs / "example_com-access.log").read_text() == "tenant-forged\n"


def _archive(directory, name, age_days, size=10):
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_prune_vhost_logs_keeps_the_newest_archives_of_each_log(vhost_log_root, monkeypatch):
    """Vhost logs moved to /var: OLS bounds each file and the archives' age,
    but not how many archives a busy site keeps."""
    directory = vhost_log_root / "demo1"
    directory.mkdir()
    (directory / "example_com-access.log").write_text("active")
    (directory / "php-error.log").write_text("not a vhost log")
    for day in range(14):
        _archive(directory, f"example_com-access.log.10_{day + 1:02d}_2026.gz", age_days=day)
    for day in range(3):
        _archive(directory, f"example_com-error.log.10_{day + 1:02d}_2026.gz", age_days=day)
    assert sysops.prune_vhost_logs("demo1") == 14 - sysops.VHOST_LOG_KEEP_ARCHIVES
    names = sorted(p.name for p in directory.iterdir())
    assert "example_com-access.log" in names and "php-error.log" in names
    access = [name for name in names if name.startswith("example_com-access.log.")]
    assert access == sorted(f"example_com-access.log.10_{day + 1:02d}_2026.gz"
                            for day in range(sysops.VHOST_LOG_KEEP_ARCHIVES))
    assert len([name for name in names if name.startswith("example_com-error.log.")]) == 3
    # A byte budget applies as well.
    monkeypatch.setattr(sysops, "VHOST_LOG_KEEP_ARCHIVE_BYTES", 25)
    assert sysops.prune_vhost_logs("demo1") == sysops.VHOST_LOG_KEEP_ARCHIVES - 2 + 1
    assert sysops.prune_vhost_logs("missing1") == 0


def test_log_migration_moves_only_the_archives_retention_keeps(account_with_home, vhost_log_root, monkeypatch):
    monkeypatch.setattr(sysops, "run", lambda args, **kwargs: None)
    monkeypatch.setattr(sysops.pwd, "getpwnam", lambda name: real_pwd.struct_passwd(
        (name, "x", os.getuid(), os.getgid(), "", str(account_with_home), "/usr/sbin/nologin")))
    logs = account_with_home / "logs"
    logs.mkdir()
    (logs / "example_com-access.log").write_text("active")
    for day in range(12):
        _archive(logs, f"example_com-access.log.09_{day + 1:02d}_2026.gz", age_days=day)
    sysops.ensure_web_logs("demo1")
    moved = sorted(p.name for p in (vhost_log_root / "demo1").iterdir())
    assert len(moved) == 1 + sysops.VHOST_LOG_KEEP_ARCHIVES and "example_com-access.log" in moved
    # The oldest archives stay in the account's own ~/logs.
    assert sorted(p.name for p in logs.iterdir()) == ["example_com-access.log.09_11_2026.gz",
                                                       "example_com-access.log.09_12_2026.gz"]


def test_remove_and_rename_vhost_log_dir(vhost_log_root, monkeypatch):
    monkeypatch.setattr(sysops, "run", lambda args, **kwargs: None)
    monkeypatch.setattr(sysops.pwd, "getpwnam", lambda name: real_pwd.struct_passwd(
        (name, "x", os.getuid(), os.getgid(), "", "/", "/usr/sbin/nologin")))
    (vhost_log_root / "old1").mkdir()
    (vhost_log_root / "old1" / "a_com-access.log").write_text("x")
    sysops.rename_vhost_log_dir("old1", "new1")
    assert (vhost_log_root / "new1" / "a_com-access.log").exists()
    sysops.remove_vhost_log_dir("new1")
    assert list(vhost_log_root.iterdir()) == []
    sysops.remove_vhost_log_dir("new1")  # idempotent


def test_new_account_has_private_default_backup_folder(account_with_home, monkeypatch):
    monkeypatch.setattr(sysops, 'user_exists', lambda _: False)
    monkeypatch.setattr(sysops, 'run', lambda *a, **kw: None)
    uid, gid = sysops.create_linux_user('demo1')
    root = account_with_home / 'public_html'
    assert root.is_dir()
    assert stat.S_IMODE(root.stat().st_mode) == 0o750
    assert (root.stat().st_uid, root.stat().st_gid) == (uid, gid)
    sessions = account_with_home / 'tmp' / 'sessions'
    assert sessions.is_dir()
    assert stat.S_IMODE(sessions.stat().st_mode) == 0o700
    assert (sessions.stat().st_uid, sessions.stat().st_gid) == (uid, gid)
