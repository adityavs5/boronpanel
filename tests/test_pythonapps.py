import os
import pwd as real_pwd

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import pythonapps
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, Domain, PythonApp
from shared.validation import ValidationError


@pytest.fixture(autouse=True)
def fixed_app_env_key(monkeypatch):
    monkeypatch.setitem(pythonapps.appcrypto.settings.secrets, "APP_ENV_KEY", Fernet.generate_key().decode("ascii"))


@pytest.fixture()
def stub_ols(monkeypatch):
    calls = []
    monkeypatch.setattr(pythonapps.ols, "refresh_vhost", lambda account: calls.append(account.username))
    return calls


@pytest.fixture()
def fake_systemctl(monkeypatch):
    calls = []

    def fake_run(args, timeout=30, **kwargs):
        calls.append(args)
        if args[0] == "systemctl" and args[1] in ("is-active", "is-enabled"):
            return ProcResult(args=args, returncode=0, stdout="active\n" if args[1] == "is-active" else "enabled\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pythonapps.appunits, "run", fake_run)
    return calls


@pytest.fixture()
def fake_venv(monkeypatch):
    """create_app()/pip_install() shell out to `runuser -u <user> -- ...
    python3 -m venv`/`pip install`, which would try to touch a real
    virtualenv path with a nonexistent test user otherwise -- record the
    calls and fake success instead of hitting the real filesystem/network."""
    calls = []

    def fake_run(args, timeout=30, cwd=None, **kwargs):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pythonapps, "run", fake_run)
    return calls


@pytest.fixture()
def account_with_domain(isolated_db, tmp_path, monkeypatch, stub_ols, fake_systemctl, fake_venv):
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(pythonapps.settings, "home_base", str(home_base))
    monkeypatch.setattr(pythonapps.settings, "app_env_dir", str(tmp_path / "app-env"))

    account_home = home_base / "demo1"
    account_home.mkdir()
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), "/usr/sbin/nologin"))
    monkeypatch.setattr(pythonapps.pwd, "getpwnam", lambda name: fake_pw)

    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(account_home / "public_html")))
        session.add(Domain(account_id=account.id, domain="second.example", kind="addon", docroot=str(account_home / "second.example")))

    return {"home": account_home}


def test_create_python_app_happy_path(account_with_domain, stub_ols, fake_venv):
    result = pythonapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api",
        "entry_point": "app:app", "app_type": "asgi", "env_vars": {"FOO": "bar"},
    })
    assert result["domain"] == "demo1.example"
    assert result["app_type"] == "asgi"
    assert result["env_vars"] == {"FOO": "bar"}
    assert "demo1" in stub_ols
    # venv creation + gunicorn/uvicorn install both happened
    assert any("venv" in a for a in fake_venv[0])
    assert any(a[-2:] == ["gunicorn", "uvicorn"] for a in fake_venv)


def test_create_python_app_compensates_db_row_on_ols_failure(account_with_domain, monkeypatch):
    """Same real bug/fix as nodeapps.py's identical test -- see its
    docstring."""
    monkeypatch.setattr(pythonapps.ols, "refresh_vhost", lambda account: (_ for _ in ()).throw(RuntimeError("OLS config transaction failed")))

    with pytest.raises(RuntimeError):
        pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})

    with write_session() as session:
        assert session.scalar(select(PythonApp).where(PythonApp.domain == "demo1.example")) is None


def test_create_python_app_defaults_to_wsgi(account_with_domain):
    result = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    assert result["app_type"] == "wsgi"


def test_create_python_app_creates_app_dir_and_log_file(account_with_domain):
    result = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    assert os.path.isdir(result["app_dir"])
    assert os.path.exists(pythonapps._log_path("demo1", "my-api"))


def test_unit_execstart_uses_gunicorn_for_wsgi(account_with_domain):
    result = pythonapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app", "app_type": "wsgi",
    })
    content = pythonapps.appunits.unit_path(result["unit"]).read_text()
    assert "gunicorn --bind 127.0.0.1" in content
    assert "app:app" in content


