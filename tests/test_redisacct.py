import os
import pwd as real_pwd

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import redisacct
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, RedisInstance
from shared.validation import ValidationError


@pytest.fixture()
def fake_systemctl(monkeypatch):
    calls = []

    def fake_run(args, timeout=30, **kwargs):
        calls.append(args)
        if args[0] == "systemctl" and args[1] in ("is-active", "is-enabled"):
            return ProcResult(args=args, returncode=0, stdout="active\n" if args[1] == "is-active" else "enabled\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(redisacct.appunits, "run", fake_run)
    return calls


@pytest.fixture()
def fake_redis_cli(monkeypatch):
    calls = []

    def fake_run(args, timeout=30, **kwargs):
        calls.append(args)
        if "INFO" in args:
            return ProcResult(args=args, returncode=0, stdout="used_memory:1048576\r\nused_memory_human:1.00M\r\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="OK\n", stderr="")

    monkeypatch.setattr(redisacct, "run", fake_run)
    return calls


@pytest.fixture()
def account_with_home(isolated_db, tmp_path, monkeypatch, fake_systemctl, fake_redis_cli):
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(redisacct.settings, "home_base", str(home_base))
    monkeypatch.setattr(redisacct.settings, "redis_run_dir", str(tmp_path / "run" / "redis"))
    (tmp_path / "run" / "redis").mkdir(parents=True)
    monkeypatch.setattr(redisacct, "_conf_path", lambda unit: tmp_path / "redis-conf" / f"{unit}.conf")

    account_home = home_base / "demo1"
    account_home.mkdir()
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), "/usr/sbin/nologin"))
    monkeypatch.setattr(redisacct.pwd, "getpwnam", lambda name: fake_pw)

    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})

    return {"home": account_home}


def test_enable_redis_defaults_to_configured_mem(account_with_home):
    result = redisacct.enable_redis({"username": "demo1"})
    assert result["mem_mb"] == redisacct.settings.redis_default_mem_mb
    assert result["enabled"] is True
    assert result["active"] == "active"


def test_enable_redis_creates_data_dir_with_0700(account_with_home):
    result = redisacct.enable_redis({"username": "demo1", "mem_mb": 128})
    st = os.stat(result["data_dir"])
    import stat as statmod
    assert statmod.S_IMODE(st.st_mode) == 0o700


def test_enable_redis_writes_conf_with_unix_socket_and_no_tcp_port(account_with_home):
    result = redisacct.enable_redis({"username": "demo1", "mem_mb": 96})
    conf_path = redisacct._conf_path(result["unit"])
    content = conf_path.read_text()
    assert conf_path.parent.stat().st_mode & 0o777 == 0o711
    assert "port 0" in content
    assert f"unixsocket {result['socket_path']}" in content
    assert "unixsocketperm 700" in content
    assert "maxmemory 96mb" in content
    assert 'save ""' in content


def test_enable_redis_rejects_out_of_range_mem(account_with_home):
    with pytest.raises(ValidationError):
        redisacct.enable_redis({"username": "demo1", "mem_mb": 8})
    with pytest.raises(ValidationError):
        redisacct.enable_redis({"username": "demo1", "mem_mb": 100000})


def test_enable_redis_is_idempotent(account_with_home):
    first = redisacct.enable_redis({"username": "demo1", "mem_mb": 64})
    second = redisacct.enable_redis({"username": "demo1", "mem_mb": 128})
    assert first["id"] == second["id"]
    assert second["mem_mb"] == 128
    with write_session() as session:
        rows = session.scalars(select(RedisInstance).where(RedisInstance.account_id == first["account_id"])).all()
        assert len(rows) == 1


def test_set_mem_limit_restarts_when_enabled(account_with_home, fake_systemctl):
    redisacct.enable_redis({"username": "demo1"})
    fake_systemctl.clear()
    result = redisacct.set_mem_limit({"username": "demo1", "mem_mb": 256})
    assert result["mem_mb"] == 256
    assert any(c[:2] == ["systemctl", "restart"] for c in fake_systemctl)


def test_set_mem_limit_requires_enabled_first(account_with_home):
    with pytest.raises(RuntimeError):
        redisacct.set_mem_limit({"username": "demo1", "mem_mb": 128})


def test_disable_redis_stops_and_persists_disabled(account_with_home, fake_systemctl):
    redisacct.enable_redis({"username": "demo1"})
    fake_systemctl.clear()
    result = redisacct.disable_redis({"username": "demo1"})
    assert result["enabled"] is False
    assert any(c[:2] == ["systemctl", "disable"] for c in fake_systemctl)


def test_flush_calls_redis_cli_flushall(account_with_home, fake_redis_cli):
    redisacct.enable_redis({"username": "demo1"})
    fake_redis_cli.clear()
    result = redisacct.flush({"username": "demo1"})
    assert result["status"] == "flushed"
    assert any("FLUSHALL" in c for c in fake_redis_cli)


def test_flush_requires_enabled_first(account_with_home):
    with pytest.raises(RuntimeError):
        redisacct.flush({"username": "demo1"})


def test_get_status_reports_not_provisioned_when_never_enabled(account_with_home):
    result = redisacct.get_status({"username": "demo1"})
    assert result["provisioned"] is False
    assert result["enabled"] is False


def test_get_status_reports_used_memory(account_with_home):
    redisacct.enable_redis({"username": "demo1"})
    result = redisacct.get_status({"username": "demo1"})
    assert result["used_memory_human"] == "1.00M"
    assert result["used_memory_bytes"] == "1048576"


def test_get_connection_info_returns_socket_and_predis_params(account_with_home):
    redisacct.enable_redis({"username": "demo1"})
    info = redisacct.get_connection_info({"username": "demo1"})
    assert info["socket_path"] == redisacct.socket_path("demo1")
    assert info["predis_parameters"] == {"scheme": "unix", "path": redisacct.socket_path("demo1")}


def test_terminate_account_redis_removes_unit_and_row(account_with_home):
    result = redisacct.enable_redis({"username": "demo1"})
    unit_name = result["unit"]
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        redisacct.terminate_account_redis(account)
    with write_session() as session:
        assert session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id)) is None
    assert not redisacct.appunits.unit_path(unit_name).exists()


def test_terminate_account_redis_is_idempotent_when_never_enabled(account_with_home):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        redisacct.terminate_account_redis(account)  # must not raise
