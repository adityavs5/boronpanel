import pytest

from daemon import cgroups
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account


def make_account(session, **overrides):
    defaults = dict(username="demo1", status="active", uid=2000, gid=2000, cpu_pct=25, mem_mb=512, io_mb=50, pids_max=50)
    defaults.update(overrides)
    account = Account(**defaults)
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


@pytest.fixture()
def fake_unit_fs(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd" / "system"
    unit_dir.mkdir(parents=True)
    monkeypatch.setattr(cgroups, "_unit_path", lambda username: unit_dir / cgroups.slice_name(username))
    return unit_dir


def test_slice_name_format():
    assert cgroups.slice_name("demo1") == "forgehost-demo1.slice"


def test_ensure_slice_writes_unit_and_starts(fake_systemctl, fake_unit_fs):
    cgroups.ensure_slice("demo1")
    unit_file = fake_unit_fs / "forgehost-demo1.slice"
    assert unit_file.exists()
    content = unit_file.read_text()
    assert "[Unit]" in content
    assert "demo1" in content
    # deliberately no Slice=forgehost.slice line -- naming-convention alone
    # nests it (see _write_unit_file's docstring for why the explicit
    # directive was removed)
    assert "Slice=" not in content
    assert any(args[:2] == ["systemctl", "daemon-reload"] for args in fake_systemctl)
    assert any(args[:3] == ["systemctl", "start", "forgehost-demo1.slice"] for args in fake_systemctl)


def test_ensure_slice_is_idempotent_does_not_rewrite_unchanged_file(fake_systemctl, fake_unit_fs):
    cgroups.ensure_slice("demo1")
    unit_file = fake_unit_fs / "forgehost-demo1.slice"
    mtime_before = unit_file.stat().st_mtime_ns
    cgroups.ensure_slice("demo1")
    assert unit_file.stat().st_mtime_ns == mtime_before


def test_apply_limits_calls_set_property_with_correct_values(fake_systemctl, fake_unit_fs, monkeypatch):
    monkeypatch.setattr(cgroups.settings, "cgroup_io_device", "/dev/vda")
    cgroups.apply_limits("demo1", cpu_pct=40, mem_mb=1024, io_mb=100, pids_max=75)

    set_property_call = next(args for args in fake_systemctl if args[:2] == ["systemctl", "set-property"])
    assert "forgehost-demo1.slice" in set_property_call
    assert "CPUQuota=40%" in set_property_call
    assert "MemoryMax=1024M" in set_property_call
    assert "MemorySwapMax=0" in set_property_call
    assert "TasksMax=75" in set_property_call
    assert "IOReadBandwidthMax=/dev/vda 100M" in set_property_call
    assert "IOWriteBandwidthMax=/dev/vda 100M" in set_property_call


def test_apply_limits_raises_on_set_property_failure(fake_unit_fs, monkeypatch):
    def fake_run(args, timeout=30.0, check=False):
        if args[:2] == ["systemctl", "set-property"]:
            return ProcResult(args=args, returncode=1, stdout="", stderr="unknown property")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cgroups, "run", fake_run)
    with pytest.raises(cgroups.CgroupError):
        cgroups.apply_limits("demo1", cpu_pct=25, mem_mb=512, io_mb=50, pids_max=50)


def test_remove_slice_stops_and_removes_unit(fake_systemctl, fake_unit_fs):
    cgroups.ensure_slice("demo1")
    unit_file = fake_unit_fs / "forgehost-demo1.slice"
    assert unit_file.exists()

    cgroups.remove_slice("demo1")
    assert not unit_file.exists()
    assert any(args[:3] == ["systemctl", "stop", "forgehost-demo1.slice"] for args in fake_systemctl)


def test_remove_slice_is_idempotent_when_never_created(fake_systemctl, fake_unit_fs):
    cgroups.remove_slice("never-existed")  # must not raise


