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
    assert "IOReadBandwidthMax=/dev/vda 100M" in command
    assert "CPUAccounting=yes" in command


def test_io_device_falls_back_to_detected_major_minor(monkeypatch):
    monkeypatch.setattr(cgroups.settings, "cgroup_io_device", "/dev/missing-disk")
    monkeypatch.setattr(cgroups, "_is_block_device", lambda path: False)
    monkeypatch.setattr(cgroups, "_backing_major_minor", lambda: (8, 1))
    monkeypatch.setattr(cgroups, "_materialize_block_device", lambda major, minor: cgroups.Path(f"/run/boron/cgroup-devices/{major}-{minor}"))

    assert cgroups.resolve_io_device() == "/run/boron/cgroup-devices/8-1"


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
