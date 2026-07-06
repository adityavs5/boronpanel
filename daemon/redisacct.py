"""Phase 7a feature 3: per-account Redis.

Reachable only via a private Unix socket at /run/redis/<username>.sock --
no TCP listener at all (`port 0` in the rendered redis.conf), so there is
no port to firewall or misconfigure. `/run/redis` itself is mode 1777
(world-writable, sticky bit) -- the same pattern /tmp itself uses -- since
each account's own redis-server process (spawned by systemd running as
that account's own uid via `User=`, never root) has to be able to create
its own socket file there; the real isolation is each individual socket
file's own `unixsocketperm 700` plus being owned by that uid the moment
redis-server creates it, not the shared directory's permissions (a
directory permissive enough to let every account create a file in it,
narrowed back down at the file level -- confirmed empirically while
building this feature: a root:root 0755 /run/redis would have blocked
every non-root account's redis-server from ever binding its own socket at
all, since 0755 grants no "other" write bit).

One systemd unit per account (forgehost-redis-{username}-{id}.service,
daemon/appunits.py's shared naming convention, keyed by the RedisInstance
row's own id even though there's only ever one per account -- consistent
with NodeApp/PythonApp rather than a special case), Slice=-assigned to
that account's own cgroup. No persistence by default (`save ""`,
goal's explicit v1 default) and no authentication (`requirepass` is not
set) -- the goal's own stated isolation model for this feature is the Unix
socket's filesystem permission, not an application-level password.
"""
from __future__ import annotations

import os
import pwd
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, RedisInstance
from shared.validation import ValidationError, validate_username

from daemon import appunits
from daemon.procutil import run

KIND = "redis"
MIN_MEM_MB = 16
MAX_MEM_MB = 4096


def _validate_mem_mb(value) -> int:
    try:
        mem_mb = int(value)
    except (TypeError, ValueError):
        raise ValidationError("mem_mb must be an integer") from None
    if not (MIN_MEM_MB <= mem_mb <= MAX_MEM_MB):
        raise ValidationError(f"mem_mb must be between {MIN_MEM_MB} and {MAX_MEM_MB}")
    return mem_mb


def socket_path(username: str) -> str:
    return f"{settings.redis_run_dir}/{username}.sock"


def _data_dir(username: str) -> str:
    return f"{settings.home_base}/{username}/.redis"


def _conf_path(unit: str) -> Path:
    return Path("/etc/forgehost/redis") / f"{unit}.conf"


def _pid_path(username: str) -> str:
    return f"{settings.redis_run_dir}/{username}.pid"


def _get_account(session, username: str) -> Account:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    return account


def _row_to_dict(row: RedisInstance, username: str) -> dict:
    unit = appunits.unit_name(KIND, username, row.id)
    st = appunits.status(unit)
    return {
        "id": row.id,
        "account_id": row.account_id,
        "mem_mb": row.mem_mb,
        "enabled": row.enabled,
        "socket_path": socket_path(username),
        "data_dir": _data_dir(username),
        "unit": unit,
        "active": st["active"],
        "unit_enabled": st["enabled"],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _provision_filesystem(username: str) -> None:
    pw = pwd.getpwnam(username)
    data_dir = Path(_data_dir(username))
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chown(data_dir, pw.pw_uid, pw.pw_gid)
    os.chmod(data_dir, 0o700)


def _render_conf(username: str, mem_mb: int) -> str:
    return (
        f"port 0\n"
        f"unixsocket {socket_path(username)}\n"
        f"unixsocketperm 700\n"
        f"dir {_data_dir(username)}\n"
        f"pidfile {_pid_path(username)}\n"
        f"maxmemory {mem_mb}mb\n"
        f"maxmemory-policy allkeys-lru\n"
        f"save \"\"\n"
        f"appendonly no\n"
        f"daemonize no\n"
        f"logfile \"\"\n"
        f"loglevel notice\n"
    )


def _write_unit(username: str, instance_id: int, mem_mb: int) -> str:
    unit = appunits.unit_name(KIND, username, instance_id)
    conf_path = _conf_path(unit)
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(_render_conf(username, mem_mb))
    os.chmod(conf_path, 0o644)

    log_dir = Path(f"{settings.home_base}/{username}/logs/redis")
    log_dir.mkdir(parents=True, exist_ok=True)
    pw = pwd.getpwnam(username)
    os.chown(log_dir, pw.pw_uid, pw.pw_gid)
    os.chmod(log_dir, 0o750)
    log_path = log_dir / f"{username}.log"
    if not log_path.exists():
        log_path.touch()
    os.chown(log_path, pw.pw_uid, pw.pw_gid)
    os.chmod(log_path, 0o640)

    content = (
        "[Unit]\n"
        f"Description=Forgehost Redis for account '{username}'\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={username}\n"
        f"Group={username}\n"
        f"WorkingDirectory={_data_dir(username)}\n"
        f"ExecStart={settings.redis_bin} {conf_path}\n"
        f"Slice=forgehost-{username}.slice\n"
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


def enable_redis(params: dict) -> dict:
    """Create-or-update-and-start -- idempotent, matching this project's
    established "enable" verb shape (namespace.enable, waf.set_enabled):
    calling it again on an already-enabled account just re-applies the
    current mem_mb and restarts, rather than erroring."""
    username = validate_username(params["username"])
    mem_mb = _validate_mem_mb(params.get("mem_mb", settings.redis_default_mem_mb))

    with write_session() as session:
        account = _get_account(session, username)
        row = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id))
        if row is None:
            row = RedisInstance(account_id=account.id, mem_mb=mem_mb, enabled=True)
            session.add(row)
        else:
            row.mem_mb = mem_mb
            row.enabled = True
        session.flush()
        instance_id = row.id

    _provision_filesystem(username)
    unit = _write_unit(username, instance_id, mem_mb)
    appunits.enable_start(unit)

    with write_session() as session:
        row = session.get(RedisInstance, instance_id)
        return _row_to_dict(row, username)