def test_bootstrap_all_slices_applies_limits_for_active_and_suspended_only(isolated_db, monkeypatch):
    applied = []
    monkeypatch.setattr(cgroups, "apply_limits", lambda username, cpu_pct, mem_mb, io_mb, pids_max: applied.append(username))

    with write_session() as session:
        make_account(session, username="demo1", status="active")
        make_account(session, username="demo2", status="terminated")
        make_account(session, username="demo3", status="suspended")

    cgroups.bootstrap_all_slices()
    assert set(applied) == {"demo1", "demo3"}


def test_bootstrap_all_slices_continues_after_one_failure(isolated_db, monkeypatch):
    applied = []

    def maybe_fail(username, cpu_pct, mem_mb, io_mb, pids_max):
        if username == "demo1":
            raise cgroups.CgroupError("boom")
        applied.append(username)

    monkeypatch.setattr(cgroups, "apply_limits", maybe_fail)
    with write_session() as session:
        make_account(session, username="demo1", status="active")
        make_account(session, username="demo2", status="active")

    cgroups.bootstrap_all_slices()  # must not raise
    assert applied == ["demo2"]


def test_reconcile_processes_returns_zero_when_lshttpd_cgroup_missing(monkeypatch):
    monkeypatch.setattr(cgroups, "LSHTTPD_CGROUP_PROCS", cgroups.Path("/definitely/does/not/exist"))
    assert cgroups.reconcile_processes() == 0


def test_reconcile_processes_moves_matching_pids(isolated_db, tmp_path, monkeypatch):
    lshttpd_procs = tmp_path / "lshttpd_cgroup_procs"
    lshttpd_procs.write_text("111\n222\n")
    monkeypatch.setattr(cgroups, "LSHTTPD_CGROUP_PROCS", lshttpd_procs)

    with write_session() as session:
        make_account(session, username="demo1", uid=2000)

    # pid 111 belongs to uid 2000 (demo1), pid 222 belongs to uid 0 (root) --
    # only the account-owned one should move.
    fake_stat_uids = {111: 2000, 222: 0}

    class FakeStat:
        def __init__(self, uid):
            self.st_uid = uid

    def fake_stat(path):
        pid = int(path.split("/")[2])
        return FakeStat(fake_stat_uids[pid])

    monkeypatch.setattr(cgroups.os, "stat", fake_stat)

    target_dir = tmp_path / "forgehost.slice" / "forgehost-demo1.slice"
    target_dir.mkdir(parents=True)
    (target_dir / "cgroup.procs").write_text("")
    monkeypatch.setattr(cgroups, "_cgroup_path", lambda username: tmp_path / "forgehost.slice" / cgroups.slice_name(username))

    moved = cgroups.reconcile_processes()
    assert moved == 1
    assert (target_dir / "cgroup.procs").read_text() == "111"


def test_reconcile_processes_skips_pids_below_min_account_uid(isolated_db, tmp_path, monkeypatch):
    lshttpd_procs = tmp_path / "lshttpd_cgroup_procs"
    lshttpd_procs.write_text("999\n")
    monkeypatch.setattr(cgroups, "LSHTTPD_CGROUP_PROCS", lshttpd_procs)

    class FakeStat:
        st_uid = 33  # www-data, a system uid, not a hosting account

    monkeypatch.setattr(cgroups.os, "stat", lambda path: FakeStat())

    moved = cgroups.reconcile_processes()
    assert moved == 0


def test_reconcile_processes_ignores_pid_that_exited_mid_scan(isolated_db, tmp_path, monkeypatch):
    lshttpd_procs = tmp_path / "lshttpd_cgroup_procs"
    lshttpd_procs.write_text("555\n")
    monkeypatch.setattr(cgroups, "LSHTTPD_CGROUP_PROCS", lshttpd_procs)

    def fake_stat(path):
        raise OSError("No such process")

    monkeypatch.setattr(cgroups.os, "stat", fake_stat)
    assert cgroups.reconcile_processes() == 0  # must not raise
