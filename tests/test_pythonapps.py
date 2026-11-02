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
def fixed_app_env_key(monkeypatch, tmp_path):
    monkeypatch.setitem(pythonapps.appcrypto.settings.secrets, "APP_ENV_KEY", Fernet.generate_key().decode("ascii"))
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setattr(pythonapps.appunits, "UNITS_DIR", units)
    # Start-health polling: no real waiting in unit tests.
    monkeypatch.setattr(pythonapps.appunits, "START_SETTLE_SECONDS", 0)
    monkeypatch.setattr(pythonapps.appunits, "START_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(pythonapps.appunits, "START_POLL_SECONDS", 0)
    monkeypatch.setattr(pythonapps.appunits, "private_socket_ready", lambda *args: True)


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
        if args[:2] == ["systemctl", "show"]:
            return ProcResult(args=args, returncode=0,
                              stdout="ActiveState=active\nSubState=running\nNRestarts=0\nMainPID=4242\n", stderr="")
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
    monkeypatch.setattr(pythonapps.account_exec, "wrap", lambda username, argv, **kwargs: argv)

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


def test_legacy_bootstrap_failure_keeps_socket_route_and_aborts_migration(
    account_with_domain, stub_ols, monkeypatch
):
    result = pythonapps.create_app({"username":"demo1", "domain":"demo1.example",
                                    "name":"secure-migrate", "entry_point":"app:app"})
    with write_session() as session:
        row = session.get(PythonApp, result["id"])
        row.backend_mode = "legacy"
        row.enabled = True
    stopped = []
    monkeypatch.setattr(pythonapps.appunits, "enable_start_app",
                        lambda _unit: (_ for _ in ()).throw(RuntimeError("start failed")))
    monkeypatch.setattr(pythonapps.appunits, "stop_disable_app", lambda unit: stopped.append(unit))

    with pytest.raises(pythonapps.appunits.AppSocketMigrationError):
        pythonapps.bootstrap_all_python_apps()

    with write_session() as session:
        assert session.get(PythonApp, result["id"]).backend_mode == "socket"
    assert stopped == [result["unit"]]
    assert stub_ols.count("demo1") >= 2


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


def test_environment_timeout_rolls_back_application_and_returns_actionable_error(account_with_domain, monkeypatch):
    import subprocess
    timeouts = []
    def timed_out(args, **kwargs):
        timeouts.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])
    monkeypatch.setattr(pythonapps, 'run', timed_out)
    with pytest.raises(ValidationError, match='Check account resource limits and retry'):
        pythonapps.create_app({'username':'demo1','domain':'demo1.example','name':'timeout-test','entry_point':'app:app'})
    assert timeouts == [180]
    with write_session() as db:
        assert db.scalar(select(PythonApp.id)) is None


def test_create_python_app_creates_app_dir_and_log_file(account_with_domain):
    result = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    assert os.path.isdir(result["app_dir"])
    assert os.path.exists(pythonapps._log_path("demo1", "my-api"))


def test_unit_execstart_uses_gunicorn_for_wsgi(account_with_domain):
    result = pythonapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app", "app_type": "wsgi",
    })
    content = pythonapps.appunits.unit_path(result["unit"]).read_text()
    # Regression: "--bind fd://3" duplicated the LISTEN_FDS listener gunicorn
    # already adopts; it closed fd 3 after the first copy and crash-looped
    # with EBADF. The socket-activated unit must not name the fd again.
    assert "--bind" not in content
    assert "bin/gunicorn app:app" in content


def test_unit_execstart_uses_uvicorn_for_asgi(account_with_domain):
    result = pythonapps.create_app({
        "username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app", "app_type": "asgi",
    })
    content = pythonapps.appunits.unit_path(result["unit"]).read_text()
    assert "uvicorn app:app --fd 3" in content


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
    with pytest.raises(ValidationError, match='NodeJS app'):
        pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "p1", "entry_point": "app:app"})


def test_create_rejects_duplicate_domain_binding(account_with_domain):
    pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "app-one", "entry_point": "a:a"})
    with pytest.raises(ValidationError, match='Python app'):
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


@pytest.mark.parametrize("terminate", [False, True])
def test_failed_teardown_retains_recovery_metadata(account_with_domain, monkeypatch, terminate):
    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    def fail(name):
        raise RuntimeError("stop failed")
    monkeypatch.setattr(pythonapps.appunits, "remove_app_unit", fail)
    with pytest.raises(RuntimeError, match="stop failed"):
        if terminate:
            with write_session() as session:
                account = session.scalar(select(Account).where(Account.username == "demo1"))
            pythonapps.terminate_account_python_apps(account)
        else:
            pythonapps.delete_app({"username": "demo1", "id": created["id"]})
    with write_session() as session:
        assert session.get(PythonApp, created["id"]) is not None


