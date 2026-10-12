"""Shared systemd-unit plumbing for NodeApp/PythonApp (Phase 7a features
1/2) and RedisInstance (feature 3) -- the parts that are identical
regardless of what's actually being supervised: unit file naming
(`boron-{kind}-{username}-{id}.service`, the goal's own explicit
convention), writing/starting/stopping/removing a unit, and tailing an
app's own log file instead of the system journal. A trusted launcher opens
the log after systemd drops to the hosting user; systemd must not open a
tenant-controlled log path with root privileges.

Each app is assigned directly to its account's canonical user-UID slice via
`Slice=user-<uid>.slice` in the unit itself -- daemon/cgroups.py
already configures that slice at account-creation time (CREATE_HOOKS), so it
always exists before any app unit references it. This is simpler than
cgroups.py's own LSAPI-worker reconciler: a systemd-spawned unit can be
told its target slice directly at spawn time (root, via systemd, always
permitted), unlike a PHP worker OLS itself forks into its own service's
cgroup first.
"""
from __future__ import annotations

import os
import pwd
import socket
import stat
from pathlib import Path

from shared.config import settings
from shared.validation import ENV_VAR_KEY_RE

from daemon.procutil import run
from daemon import safeio

UNITS_DIR = Path("/etc/systemd/system")
# Keep public proxy endpoints outside /run/boron: that directory is 0750 and
# deliberately reserved for the boron-api RPC boundary, so OLS's `nobody`
# worker cannot traverse it. Socket inodes here remain 0600 and owned by OLS.
APP_SOCKET_ROOT = Path("/run/boron-apps")


class AppSocketMigrationError(RuntimeError):
    """A legacy shared-port application could not be moved safely."""


def logged_exec(command: str, log_path: str) -> str:
    # Paths and commands here are generated from validated app fields. The
    # helper is root-owned but executes under the unit's User=, before opening
    # any tenant-controlled path. systemd must never open these logs itself.
    return f"/usr/bin/python3 -I /opt/boron/scripts/app_exec.py --log {log_path} -- {command}"


def unit_name(kind: str, username: str, app_id: int) -> str:
    return f"boron-{kind}-{username}-{app_id}.service"


def socket_unit_name(service_name: str) -> str:
    if not service_name.endswith(".service"):
        raise ValueError("application service name required")
    return service_name[:-8] + ".socket"


def socket_path(kind: str, app_id: int) -> Path:
    if kind not in {"node", "python"} or isinstance(app_id, bool) or int(app_id) <= 0:
        raise ValueError("valid application kind and id required")
    return APP_SOCKET_ROOT / f"{kind}-{int(app_id)}.sock"


def write_socket_unit(service_name: str, kind: str, app_id: int) -> str:
    """Create the root-owned OLS endpoint for one application.

    The socket inode is owned by OLS's unprivileged worker and lives in a
    root-owned directory. Tenant processes receive only the already-open fd;
    they can neither bind nor replace another application's endpoint.
    """
    socket_name = socket_unit_name(service_name)
    content = (
        "[Unit]\n"
        f"Description=Boron private proxy socket for {service_name}\n"
        "\n"
        "[Socket]\n"
        f"ListenStream={socket_path(kind, app_id)}\n"
        "SocketUser=nobody\n"
        "SocketGroup=nogroup\n"
        "SocketMode=0600\n"
        "DirectoryMode=0755\n"
        "RemoveOnStop=yes\n"
        "Accept=no\n"
        f"Service={service_name}\n"
        "\n"
        "[Install]\n"
        "WantedBy=sockets.target\n"
    )
    write_unit_file(socket_name, content)
    return socket_name


def enable_start_app(service_name: str) -> None:
    socket_name = socket_unit_name(service_name)
    enable_start(socket_name)
    # A legacy TCP process may already be active while its unit file has just
    # been replaced. Merely enabling it would leave that old process running
    # without fd 3 while OLS is switched to the new socket. Restart active
    # services so the private descriptor is actually inherited; start cleanly
    # when the service was disabled.
    run(["systemctl", "enable", service_name], timeout=30, check=True)
    active = run(["systemctl", "is-active", service_name], timeout=10)
    (restart if active.stdout.strip() == "active" else start)(service_name)


def stop_disable_app(service_name: str) -> None:
    stop_disable(service_name)
    stop_disable(socket_unit_name(service_name))


def restart_app(service_name: str) -> None:
    start(socket_unit_name(service_name))
    restart(service_name)


def private_socket_ready(service_name: str, kind: str, app_id: int) -> bool:
    """Verify the protected endpoint and its consumer both reached ready."""
    for name in (socket_unit_name(service_name), service_name):
        state = run(["systemctl", "is-active", name], timeout=10)
        if state.stdout.strip() != "active":
            return False
    try:
        info = socket_path(kind, app_id).lstat()
        nobody = pwd.getpwnam("nobody")
        return (stat.S_ISSOCK(info.st_mode) and info.st_uid == nobody.pw_uid
                and stat.S_IMODE(info.st_mode) == 0o600)
    except (FileNotFoundError, KeyError, OSError):
        return False


def remove_socket_unit(service_name: str) -> None:
    name = socket_unit_name(service_name)
    stop_disable(name)
    remove_unit_file(name)
    daemon_reload()


