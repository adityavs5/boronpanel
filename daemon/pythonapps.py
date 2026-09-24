"""Phase 7a feature 2: per-account Python WSGI/ASGI app hosting.

Same shape and lifecycle as daemon/nodeapps.py (one systemd unit per app,
Slice=-assigned to the account's own cgroup, restarted on crash, 1:1
domain binding reverse-proxied by OLS -- see nodeapps.py's module
docstring for the shared reasoning, not repeated here). The two concrete
differences: a per-app virtualenv under the account's own home (never a
shared/system venv -- goal's explicit requirement) instead of a shared
Node install selected by version, and a choice of launch command (gunicorn
for WSGI, uvicorn for ASGI) instead of a Node version.

gunicorn+uvicorn are installed into the app's own venv at create() time
(both, regardless of app_type -- so switching a running app between wsgi/
asgi later, e.g. Flask to FastAPI, never requires recreating the venv) --
unlike the customer's own application dependencies (requirements.txt),
which are a separate, explicit `pip_install` action (same "don't run
customer-supplied installs implicitly" posture as nodeapps.npm_install).
"""
from __future__ import annotations

import os
import pwd
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, NodeApp, PythonApp
from shared.validation import (
    ValidationError,
    validate_app_name,
    validate_domain,
    validate_env_vars,
    validate_python_entry_point,
    validate_username,
)

from daemon import account_mutation, appcrypto, appunits, ols, resource_limits, safeio
from daemon.portalloc import allocate_port
from daemon.procutil import run

KIND = "python"
APP_TYPES = ("wsgi", "asgi")


def _app_dir(username: str, name: str) -> str:
    return f"{settings.home_base}/{username}/pythonapps/{name}"


def _venv_dir(username: str, name: str) -> str:
    return f"{_app_dir(username, name)}/venv"


def _log_path(username: str, name: str) -> str:
    return f"{settings.home_base}/{username}/logs/python/{name}.log"


def _validate_app_type(value: str) -> str:
    if value not in APP_TYPES:
        raise ValidationError(f"app_type must be one of {APP_TYPES}")
    return value


def _get_account(session, username: str) -> Account:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    return account


def _get_domain_for_account(session, account: Account, domain_name: str) -> Domain:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
    if domain_row is None:
        raise RuntimeError(f"domain '{domain_name}' not found for account '{account.username}'")
    return domain_row


def _assert_domain_free(session, domain_name: str) -> None:
    if session.scalar(select(NodeApp).where(NodeApp.domain == domain_name)) is not None:
        raise RuntimeError(f"domain '{domain_name}' already has a NodeJS app bound to it")
    if session.scalar(select(PythonApp).where(PythonApp.domain == domain_name)) is not None:
        raise RuntimeError(f"domain '{domain_name}' already has a Python app bound to it")


def _get_row(session, username: str, app_id: int) -> tuple[Account, PythonApp]:
    account = _get_account(session, username)
    row = session.scalar(select(PythonApp).where(PythonApp.id == app_id, PythonApp.account_id == account.id))
    if row is None:
        raise RuntimeError(f"Python app {app_id} not found for account '{username}'")
    return account, row


