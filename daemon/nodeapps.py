"""Phase 7a feature 1: per-account NodeJS app hosting.

One systemd unit per app (boron-node-{username}-{id}.service,
daemon/appunits.py), running as the account's own Linux user (never root),
assigned directly to that account's existing cgroup slice via `Slice=`
(daemon/cgroups.py already creates boron-<username>.slice at account-
creation time), restarted on crash (`Restart=on-failure`). Node itself is
one of several full runtime installs living side by side under
settings.node_base_dir/<version> (the same pattern lsphp already uses for
PHP versions, ARCHITECTURE.md SS6) -- selected per app, not server-wide.

Each app is bound 1:1 to one of the account's own domains: that domain's
entire vhost context `/` becomes a reverse proxy to the app's local port
(daemon/ols.py's `_app_proxy_for_domain`), via OLS's native "Web Server
(Proxy)" external app + Proxy Context (confirmed against this server's own
installed OLS docs, `/usr/local/lsws/docs/{External_WS,Proxy_Context}.html`)
-- not OLS's separate "App Server Context" feature (which can itself spawn
and manage a Node.js process), because the goal explicitly directs systemd
for supervision/crash-restart/cgroup assignment, with OLS's role limited to
reverse-proxying to whatever local port that systemd unit is listening on.
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
    validate_app_entry_point,
    validate_app_name,
    validate_domain,
    validate_env_vars,
    validate_username,
)

from daemon import appcrypto, appunits, ols, safeio
from daemon.portalloc import allocate_port
from daemon.procutil import run

KIND = "node"


def _node_bin(version: str) -> str:
    return f"{settings.node_base_dir}/{version}/bin/node"


def _npm_bin(version: str) -> str:
    return f"{settings.node_base_dir}/{version}/bin/npm"


def _app_dir(username: str, name: str) -> str:
    return f"{settings.home_base}/{username}/nodeapps/{name}"


def _log_path(username: str, name: str) -> str:
    return f"{settings.home_base}/{username}/logs/node/{name}.log"


def _validate_node_version(version: str) -> str:
    if version not in settings.node_versions:
        raise ValidationError(f"node_version must be one of {settings.node_versions}")
    return version


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
    """A domain's vhost context `/` can be owned by at most one app (Node
    or Python) at a time -- reusing it for a second app would silently
    make one of them unreachable with no indication why."""
    if session.scalar(select(NodeApp).where(NodeApp.domain == domain_name)) is not None:
        raise RuntimeError(f"domain '{domain_name}' already has a NodeJS app bound to it")
    if session.scalar(select(PythonApp).where(PythonApp.domain == domain_name)) is not None:
        raise RuntimeError(f"domain '{domain_name}' already has a Python app bound to it")


def _get_row(session, username: str, app_id: int) -> tuple[Account, NodeApp]:
    account = _get_account(session, username)
    row = session.scalar(select(NodeApp).where(NodeApp.id == app_id, NodeApp.account_id == account.id))
    if row is None:
        raise RuntimeError(f"NodeJS app {app_id} not found for account '{username}'")
    return account, row


def _row_to_dict(row: NodeApp, username: str) -> dict:
    unit = appunits.unit_name(KIND, username, row.id)
    st = appunits.status(unit)
    return {
        "id": row.id,
        "account_id": row.account_id,
        "domain": row.domain,
        "name": row.name,
        "entry_point": row.entry_point,
        "port": row.port,
        "node_version": row.node_version,
        "env_vars": appcrypto.decrypt_env(row.env_vars) if row.env_vars else {},
        "enabled": row.enabled,
        "app_dir": _app_dir(username, row.name),
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

    log_dir = safeio.secure_mkdirs(home, "logs/node", pw.pw_uid, pw.pw_gid, 0o750)
    safeio.secure_ensure_file(log_dir, os.path.basename(_log_path(username, name)), pw.pw_uid, pw.pw_gid, 0o640)


def _write_unit(username: str, app_id: int, name: str, entry_point: str, port: int, node_version: str, env_vars: dict) -> str:
    unit = appunits.unit_name(KIND, username, app_id)
    app_dir = _app_dir(username, name)
    log_path = _log_path(username, name)

    env = dict(env_vars)
    env["PORT"] = str(port)
    env.setdefault("NODE_ENV", "production")
    appunits.write_env_file(unit, env)

    content = (
        "[Unit]\n"
        f"Description=Boron NodeJS app '{name}' for account '{username}'\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={username}\n"
        f"Group={username}\n"
        f"WorkingDirectory={app_dir}\n"
        f"EnvironmentFile={appunits.env_file_path(unit)}\n"
        f"ExecStart={_node_bin(node_version)} {entry_point}\n"
        f"Slice=boron-{username}.slice\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        f"StandardOutput=append:{log_path}\n"
        f"StandardError=append:{log_path}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    appunits.write_unit_file(unit, content)
    appunits.daemon_reload()
    return unit


def create_app(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    name = validate_app_name(params["name"])
    entry_point = validate_app_entry_point(params["entry_point"])
    node_version = _validate_node_version(params.get("node_version", settings.default_node_version))
    env_vars = validate_env_vars(params.get("env_vars") or {})

    with write_session() as session:
        account = _get_account(session, username)
        _get_domain_for_account(session, account, domain_name)
        _assert_domain_free(session, domain_name)
        if session.scalar(select(NodeApp).where(NodeApp.account_id == account.id, NodeApp.name == name)) is not None:
            raise RuntimeError(f"app name '{name}' already exists for account '{username}'")

        port = allocate_port(session)
        row = NodeApp(
            account_id=account.id,
            domain=domain_name,
            name=name,
            entry_point=entry_point,
            port=port,
            node_version=node_version,
            env_vars=appcrypto.encrypt_env(env_vars),
            # False, not True: create() deliberately does not start the
            # systemd unit (the customer's code likely isn't deployed/
            # npm-installed yet) -- start_app() is the explicit action
            # that both flips this and actually enables+starts the unit,
            # so "enabled" always matches "was ever actually started."
            enabled=False,
        )
        session.add(row)
        session.flush()
        app_id = row.id
        account_snapshot = account

    try:
        _provision_filesystem(username, name)
        unit = _write_unit(username, app_id, name, entry_point, port, node_version, env_vars)
        # Wires the reverse-proxy context into this domain's vhost -- does
        # NOT start the systemd unit itself (the customer's code likely
        # isn't deployed/npm-installed yet); start() is a separate,
        # explicit action.
        ols.refresh_vhost(account_snapshot)
    except Exception:
        # Same compensation discipline as handlers_domain.add_domain: the
        # DB row was already committed (allocate_port/name-uniqueness need
        # it visible to a concurrent create() call), so a failed OLS apply
        # must delete it rather than leave an orphaned row with no working
        # vhost -- found live (Phase 7a's own first real end-to-end test:
        # an "Unknown external processor <type>: web" template bug failed
        # the OLS reload, and the DB row/unit file survived the failure
        # until this fix, blocking every subsequent create() for the same
        # domain/name with a stale "already has an app bound" error).
        with write_session() as session:
            orphan = session.get(NodeApp, app_id)
            if orphan is not None:
                session.delete(orphan)
        appunits.remove_unit(appunits.unit_name(KIND, username, app_id))
        raise

    with write_session() as session:
        row = session.get(NodeApp, app_id)
        return _row_to_dict(row, username)


def update_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])

    with write_session() as session:
        account, row = _get_row(session, username, app_id)
        if "entry_point" in params:
            row.entry_point = validate_app_entry_point(params["entry_point"])
        if "node_version" in params:
            row.node_version = _validate_node_version(params["node_version"])
        if "env_vars" in params:
            row.env_vars = appcrypto.encrypt_env(validate_env_vars(params["env_vars"] or {}))
        session.flush()
        name, entry_point, node_version, port, enabled = row.name, row.entry_point, row.node_version, row.port, row.enabled
        env_vars = appcrypto.decrypt_env(row.env_vars) if row.env_vars else {}

    unit = _write_unit(username, app_id, name, entry_point, port, node_version, env_vars)
    if enabled:
        # Only restarts a currently-enabled app -- a customer who
        # deliberately stopped their app shouldn't have an unrelated env
        # var edit silently start it back up.
        appunits.restart(unit)

    with write_session() as session:
        row = session.get(NodeApp, app_id)
        return _row_to_dict(row, username)


def delete_app(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])

    with write_session() as session:
        account, row = _get_row(session, username, app_id)
        domain_name, name = row.domain, row.name
        session.delete(row)
        account_snapshot = account

    unit = appunits.unit_name(KIND, username, app_id)
    appunits.remove_unit(unit)
    ols.refresh_vhost(account_snapshot)
    # App code under app_dir is deliberately left on disk -- same "delete
    # the routing, not the customer's content" convention
    # handlers_domain.remove_domain already established.
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


def npm_install(params: dict) -> dict:
    """Explicit action (not implicit in create()) -- installs
    dependencies from the app's own package.json as the account's own uid,
    inside its own app_dir. Matches the same explicit-lifecycle-action
    pattern as start/stop/restart rather than running automatically on
    every create/update, since a customer may still be uploading/editing
    code at create time."""
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, row = _get_row(session, username, app_id)
        name, node_version = row.name, row.node_version

    app_dir = _app_dir(username, name)
    node_bin_dir = str(Path(_node_bin(node_version)).parent)
    result = run(
        [
            "runuser", "-u", username, "--",
            "env", f"PATH={node_bin_dir}:/usr/bin:/bin", f"HOME={app_dir}",
            _npm_bin(node_version), "install", "--omit=dev",
        ],
        cwd=app_dir,
        timeout=300,
    )
    if not result.ok:
        raise RuntimeError(f"npm install failed: {result.stderr.strip()[:2000] or result.stdout.strip()[:2000]}")
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
        rows = session.scalars(select(NodeApp).where(NodeApp.account_id == account.id)).all()
        return {"apps": [_row_to_dict(r, username) for r in rows]}


def get_logs(params: dict) -> dict:
    username = validate_username(params["username"])
    app_id = int(params["id"])
    with write_session() as session:
        _account, row = _get_row(session, username, app_id)
        log_path = _log_path(username, row.name)
    return {"id": app_id, "log_lines": appunits.tail_log_file(log_path, lines=100)}


def terminate_account_node_apps(account: Account) -> None:
    """TERMINATE_HOOKS entry: idempotent -- safe even if the account never
    had a NodeJS app. Removes every systemd unit/env file (the vhost proxy
    contexts themselves are already torn down by ols.terminate_vhost,
    which runs before this in server.py's hook order); DB rows are deleted
    here directly rather than relying on a cascade, matching this
    project's established manual-cascade convention (Redirect/MailUser/
    etc.)."""
    with write_session() as session:
        rows = session.scalars(select(NodeApp).where(NodeApp.account_id == account.id)).all()
        ids = [r.id for r in rows]
        for row in rows:
            session.delete(row)

    for app_id in ids:
        appunits.remove_unit(appunits.unit_name(KIND, account.username, app_id))


def bootstrap_all_node_apps() -> None:
    """Run once at borond startup (server.py's amain(), same category
    as cgroups.bootstrap_all_slices) -- re-applies every enabled app's
    systemd unit so it survives a host reboot, not just a borond
    restart. A disabled (explicitly stopped) app is left stopped, matching
    stop_app's own "sticks across reboot" semantics."""
    with write_session() as session:
        rows = session.scalars(select(NodeApp)).all()
        snapshot = [
            (r.id, r.account_id, r.name, r.entry_point, r.port, r.node_version, r.env_vars, r.enabled)
            for r in rows
        ]
        usernames = {a.id: a.username for a in session.scalars(select(Account)).all()}

    for app_id, account_id, name, entry_point, port, node_version, env_vars_enc, enabled in snapshot:
        username = usernames.get(account_id)
        if username is None:
            continue
        try:
            env_vars = appcrypto.decrypt_env(env_vars_enc) if env_vars_enc else {}
            _provision_filesystem(username, name)
            unit = _write_unit(username, app_id, name, entry_point, port, node_version, env_vars)
            if enabled:
                appunits.enable_start(unit)
        except Exception:
            import logging

            logging.getLogger("borond.nodeapps").exception(
                "failed to bootstrap NodeJS app %d for account '%s'", app_id, username
            )
