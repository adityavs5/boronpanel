import os
import pwd as real_pwd

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import nodeapps
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, Domain, NodeApp
from shared.validation import ValidationError


@pytest.fixture(autouse=True)
def fixed_app_env_key(monkeypatch, tmp_path):
    """appcrypto.get_key() auto-generates and PERSISTS a key to the real
    /etc/boron/secrets.env on first use if none is already configured
    (daemon/appcrypto.py) -- never acceptable as a side effect of running
    the test suite. Every test in this file gets a fixed in-memory key via
    settings.secrets instead, so encrypt_env/decrypt_env never touch disk."""
    monkeypatch.setitem(nodeapps.appcrypto.settings.secrets, "APP_ENV_KEY", Fernet.generate_key().decode("ascii"))
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setattr(nodeapps.appunits, "UNITS_DIR", units)


@pytest.fixture()
def stub_ols(monkeypatch):
    calls = []
    monkeypatch.setattr(nodeapps.ols, "refresh_vhost", lambda account: calls.append(account.username))
    return calls


@pytest.fixture()
def fake_systemctl(monkeypatch):
    calls = []

    def fake_run(args, timeout=30, **kwargs):
        calls.append(args)
        if args[0] == "systemctl" and args[1] in ("is-active", "is-enabled"):
            return ProcResult(args=args, returncode=0, stdout="active\n" if args[1] == "is-active" else "enabled\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(nodeapps.appunits, "run", fake_run)
    return calls


@pytest.fixture()
def account_with_domain(isolated_db, tmp_path, monkeypatch, stub_ols, fake_systemctl):
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(nodeapps.settings, "home_base", str(home_base))
    monkeypatch.setattr(nodeapps.settings, "app_env_dir", str(tmp_path / "app-env"))
    monkeypatch.setattr(nodeapps.settings, "node_base_dir", str(tmp_path / "nodejs"))

    account_home = home_base / "demo1"
    account_home.mkdir()
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), "/usr/sbin/nologin"))
    monkeypatch.setattr(nodeapps.pwd, "getpwnam", lambda name: fake_pw)

    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(account_home / "public_html")))
        session.add(Domain(account_id=account.id, domain="second.example", kind="addon", docroot=str(account_home / "second.example")))

    return {"home": account_home}


def test_create_node_app_happy_path(account_with_domain, stub_ols):
    result = nodeapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api",
        "entry_point": "server.js", "node_version": "20", "env_vars": {"FOO": "bar"},
    })
    assert result["domain"] == "demo1.example"
    assert result["name"] == "my-api"
    assert result["node_version"] == "20"
    assert result["env_vars"] == {"FOO": "bar"}
    assert result["port"] >= nodeapps.settings.app_port_range_start
    assert "demo1" in stub_ols  # ols.refresh_vhost was called to wire the proxy context


def test_create_node_app_creates_app_dir_and_log_file(account_with_domain):
    result = nodeapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js",
    })
    assert os.path.isdir(result["app_dir"])
    log_path = nodeapps._log_path("demo1", "my-api")
    assert os.path.exists(log_path)


def test_create_node_app_writes_systemd_unit_and_env_file(account_with_domain):
    result = nodeapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js",
        "env_vars": {"SECRET": "hunter2"},
    })
    unit_path = nodeapps.appunits.unit_path(result["unit"])
    content = unit_path.read_text()
    assert f"boron-{'demo1'}.slice" in content
    assert "User=demo1" in content
    assert "Restart=on-failure" in content
    env_path = nodeapps.appunits.env_file_path(result["unit"])
    env_content = env_path.read_text()
    assert 'SECRET="hunter2"' in env_content
    assert f'PORT="{result["port"]}"' in env_content
    # env file must be root-only -- the whole point of encrypting at rest
    import stat
    assert stat.S_IMODE(os.stat(env_path).st_mode) == 0o600


def test_env_vars_are_encrypted_at_rest_in_db(account_with_domain):
    result = nodeapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js",
        "env_vars": {"SECRET": "hunter2"},
    })
    with write_session() as session:
        row = session.get(NodeApp, result["id"])
        assert "hunter2" not in row.env_vars
        assert row.env_vars  # non-empty ciphertext


def test_create_node_app_rejects_domain_not_owned_by_account(account_with_domain):
    with pytest.raises(RuntimeError):
        nodeapps.create_app({
            "username": "demo1", "domain": "not-mine.example", "name": "my-api", "entry_point": "server.js",
        })


def test_create_node_app_rejects_duplicate_domain_binding(account_with_domain):
    nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-one", "entry_point": "a.js"})
    with pytest.raises(RuntimeError):
        nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-two", "entry_point": "b.js"})


def test_create_node_app_rejects_duplicate_name(account_with_domain):
    nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-one", "entry_point": "a.js"})
    with pytest.raises(RuntimeError):
        nodeapps.create_app({"username": "demo1", "domain": "second.example", "name": "app-one", "entry_point": "b.js"})


def test_create_node_app_rejects_bad_node_version(account_with_domain):
    with pytest.raises(ValidationError):
        nodeapps.create_app({
            "username": "demo1", "domain": "demo1.example", "name": "my-api",
            "entry_point": "server.js", "node_version": "99",
        })


def test_create_node_app_rejects_traversal_entry_point(account_with_domain):
    with pytest.raises(ValidationError):
        nodeapps.create_app({
            "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "../../etc/passwd",
        })


