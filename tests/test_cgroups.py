import pytest

from daemon import cgroups
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account


def make_account(session, **overrides):
    values = dict(
        username="demo1", status="active", uid=2000, gid=2000,
        cpu_pct=25, mem_mb=512, io_mb=50, pids_max=50,
    )
    values.update(overrides)
    account = Account(**values)
    session.add(account)
    session.flush()
    return account


@pytest.fixture()
def fake_systemctl(monkeypatch):
    calls = []

    def fake_run(args, timeout=30.0, check=False):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cgroups, "run", fake_run)
    return calls


def test_user_slice_name_rejects_system_uids():
    assert cgroups.user_slice_name(2000) == "user-2000.slice"
    with pytest.raises(cgroups.CgroupError, match="system uid"):
        cgroups.user_slice_name(999)


def test_resolver_uses_and_cross_checks_account_uid(isolated_db, monkeypatch):
    with write_session() as session:
        make_account(session)
    monkeypatch.setattr(cgroups, "_getpwnam", lambda _username: (_ for _ in ()).throw(KeyError()))
    assert cgroups.account_slice_name("demo1") == "user-2000.slice"
    with pytest.raises(cgroups.CgroupError, match="does not match"):
        cgroups.account_slice_name("demo1", 2001)


def test_apply_limits_targets_canonical_user_slice(isolated_db, fake_systemctl, monkeypatch):
    with write_session() as session:
        make_account(session)
    monkeypatch.setattr(cgroups, "resolve_io_device", lambda: "/dev/vda")
    cgroups.apply_limits("demo1", cpu_pct=40, mem_mb=1024, io_mb=100, pids_max=75)

    assert ["systemctl", "start", "user-2000.slice"] in fake_systemctl
    command = next(args for args in fake_systemctl if args[:2] == ["systemctl", "set-property"])
    assert command[2] == "user-2000.slice"
    assert "CPUQuota=40%" in command
    assert "MemoryMax=1024M" in command
    assert "MemorySwapMax=0" in command
    assert "TasksMax=75" in command
    # Exact bytes: systemd reads an "M" bandwidth suffix as 10**6, so "100M"
    # enforced 100,000,000 B/s for a requested 100 MiB/s.
    assert command.index("IOReadBandwidthMax=") < command.index("IOReadBandwidthMax=/dev/vda 104857600")
    assert command.index("IOWriteBandwidthMax=") < command.index("IOWriteBandwidthMax=/dev/vda 104857600")
    assert "IOReadBandwidthMax=/dev/vda 104857600" in command
    assert not any(arg.endswith("100M") for arg in command if "Bandwidth" in arg)
    assert "CPUAccounting=yes" in command


def test_io_device_falls_back_to_detected_major_minor(monkeypatch, tmp_path):
    monkeypatch.setattr(cgroups.settings, "cgroup_io_device", "/dev/missing-disk")
    monkeypatch.setattr(cgroups, "SYS_DEV_BLOCK", tmp_path)  # 8:1 is no partition here
    monkeypatch.setattr(cgroups, "_is_block_device", lambda path: False)
    monkeypatch.setattr(cgroups, "_backing_major_minor", lambda: (8, 1))
    monkeypatch.setattr(cgroups, "_materialize_block_device", lambda major, minor: cgroups.Path(f"/run/boron/cgroup-devices/{major}-{minor}"))

    assert cgroups.resolve_io_device() == "/run/boron/cgroup-devices/8-1"


def _fake_sysfs(root):
    """sda (8:0) holding partition sda1 (8:1), linked like /sys/dev/block."""
    disk = root / "devices" / "block" / "sda"
    (disk / "sda1").mkdir(parents=True)
    (disk / "dev").write_text("8:0\n")
    (disk / "sda1" / "dev").write_text("8:1\n")
    (disk / "sda1" / "partition").write_text("1\n")
    links = root / "dev" / "block"
    links.mkdir(parents=True)
    (links / "8:0").symlink_to(disk)
    (links / "8:1").symlink_to(disk / "sda1")
    return links


