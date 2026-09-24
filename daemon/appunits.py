"""Shared systemd-unit plumbing for NodeApp/PythonApp (Phase 7a features
1/2) and RedisInstance (feature 3) -- the parts that are identical
regardless of what's actually being supervised: unit file naming
(`boron-{kind}-{username}-{id}.service`, the goal's own explicit
convention), writing/starting/stopping/removing a unit, and tailing an
app's own log file instead of the system journal. A trusted launcher opens
the log after systemd drops to the hosting user; systemd must not open a
tenant-controlled log path with root privileges.

Each app is assigned directly to its account's existing cgroup slice via
`Slice=boron-<username>.slice` in the unit itself -- daemon/cgroups.py
already creates that slice at account-creation time (CREATE_HOOKS), so it
always exists before any app unit references it. This is simpler than
cgroups.py's own LSAPI-worker reconciler: a systemd-spawned unit can be
told its target slice directly at spawn time (root, via systemd, always
permitted), unlike a PHP worker OLS itself forks into its own service's
cgroup first.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from shared.config import settings
from shared.validation import ENV_VAR_KEY_RE

from daemon.procutil import run
from daemon import safeio

UNITS_DIR = Path("/etc/systemd/system")


def logged_exec(command: str, log_path: str) -> str:
    # Paths and commands here are generated from validated app fields. The
    # helper is root-owned but executes under the unit's User=, before opening
    # any tenant-controlled path. systemd must never open these logs itself.
    return f"/usr/bin/python3 -I /opt/boron/scripts/app_exec.py --log {log_path} -- {command}"


def unit_name(kind: str, username: str, app_id: int) -> str:
    return f"boron-{kind}-{username}-{app_id}.service"


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
    run(["systemctl", "stop", name], timeout=30)
    run(["systemctl", "disable", name], timeout=30)
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