def test_create_node_app_rejects_reserved_env_key(account_with_domain):
    with pytest.raises(ValidationError):
        nodeapps.create_app({
            "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js",
            "env_vars": {"PORT": "1234"},
        })


def test_create_node_app_compensates_db_row_on_ols_failure(account_with_domain, monkeypatch):
    """Real bug found live during Phase 7a's own first end-to-end test: an
    OLS template error failed refresh_vhost() AFTER the NodeApp row was
    already committed, leaving an orphaned row + unit file that blocked
    every subsequent create() for the same domain/name with a stale
    "already has an app bound" error -- same compensation discipline
    handlers_domain.add_domain already established for exactly this
    failure shape."""
    monkeypatch.setattr(nodeapps.ols, "refresh_vhost", lambda account: (_ for _ in ()).throw(RuntimeError("OLS config transaction failed")))

    with pytest.raises(RuntimeError):
        nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})

    with write_session() as session:
        assert session.scalar(select(NodeApp).where(NodeApp.domain == "demo1.example")) is None

    unit = nodeapps.appunits.unit_name("node", "demo1", 1)
    assert not nodeapps.appunits.unit_path(unit).exists()


def test_two_different_apps_get_different_ports(account_with_domain):
    a = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-one", "entry_point": "a.js"})
    b = nodeapps.create_app({"username": "demo1", "domain": "second.example", "name": "app-two", "entry_point": "b.js"})
    assert a["port"] != b["port"]


def test_start_stop_restart_lifecycle(account_with_domain, fake_systemctl):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    app_id = created["id"]

    started = nodeapps.start_app({"username": "demo1", "id": app_id})
    assert started["active"] == "active"
    assert any(c[:2] == ["systemctl", "enable"] for c in fake_systemctl)

    nodeapps.stop_app({"username": "demo1", "id": app_id})
    assert any(c[:2] == ["systemctl", "disable"] for c in fake_systemctl)
    with write_session() as session:
        row = session.get(NodeApp, app_id)
        assert row.enabled is False

    nodeapps.restart_app({"username": "demo1", "id": app_id})
    assert any(c[:2] == ["systemctl", "restart"] for c in fake_systemctl)


def test_update_app_rewrites_unit_and_restarts_if_enabled(account_with_domain, fake_systemctl):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    nodeapps.start_app({"username": "demo1", "id": created["id"]})
    fake_systemctl.clear()

    updated = nodeapps.update_app({"username": "demo1", "id": created["id"], "entry_point": "index.js"})
    assert updated["entry_point"] == "index.js"
    assert any(c[:2] == ["systemctl", "restart"] for c in fake_systemctl)

    unit_path = nodeapps.appunits.unit_path(created["unit"])
    assert "index.js" in unit_path.read_text()


def test_update_app_does_not_restart_if_stopped(account_with_domain, fake_systemctl):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    fake_systemctl.clear()
    nodeapps.update_app({"username": "demo1", "id": created["id"], "entry_point": "index.js"})
    assert not any(c[:2] == ["systemctl", "restart"] for c in fake_systemctl)


def test_delete_app_removes_unit_and_frees_domain(account_with_domain, stub_ols):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    unit_path = nodeapps.appunits.unit_path(created["unit"])
    assert unit_path.exists()

    result = nodeapps.delete_app({"username": "demo1", "id": created["id"]})
    assert result["status"] == "deleted"
    assert not unit_path.exists()

    # domain is free again for a new app
    again = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "another", "entry_point": "server.js"})
    assert again["domain"] == "demo1.example"


def test_delete_app_leaves_app_dir_on_disk(account_with_domain):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    app_dir = created["app_dir"]
    nodeapps.delete_app({"username": "demo1", "id": created["id"]})
    assert os.path.isdir(app_dir)


def test_get_logs_returns_empty_list_when_no_output_yet(account_with_domain):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    result = nodeapps.get_logs({"username": "demo1", "id": created["id"]})
    assert result["log_lines"] == []


def test_get_logs_tails_last_100_lines(account_with_domain):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    log_path = nodeapps._log_path("demo1", "my-api")
    with open(log_path, "w") as f:
        for i in range(150):
            f.write(f"line{i}\n")
    result = nodeapps.get_logs({"username": "demo1", "id": created["id"]})
    assert len(result["log_lines"]) == 100
    assert result["log_lines"][-1] == "line149"
    assert result["log_lines"][0] == "line50"


def test_list_apps_scoped_to_account(account_with_domain):
    nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-one", "entry_point": "a.js"})
    with write_session() as session:
        session.add(Account(username="demo2", uid=5002, gid=5002, status="active"))
        session.add(Domain(account_id=session.scalar(select(Account).where(Account.username == "demo2")).id,
                            domain="other.example", kind="primary", docroot="/home/demo2/public_html"))

    result = nodeapps.list_apps({"username": "demo1"})
    assert len(result["apps"]) == 1
    assert result["apps"][0]["name"] == "app-one"


def test_terminate_account_node_apps_removes_units_and_rows(account_with_domain):
    created = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "server.js"})
    unit_path = nodeapps.appunits.unit_path(created["unit"])
    assert unit_path.exists()

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        nodeapps.terminate_account_node_apps(account)

    assert not unit_path.exists()
    with write_session() as session:
        assert session.scalar(select(NodeApp).where(NodeApp.account_id == account.id)) is None


def test_terminate_account_node_apps_is_idempotent_with_no_apps(account_with_domain):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        nodeapps.terminate_account_node_apps(account)  # must not raise