def test_io_limits_name_the_disk_that_holds_the_home_partition(monkeypatch, tmp_path):
    """io.max refuses a partition: limits written for 8:1 were never enforced
    by kernels/systemd versions that did not map it to its disk."""
    monkeypatch.setattr(cgroups, "SYS_DEV_BLOCK", _fake_sysfs(tmp_path))
    assert cgroups._whole_disk(8, 1) == (8, 0)
    assert cgroups._whole_disk(8, 0) == (8, 0)
    assert cgroups._whole_disk(253, 0) == (253, 0)  # device-mapper: unchanged

    monkeypatch.setattr(cgroups.settings, "cgroup_io_device", "auto")
    monkeypatch.setattr(cgroups, "_backing_major_minor", lambda: (8, 1))
    monkeypatch.setattr(cgroups, "_materialize_block_device", lambda major, minor: cgroups.Path(f"/run/boron/cgroup-devices/{major}-{minor}"))
    assert cgroups.resolve_io_device() == "/run/boron/cgroup-devices/8-0"


def _fake_slice(root, uid, names):
    slice_root = root / f"user-{uid}.slice"
    for name in names:
        (slice_root / name).mkdir(parents=True)
        (slice_root / name / "cgroup.kill").write_text("")
    return slice_root


def test_suspension_kills_everything_but_panel_managed_services(isolated_db, monkeypatch, tmp_path):
    with write_session() as session:
        make_account(session)
    monkeypatch.setattr(cgroups, "_getpwnam", lambda _username: (_ for _ in ()).throw(KeyError()))
    monkeypatch.setattr(cgroups, "USER_SLICE_ROOT", tmp_path)
    killed_units = ["session-7.scope", "user@2000.service", "litespeed-exec.scope", "run-u42.scope",
                    "boron-account-2000-abc.service"]
    managed = ["boron-node-demo1-1.service", "boron-python-demo1-2.service", "boron-redis-demo1-3.service",
               "boron-filebrowser@demo1.service"]
    slice_root = _fake_slice(tmp_path, 2000, killed_units + managed)
    # A cron job started before the suspension runs outside the slice.
    cgroup_of = {11: "/system.slice/cron.service", 12: "/user.slice/user-2000.slice/boron-redis-demo1-3.service"}
    monkeypatch.setattr(cgroups, "_account_pids", lambda uid: sorted(cgroup_of) if uid == 2000 else [])
    monkeypatch.setattr(cgroups, "_pid_cgroup", lambda pid: cgroup_of[pid])
    signalled = []
    monkeypatch.setattr(cgroups, "_kill_pid", lambda pid: signalled.append(pid) or cgroup_of.pop(pid) is not None)

    cgroups.end_account_processes("demo1")

    for name in killed_units:
        assert (slice_root / name / "cgroup.kill").read_text() == "1", name
    for name in managed:
        assert (slice_root / name / "cgroup.kill").read_text() == "", name
    assert signalled == [11]


def test_process_cleanup_refuses_the_daemons_own_uid(isolated_db, monkeypatch):
    with write_session() as session:
        make_account(session)
    monkeypatch.setattr(cgroups, "_getpwnam", lambda _username: (_ for _ in ()).throw(KeyError()))
    monkeypatch.setattr(cgroups.os, "geteuid", lambda: 2000)
    with pytest.raises(cgroups.CgroupError, match="daemon's uid"):
        cgroups.end_account_processes("demo1")


def test_unsuspend_restarts_only_a_lingering_user_manager(isolated_db, fake_systemctl, monkeypatch, tmp_path):
    with write_session() as session:
        make_account(session)
    monkeypatch.setattr(cgroups, "_getpwnam", lambda _username: (_ for _ in ()).throw(KeyError()))
    monkeypatch.setattr(cgroups, "LINGER_DIR", tmp_path)
    cgroups.resume_user_manager("demo1")
    assert fake_systemctl == []
    (tmp_path / "demo1").touch()
    cgroups.resume_user_manager("demo1")
    assert fake_systemctl == [["systemctl", "start", "user@2000.service"]]


def test_apply_limits_fails_closed_on_systemd_error(isolated_db, monkeypatch):
    with write_session() as session:
        make_account(session)

    def fake_run(args, timeout=30.0, check=False):
        if args[:2] == ["systemctl", "set-property"]:
            return ProcResult(args=args, returncode=1, stdout="", stderr="unknown property")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cgroups, "run", fake_run)
    monkeypatch.setattr(cgroups, "resolve_io_device", lambda: "/dev/vda")
    with pytest.raises(cgroups.CgroupError, match="set-property"):
        cgroups.apply_limits("demo1", 25, 512, 50, 50)