def remove_app_unit(service_name: str) -> None:
    """Idempotently remove a service and its private activation socket."""
    socket_name = socket_unit_name(service_name)
    for name in (service_name, socket_name):
        stopped = run(["systemctl", "stop", name], timeout=30)
        if not stopped.ok and unit_path(name).exists():
            state = run(["systemctl", "is-active", name], timeout=10)
            if state.stdout.strip() not in ("inactive", "failed", "unknown"):
                raise RuntimeError("Application service could not be stopped safely")
        run(["systemctl", "disable", name], timeout=30)
        remove_unit_file(name)
    remove_env_file(service_name)
    daemon_reload()


def unit_path(name: str) -> Path:
    return UNITS_DIR / name


def env_file_path(name: str) -> Path:
    return Path(settings.app_env_dir) / f"{name}.env"


def write_env_file(name: str, env: dict[str, str]) -> Path:
    """Root-only (0600) -- systemd reads this itself, as root, before
    dropping to the unit's own User=, so a decrypted secret is never
    written anywhere the hosting account's own uid can read it."""
    path = env_file_path(name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = []
    for key, value in env.items():
        # systemd EnvironmentFile syntax: KEY=VALUE, one per line, no shell
        # expansion -- but embedded newlines would still break the format,
        # so they're rejected rather than silently truncated/corrupted.
        if (not isinstance(key, str) or not ENV_VAR_KEY_RE.fullmatch(key)
                or not isinstance(value, str) or any(c in value for c in ('\0', '\r', '\n'))):
            raise ValueError("invalid environment assignment")
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    content = "\n".join(lines) + ("\n" if lines else "")
    safeio.secure_replace_file(str(path.parent), path.name, content.encode(), os.geteuid(), os.getegid(), 0o600)
    return path


def remove_env_file(name: str) -> None:
    env_file_path(name).unlink(missing_ok=True)


def write_unit_file(name: str, content: str) -> None:
    path = unit_path(name)
    if path.exists() and path.read_text() == content:
        return
    path.write_text(content)


def remove_unit_file(name: str) -> None:
    path = unit_path(name)
    path.unlink(missing_ok=True)


def daemon_reload() -> None:
    run(["systemctl", "daemon-reload"], timeout=20, check=True)


def enable_start(name: str) -> None:
    run(["systemctl", "enable", "--now", name], timeout=30, check=True)


def enable_start_in_slice(name: str, slice_unit: str) -> None:
    """Start a unit or restart an old live process after a Slice= migration."""
    active = run(["systemctl", "is-active", name], timeout=10)
    if active.stdout.strip() != "active":
        enable_start(name)
        return
    shown = run(["systemctl", "show", name, "--property=ControlGroup", "--value"], timeout=10)
    control_group = shown.stdout.strip() if shown.ok else ""
    expected = f"/user.slice/{slice_unit}/"
    if not control_group.startswith(expected):
        restart(name)


def stop_disable(name: str) -> None:
    run(["systemctl", "disable", "--now", name], timeout=30)


def start(name: str) -> None:
    run(["systemctl", "start", name], timeout=30, check=True)


def stop(name: str) -> None:
    run(["systemctl", "stop", name], timeout=30, check=True)


def restart(name: str) -> None:
    run(["systemctl", "restart", name], timeout=30, check=True)


def status(name: str) -> dict:
    active = run(["systemctl", "is-active", name], timeout=10)
    enabled = run(["systemctl", "is-enabled", name], timeout=10)
    return {
        "active": active.stdout.strip() or active.stderr.strip(),
        "enabled": enabled.stdout.strip() or enabled.stderr.strip(),
    }


def remove_unit(name: str) -> None:
    """Idempotent full teardown -- safe even if the unit was never
    successfully started (e.g. a create() that failed partway)."""
    stopped = run(["systemctl", "stop", name], timeout=30)
    if not stopped.ok:
        state = run(["systemctl", "is-active", name], timeout=10)
        if state.stdout.strip() not in ('inactive', 'failed', 'unknown'):
            raise RuntimeError('Application service could not be stopped safely')
    disabled = run(["systemctl", "disable", name], timeout=30)
    if not disabled.ok and unit_path(name).exists():
        raise RuntimeError('Application service could not be disabled safely')
    remove_unit_file(name)
    remove_env_file(name)
    daemon_reload()


def tail_log_file(path: str, lines: int = 100) -> list[str]:
    """Reads the app's own log file directly (goal: "not system journal") --
    a plain tail, not a streaming/follow read. Missing file (app never
    logged anything yet, or was just created) returns an empty list rather
    than raising, since "no output yet" is the expected common case right
    after create()."""
    relative = Path(path).relative_to(settings.home_base)
    try:
        directory = safeio.open_dir_beneath(settings.home_base, str(relative.parent))
    except FileNotFoundError:
        return []
    try:
        try:
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError:
            return []
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise safeio.UnsafePathError('app log must be a regular file')
            start = max(0, info.st_size - 256 * 1024)
            os.lseek(fd, start, os.SEEK_SET)
            content = os.read(fd, 256 * 1024).decode('utf-8', errors='replace').splitlines()
            if start and content:
                content.pop(0)
            return content[-max(1, min(int(lines), 1000)):]
        finally:
            os.close(fd)
    finally:
        os.close(directory)