def set_mem_limit(params: dict) -> dict:
    username = validate_username(params["username"])
    mem_mb = _validate_mem_mb(params["mem_mb"])

    with write_session() as session:
        account = _get_account(session, username)
        row = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id))
        if row is None:
            raise RuntimeError(f"Redis is not enabled for account '{username}'")
        row.mem_mb = mem_mb
        session.flush()
        instance_id, enabled = row.id, row.enabled

    unit = _write_unit(username, instance_id, mem_mb)
    if enabled:
        appunits.restart(unit)

    with write_session() as session:
        row = session.get(RedisInstance, instance_id)
        return _row_to_dict(row, username)


def disable_redis(params: dict) -> dict:
    """Stops the service and marks it disabled (persists across reboot,
    same semantics as NodeApp/PythonApp's stop_app) -- does NOT delete the
    DB row, the rendered conf, or the account's own ~/.redis data dir, so
    re-enabling resumes with the same memory limit and (if persistence
    were ever turned on by an operator override) the same data."""
    username = validate_username(params["username"])
    with write_session() as session:
        account = _get_account(session, username)
        row = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id))
        if row is None:
            raise RuntimeError(f"Redis is not enabled for account '{username}'")
        row.enabled = False
        instance_id = row.id
    appunits.stop_disable(appunits.unit_name(KIND, username, instance_id))
    return get_status({"username": username})


def flush(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = _get_account(session, username)
        row = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id))
        if row is None:
            raise RuntimeError(f"Redis is not enabled for account '{username}'")

    result = run([settings.redis_cli_bin, "-s", socket_path(username), "FLUSHALL"], timeout=30)
    if not result.ok:
        raise RuntimeError(f"FLUSHALL failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"username": username, "status": "flushed"}


def _parse_info_field(info_text: str, field: str) -> str | None:
    for line in info_text.splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    return None


def get_status(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = _get_account(session, username)
        row = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id))
        if row is None:
            return {"username": username, "enabled": False, "provisioned": False}
        result = _row_to_dict(row, username)

    info = run([settings.redis_cli_bin, "-s", socket_path(username), "INFO", "memory"], timeout=10)
    if info.ok:
        result["used_memory_human"] = _parse_info_field(info.stdout, "used_memory_human")
        result["used_memory_bytes"] = _parse_info_field(info.stdout, "used_memory")
    else:
        result["used_memory_human"] = None
        result["used_memory_bytes"] = None
    result["provisioned"] = True
    return result


def get_connection_info(params: dict) -> dict:
    username = validate_username(params["username"])
    path = socket_path(username)
    return {
        "username": username,
        "socket_path": path,
        # predis DSN form -- predis's own documented "unix" scheme
        # (https://github.com/predis/predis): new Client(['scheme' =>
        # 'unix', 'path' => $path]). phpredis's native `Redis` extension
        # (already installed for every lsphp version on this box,
        # `lsphp8x-redis`) takes the same bare path via
        # `$redis->connect($path)`.
        "predis_parameters": {"scheme": "unix", "path": path},
        "phpredis_connect_path": path,
    }


def terminate_account_redis(account: Account) -> None:
    """TERMINATE_HOOKS entry: idempotent -- safe even if the account never
    enabled Redis."""
    with write_session() as session:
        row = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id))
        if row is None:
            return
        instance_id = row.id
        session.delete(row)

    appunits.remove_unit(appunits.unit_name(KIND, account.username, instance_id))
    _conf_path(appunits.unit_name(KIND, account.username, instance_id)).unlink(missing_ok=True)


def bootstrap_all_redis() -> None:
    """Run once at forgehostd startup, same category as
    nodeapps.bootstrap_all_node_apps -- re-applies every enabled account's
    Redis unit so it survives a host reboot."""
    with write_session() as session:
        rows = session.scalars(select(RedisInstance)).all()
        snapshot = [(r.id, r.account_id, r.mem_mb, r.enabled) for r in rows]
        usernames = {a.id: a.username for a in session.scalars(select(Account)).all()}

    for instance_id, account_id, mem_mb, enabled in snapshot:
        username = usernames.get(account_id)
        if username is None:
            continue
        try:
            _provision_filesystem(username)
            unit = _write_unit(username, instance_id, mem_mb)
            if enabled:
                appunits.enable_start(unit)
        except Exception:
            import logging

            logging.getLogger("forgehostd.redisacct").exception(
                "failed to bootstrap Redis for account '%s'", username
            )