def test_remove_slice_resets_uid_policy_and_legacy_state(isolated_db, fake_systemctl, monkeypatch):
    with write_session() as session:
        make_account(session)
    monkeypatch.setattr(cgroups, "_retire_legacy_slice", lambda username: username == "demo1")
    monkeypatch.setattr(cgroups.shutil, "rmtree", lambda *args, **kwargs: None)

    cgroups.remove_slice("demo1")

    assert ["systemctl", "stop", "user-2000.slice"] in fake_systemctl
    assert ["systemctl", "revert", "user-2000.slice"] in fake_systemctl


def test_bootstrap_applies_active_and_suspended_accounts(isolated_db, monkeypatch):
    applied = []
    monkeypatch.setattr(
        cgroups, "apply_limits",
        lambda username, cpu, mem, io, pids, **kwargs: applied.append((username, kwargs["uid"])),
    )
    with write_session() as session:
        make_account(session, username="demo1", uid=2000)
        make_account(session, username="demo2", uid=2001, status="terminated")
        make_account(session, username="demo3", uid=2002, status="suspended")
    cgroups.bootstrap_all_slices()
    assert set(applied) == {("demo1", 2000), ("demo3", 2002)}


def test_audit_reports_native_php_placement_without_moving_pids(isolated_db, tmp_path, monkeypatch):
    procs = tmp_path / "cgroup.procs"
    procs.write_text("111\n222\n")
    monkeypatch.setattr(cgroups, "LSHTTPD_CGROUP_PROCS", procs)
    with write_session() as session:
        make_account(session, username="demo1", uid=2000)
    monkeypatch.setattr(cgroups, "_pid_uid", lambda pid: {111: 2000, 222: 0}[pid])
    monkeypatch.setattr(cgroups, "_pid_cgroup", lambda pid: "/system.slice/lshttpd.service")
    monkeypatch.setattr(cgroups, "_retire_legacy_slice", lambda username: True)

    result = cgroups.audit_php_coverage()

    assert result == {"checked": 1, "uncovered": 1, "accounts": {"demo1": 1}}
    assert procs.read_text() == "111\n222\n"


def test_audit_accepts_descendant_of_user_slice(isolated_db, tmp_path, monkeypatch):
    procs = tmp_path / "cgroup.procs"
    procs.write_text("111\n")
    monkeypatch.setattr(cgroups, "LSHTTPD_CGROUP_PROCS", procs)
    with write_session() as session:
        make_account(session, uid=2000)
    monkeypatch.setattr(cgroups, "_pid_uid", lambda pid: 2000)
    monkeypatch.setattr(cgroups, "_pid_cgroup", lambda pid: "/user.slice/user-2000.slice/lsapi.scope")
    monkeypatch.setattr(cgroups, "_retire_legacy_slice", lambda username: True)
    assert cgroups.audit_php_coverage()["uncovered"] == 0


def test_audit_ignores_exited_process(isolated_db, tmp_path, monkeypatch):
    procs = tmp_path / "cgroup.procs"
    procs.write_text("555\n")
    monkeypatch.setattr(cgroups, "LSHTTPD_CGROUP_PROCS", procs)
    monkeypatch.setattr(cgroups, "_pid_uid", lambda pid: None)
    assert cgroups.audit_php_coverage()["checked"] == 0


def test_bootstrap_reconciles_later_accounts_when_quarantine_fails(isolated_db, monkeypatch):
    with write_session() as db:
        make_account(db, username="first", uid=2001)
        make_account(db, username="second", uid=2002)
    seen = []
    def apply(username, *args, **kwargs):
        seen.append(username)
        if username == "first":
            raise cgroups.CgroupError("systemd unavailable")
    def quarantine(account):
        raise cgroups.CgroupError("controller missing")
    monkeypatch.setattr(cgroups, "apply_limits", apply)
    monkeypatch.setattr(cgroups, "quarantine_failed_policy", quarantine)
    with pytest.raises(cgroups.CgroupError, match="host intervention"):
        cgroups.bootstrap_all_slices()
    assert seen == ["first", "second"]