def _row_to_dict(row: PythonApp, username: str) -> dict:
    unit = appunits.unit_name(KIND, username, row.id)
    st = appunits.status(unit)
    return {
        "id": row.id,
        "account_id": row.account_id,
        "domain": row.domain,
        "name": row.name,
        "entry_point": row.entry_point,
        "app_type": row.app_type,
        "port": row.port,
        "env_vars": appcrypto.decrypt_env(row.env_vars) if row.env_vars else {},
        "enabled": row.enabled,
        "app_dir": _app_dir(username, row.name),
        "venv_dir": _venv_dir(username, row.name),
        "unit": unit,
        "active": st["active"],
        "unit_enabled": st["enabled"],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _provision_filesystem(username: str, name: str) -> None:
    pw = pwd.getpwnam(username)
    # Symlink-safe create+chown: these dirs are inside the account-writable
    # home, so a naive mkdir+chown is a root privesc primitive (daemon/safeio.py).
    home = os.path.realpath(f"{settings.home_base}/{username}")
    app_rel = os.path.relpath(_app_dir(username, name), home)
    safeio.secure_mkdirs(home, app_rel, pw.pw_uid, pw.pw_gid, 0o750)

    log_dir = safeio.secure_mkdirs(home, "logs/python", pw.pw_uid, pw.pw_gid, 0o750)
    safeio.secure_ensure_file_beneath(home, "logs/python", os.path.basename(_log_path(username, name)), pw.pw_uid, pw.pw_gid, 0o640)


def _create_venv(username: str, name: str) -> None:
    """Creates the venv (idempotent -- `python3 -m venv` on an existing
    venv dir just re-verifies/repairs it) and installs gunicorn+uvicorn
    into it, all as the account's own uid so the venv and everything
    inside it is owned by the account, never root."""
    app_dir = _app_dir(username, name)
    venv_dir = _venv_dir(username, name)
    result = run(["runuser", "-u", username, "--", settings.python_bin, "-m", "venv", venv_dir], cwd=app_dir, timeout=60)
    if not result.ok:
        raise RuntimeError(f"failed to create virtualenv: {result.stderr.strip()[:2000] or result.stdout.strip()[:2000]}")

    pip = f"{venv_dir}/bin/pip"
    result = run(
        ["runuser", "-u", username, "--", pip, "install", "--quiet", "--disable-pip-version-check", "gunicorn", "uvicorn"],
        cwd=app_dir, timeout=180,
    )
    if not result.ok:
        raise RuntimeError(f"failed to install gunicorn/uvicorn: {result.stderr.strip()[:2000] or result.stdout.strip()[:2000]}")


def _exec_start(venv_dir: str, app_type: str, entry_point: str, port: int) -> str:
    if app_type == "asgi":
        return f"{venv_dir}/bin/uvicorn {entry_point} --host 127.0.0.1 --port {port}"
    return f"{venv_dir}/bin/gunicorn --bind 127.0.0.1:{port} {entry_point}"


def _write_unit(username: str, app_id: int, name: str, entry_point: str, app_type: str, port: int, env_vars: dict) -> str:
    unit = appunits.unit_name(KIND, username, app_id)
    app_dir = _app_dir(username, name)
    venv_dir = _venv_dir(username, name)
    log_path = _log_path(username, name)

    env = dict(env_vars)
    env["PORT"] = str(port)
    env.setdefault("PYTHONUNBUFFERED", "1")
    appunits.write_env_file(unit, env)

    content = (
        "[Unit]\n"
        f"Description=Boron Python app '{name}' for account '{username}'\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={username}\n"
        f"Group={username}\n"
        f"WorkingDirectory={app_dir}\n"
        f"EnvironmentFile={appunits.env_file_path(unit)}\n"
        f"ExecStart={appunits.logged_exec(_exec_start(venv_dir, app_type, entry_point, port), log_path)}\n"
        f"Slice=boron-{username}.slice\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "StandardOutput=null\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    appunits.write_unit_file(unit, content)
    appunits.daemon_reload()
    return unit


@account_mutation.locked
def create_app(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    name = validate_app_name(params["name"])
    entry_point = validate_python_entry_point(params["entry_point"])
    app_type = _validate_app_type(params.get("app_type", "wsgi"))
    env_vars = validate_env_vars(params.get("env_vars") or {})

    with write_session() as session:
        account = _get_account(session, username)
        _get_domain_for_account(session, account, domain_name)
        _assert_domain_free(session, domain_name)
        if session.scalar(select(PythonApp).where(PythonApp.account_id == account.id, PythonApp.name == name)) is not None:
            raise RuntimeError(f"app name '{name}' already exists for account '{username}'")
        resource_limits.require_capacity(session, account.id, "app")

        port = allocate_port(session)
        row = PythonApp(
            account_id=account.id,
            domain=domain_name,
            name=name,
            entry_point=entry_point,
            app_type=app_type,
            port=port,
            env_vars=appcrypto.encrypt_env(env_vars),
            enabled=False,
        )
        session.add(row)
        session.flush()
        app_id = row.id
        account_snapshot = account

    try:
        _provision_filesystem(username, name)
        _create_venv(username, name)
        _write_unit(username, app_id, name, entry_point, app_type, port, env_vars)
        ols.refresh_vhost(account_snapshot)
    except Exception:
        # Same compensation discipline as nodeapps.create_app / handlers_domain.
        # add_domain -- a failed venv-create/OLS-apply must not leave an
        # orphaned DB row + unit file blocking every subsequent create()
        # for the same domain/name.
        with write_session() as session:
            orphan = session.get(PythonApp, app_id)
            if orphan is not None:
                session.delete(orphan)
        appunits.remove_unit(appunits.unit_name(KIND, username, app_id))
        raise

    with write_session() as session:
        row = session.get(PythonApp, app_id)
        return _row_to_dict(row, username)


def update_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])

    with write_session() as session:
        account, row = _get_row(session, username, app_id)
        if "entry_point" in params:
            row.entry_point = validate_python_entry_point(params["entry_point"])
        if "app_type" in params:
            row.app_type = _validate_app_type(params["app_type"])
        if "env_vars" in params:
            row.env_vars = appcrypto.encrypt_env(validate_env_vars(params["env_vars"] or {}))
        session.flush()
        name, entry_point, app_type, port, enabled = row.name, row.entry_point, row.app_type, row.port, row.enabled
        env_vars = appcrypto.decrypt_env(row.env_vars) if row.env_vars else {}

    unit = _write_unit(username, app_id, name, entry_point, app_type, port, env_vars)
    if enabled:
        appunits.restart(unit)

    with write_session() as session:
        row = session.get(PythonApp, app_id)
        return _row_to_dict(row, username)


def delete_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])

    with write_session() as session:
        account, row = _get_row(session, username, app_id)
        domain_name, name = row.domain, row.name
        appunits.remove_unit(appunits.unit_name(KIND, username, app_id))
        session.delete(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return {"id": app_id, "domain": domain_name, "name": name, "status": "deleted"}


def start_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, row = _get_row(session, username, app_id)
        row.enabled = True
    appunits.enable_start(appunits.unit_name(KIND, username, app_id))
    return get_app({"username": username, "id": app_id})


def stop_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, row = _get_row(session, username, app_id)
        row.enabled = False
    appunits.stop_disable(appunits.unit_name(KIND, username, app_id))
    return get_app({"username": username, "id": app_id})


def restart_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, _row = _get_row(session, username, app_id)
    appunits.restart(appunits.unit_name(KIND, username, app_id))
    return get_app({"username": username, "id": app_id})


def pip_install(params: dict) -> dict:
    """Explicit action -- installs the customer's own requirements.txt
    (if present) into the app's already-created venv, as the account's
    own uid. Missing requirements.txt is not an error (an app with no
    external dependencies beyond gunicorn/uvicorn is a normal, valid
    state, e.g. a single-file stdlib-only ASGI app)."""
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, row = _get_row(session, username, app_id)
        name = row.name

    app_dir = _app_dir(username, name)
    requirements_path = Path(app_dir) / "requirements.txt"
    if not requirements_path.exists():
        return {"id": app_id, "status": "skipped", "output": "no requirements.txt found in app directory"}

    pip = f"{_venv_dir(username, name)}/bin/pip"
    result = run(
        ["runuser", "-u", username, "--", pip, "install", "--quiet", "--disable-pip-version-check", "-r", "requirements.txt"],
        cwd=app_dir, timeout=300,
    )
    if not result.ok:
        raise RuntimeError(f"pip install failed: {result.stderr.strip()[:2000] or result.stdout.strip()[:2000]}")
    return {"id": app_id, "status": "installed", "output": result.stdout[-4000:]}


def get_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, row = _get_row(session, username, app_id)
        return _row_to_dict(row, username)


def list_apps(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = _get_account(session, username)
        rows = session.scalars(select(PythonApp).where(PythonApp.account_id == account.id)).all()
        return {"apps": [_row_to_dict(r, username) for r in rows]}


def get_logs(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, row = _get_row(session, username, app_id)
        log_path = _log_path(username, row.name)
    return {"id": app_id, "log_lines": appunits.tail_log_file(log_path, lines=100)}


def terminate_account_python_apps(account: Account) -> None:
    """TERMINATE_HOOKS entry -- see nodeapps.terminate_account_node_apps's
    identical reasoning."""
    with write_session() as session:
        rows = session.scalars(select(PythonApp).where(PythonApp.account_id == account.id)).all()
        for row in rows:
            appunits.remove_unit(appunits.unit_name(KIND, account.username, row.id))
            session.delete(row)


def bootstrap_all_python_apps() -> None:
    """Run once at borond startup -- see nodeapps.bootstrap_all_node_apps's
    identical reasoning. Does NOT recreate the venv (assumed to already
    exist on disk from create()/pip_install() -- recreating it on every
    daemon restart would be needlessly slow and would silently blow away
    any customer-installed packages if the venv dir were ever partially
    missing)."""
    with write_session() as session:
        rows = session.scalars(select(PythonApp)).all()
        snapshot = [
            (r.id, r.account_id, r.name, r.entry_point, r.app_type, r.port, r.env_vars, r.enabled)
            for r in rows
        ]
        usernames = {a.id: a.username for a in session.scalars(select(Account)).all()}

    for app_id, account_id, name, entry_point, app_type, port, env_vars_enc, enabled in snapshot:
        username = usernames.get(account_id)
        if username is None:
            continue
        try:
            env_vars = appcrypto.decrypt_env(env_vars_enc) if env_vars_enc else {}
            unit = _write_unit(username, app_id, name, entry_point, app_type, port, env_vars)
            _provision_filesystem(username, name)
            if enabled:
                appunits.enable_start(unit)
        except Exception:
            import logging

            logging.getLogger("borond.pythonapps").exception(
                "failed to bootstrap Python app %d for account '%s'", app_id, username
            )