def test_unit_execstart_uses_uvicorn_for_asgi(account_with_domain):
    result = pythonapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app", "app_type": "asgi",
    })
    content = pythonapps.appunits.unit_path(result["unit"]).read_text()
    assert "uvicorn app:app --host 127.0.0.1" in content


def test_env_vars_encrypted_at_rest(account_with_domain):
    result = pythonapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app",
        "env_vars": {"SECRET": "hunter2"},
    })
    with write_session() as session:
        row = session.get(PythonApp, result["id"])
        assert "hunter2" not in row.env_vars


def test_create_rejects_invalid_entry_point(account_with_domain):
    with pytest.raises(ValidationError):
        pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "not valid!!"})


def test_create_rejects_bad_app_type(account_with_domain):
    with pytest.raises(ValidationError):
        pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app", "app_type": "cgi"})


def test_create_rejects_domain_already_bound_to_node_app(account_with_domain):
    import daemon.nodeapps as nodeapps

    # NodeApp lifecycle isn't under test here -- only that PythonApp's own
    # domain-uniqueness check sees a NodeApp row bound to the same domain.
    # nodeapps.py and pythonapps.py both `from daemon import appunits, ols`
    # and both `import pwd`/`from shared.config import settings` -- these
    # are the same shared module/singleton objects either way, so the
    # account_with_domain fixture's patches already cover nodeapps.create_app
    # too, with no extra patching needed here.
    result = nodeapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "n1", "entry_point": "a.js"})
    assert result["domain"] == "demo1.example"
    with pytest.raises(RuntimeError):
        pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "p1", "entry_point": "app:app"})


def test_create_rejects_duplicate_domain_binding(account_with_domain):
    pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-one", "entry_point": "a:a"})
    with pytest.raises(RuntimeError):
        pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-two", "entry_point": "b:b"})


def test_lifecycle_start_stop_restart(account_with_domain, fake_systemctl):
    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    started = pythonapps.start_app({"username": "demo1", "id": created["id"]})
    assert started["active"] == "active"
    assert any(c[:2] == ["systemctl", "enable"] for c in fake_systemctl)

    pythonapps.stop_app({"username": "demo1", "id": created["id"]})
    assert any(c[:2] == ["systemctl", "disable"] for c in fake_systemctl)

    pythonapps.restart_app({"username": "demo1", "id": created["id"]})
    assert any(c[:2] == ["systemctl", "restart"] for c in fake_systemctl)


def test_pip_install_skips_when_no_requirements_file(account_with_domain, fake_venv):
    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    result = pythonapps.pip_install({"username": "demo1", "id": created["id"]})
    assert result["status"] == "skipped"


def test_pip_install_runs_when_requirements_file_present(account_with_domain, fake_venv):
    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    with open(f"{created['app_dir']}/requirements.txt", "w") as f:
        f.write("fastapi\n")
    fake_venv.clear()
    result = pythonapps.pip_install({"username": "demo1", "id": created["id"]})
    assert result["status"] == "installed"
    assert any("requirements.txt" in a for a in fake_venv[-1])


def test_delete_app_removes_unit_and_frees_domain(account_with_domain):
    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    unit_path = pythonapps.appunits.unit_path(created["unit"])
    assert unit_path.exists()
    pythonapps.delete_app({"username": "demo1", "id": created["id"]})
    assert not unit_path.exists()
    again = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "another", "entry_point": "app:app"})
    assert again["domain"] == "demo1.example"


def test_terminate_account_python_apps(account_with_domain):
    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    unit_path = pythonapps.appunits.unit_path(created["unit"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        pythonapps.terminate_account_python_apps(account)
    assert not unit_path.exists()
    with write_session() as session:
        assert session.scalar(select(PythonApp).where(PythonApp.account_id == account.id)) is None