@pytest.mark.parametrize('provision_fails', [False, True])
def test_app_retires_welcome_only_after_route_provisioning(account_with_domain, monkeypatch, provision_fails):
    from daemon import domain_welcome, site_templates
    monkeypatch.setattr(site_templates, 'render_domain_welcome', lambda domain: '<!doctype html><title>Welcome</title>')
    docroot = account_with_domain['home'] / 'public_html'
    docroot.mkdir()
    assert domain_welcome.ensure_welcome('demo1', 'demo1.example', str(docroot))
    params = {'username': 'demo1', 'domain': 'demo1.example', 'name': 'welcome-app', 'entry_point': 'app:app'}
    if provision_fails:
        def fail(_account):
            raise RuntimeError('Test route validation failed')
        monkeypatch.setattr(pythonapps.ols, 'refresh_vhost', fail)
        with pytest.raises(RuntimeError, match='Test route validation failed'):
            pythonapps.create_app(params)
        assert (docroot / 'welcome.html').exists()
    else:
        pythonapps.create_app(params)
        assert not (docroot / 'welcome.html').exists()


def test_start_reports_crash_looping_app_as_failed_and_stops_it(account_with_domain, monkeypatch):
    """Regression: start returned 200 active:'active' while systemd was
    restarting the app every two seconds."""
    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    log = pythonapps._log_path("demo1", "my-api")
    with open(log, "w") as handle:
        handle.write("OSError: [Errno 9] Bad file descriptor\n")
    restarts = iter(range(1000))
    calls = []

    def crash_looping(args, timeout=30, **kwargs):
        calls.append(args)
        if args[:2] == ["systemctl", "show"]:
            return ProcResult(args=args, returncode=0,
                              stdout=f"ActiveState=active\nSubState=running\nNRestarts={next(restarts)}\nMainPID=1\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(pythonapps.appunits, "run", crash_looping)
    monkeypatch.setattr(pythonapps.appunits, "tail_log_file", lambda path, lines=10: open(path).read().splitlines())
    with pytest.raises(pythonapps.appunits.AppStartError, match="Bad file descriptor"):
        pythonapps.start_app({"username": "demo1", "id": created["id"]})
    assert any(c[:3] == ["systemctl", "disable", "--now"] for c in calls)
    with write_session() as session:
        assert session.get(PythonApp, created["id"]).enabled is False


def test_wait_until_running_requires_a_stable_main_process(monkeypatch):
    states = iter([
        "ActiveState=activating\nSubState=auto-restart\nNRestarts=3\nMainPID=0\n",
        "ActiveState=active\nSubState=running\nNRestarts=4\nMainPID=10\n",
        "ActiveState=active\nSubState=running\nNRestarts=4\nMainPID=10\n",
    ])
    monkeypatch.setattr(pythonapps.appunits, "run", lambda args, timeout=30, **kwargs: ProcResult(
        args=args, returncode=0, stdout=next(states), stderr=""))
    assert pythonapps.appunits.wait_until_running("boron-python-demo1-1.service", "python", 1)["active"] == "active"


def test_pip_install_failure_is_a_short_error_without_pip_output(account_with_domain, monkeypatch, caplog):
    """Same contract as npm install (round 1 core): pip's output is tenant
    text, logged instead of returned (and no longer an opaque 502)."""
    import logging

    created = pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    with open(f"{created['app_dir']}/requirements.txt", "w") as f:
        f.write("no-such-package==0\n")

    def failing_pip(args, timeout=30, **kwargs):
        return ProcResult(args=args, returncode=1, stdout="",
                          stderr="ERROR: No matching distribution found for no-such-package==0")

    monkeypatch.setattr(pythonapps, "run", failing_pip)
    with caplog.at_level(logging.WARNING, logger="borond.pythonapps"):
        with pytest.raises(ValidationError) as excinfo:
            pythonapps.pip_install({"username": "demo1", "id": created["id"]})
    message = str(excinfo.value)
    assert message.startswith("pip install failed (exit status 1)")
    assert "No matching distribution" not in message
    assert "No matching distribution" in caplog.text


def test_environment_install_failure_is_a_validation_error_and_registers_nothing(account_with_domain, monkeypatch):
    def failing_gunicorn_install(args, timeout=30, **kwargs):
        failed = any("gunicorn" in str(arg) for arg in args)
        return ProcResult(args=args, returncode=1 if failed else 0, stdout="", stderr="network unreachable" if failed else "")

    monkeypatch.setattr(pythonapps, "run", failing_gunicorn_install)
    with pytest.raises(ValidationError, match="Installing gunicorn and uvicorn failed") as excinfo:
        pythonapps.create_app({"username": "demo1", "domain": "demo1.example", "name": "my-api", "entry_point": "app:app"})
    assert "network unreachable" not in str(excinfo.value)
    with write_session() as session:
        assert session.scalar(select(PythonApp)) is None


def test_unknown_python_app_is_not_found(account_with_domain):
    from shared.validation import NotFoundError

    with pytest.raises(NotFoundError):
        pythonapps.get_app({"username": "demo1", "id": 999})
