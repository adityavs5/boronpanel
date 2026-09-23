"""Run each file manager as its account UID, with a private home mount.

The former shared root backend followed customer-created symlinks outside
their scope. Backend application path checks are not a privilege boundary.
These instances have no root capabilities, cannot see other homes, and expose
only a Unix socket to the authenticated panel proxy.
"""
from __future__ import annotations

import grp
import os
from pathlib import Path
import pwd
import stat
import time
import shutil
from threading import RLock

import yaml

from daemon.procutil import run
from shared.config import settings
from shared.filebrowser_paths import account_socket, frontend_socket
from shared.validation import validate_username

TEMPLATE_PATH = "/etc/systemd/system/boron-filebrowser@.service"
FRONTEND_TEMPLATE_PATH = "/etc/systemd/system/boron-filebrowser-ui.service"
_lifecycle_lock = RLock()


def unit_content() -> str:
    home = str(Path(settings.home_base) / "%i")
    data = str(Path(settings.filebrowser_account_data_dir) / "%i")
    runtime = str(Path(settings.filebrowser_runtime_dir) / "%i")
    return f"""[Unit]
Description=Boron file manager for %i
After=network.target
[Service]
Type=simple
User=%i
Group=%i
UMask=0007
ExecStart={settings.filebrowser_bin} -c {data}/config.yaml
WorkingDirectory={data}/state
NoNewPrivileges=true
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=tmpfs
BindPaths={home}
ReadWritePaths={home} {data}/state {runtime}
PrivateTmp=true
PrivateDevices=true
ProtectProc=invisible
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
RestrictAddressFamilies=AF_UNIX
RestrictNamespaces=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
MemoryMax=256M
CPUQuota=100%
TasksMax=128
TimeoutStopSec=15
"""


def bootstrap() -> dict:
    # Stop the vulnerable backend before exposing any replacement. Do not
    # silently fall back to the shared root process if isolation cannot start.
    run(["systemctl", "disable", "--now", "boron-filebrowser.service"], timeout=30)
    Path(TEMPLATE_PATH).write_text(unit_content())
    _bootstrap_frontend()
    run(["systemctl", "daemon-reload"], timeout=20, check=True)
    run(["systemctl", "start", "boron-filebrowser-ui.service"], timeout=30, check=True)
    return {"status": "ok", "service": "boron-filebrowser@.service", "active": "on-demand"}


def _directory(path: Path, uid: int, gid: int, mode: int) -> None:
    path.mkdir(exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RuntimeError("file manager state path is not a directory")
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, mode, follow_symlinks=False)


def start(username: str, home: str) -> None:
    with _lifecycle_lock:
        _start(username, home)


def _start(username: str, home: str) -> None:
    validate_username(username)
    user = pwd.getpwnam(username)
    expected = Path(settings.home_base) / username
    if str(expected) != home or expected.is_symlink() or expected.stat().st_uid != user.pw_uid:
        raise RuntimeError("file manager home ownership is invalid")
    api_gid = grp.getgrnam("boron-api").gr_gid
    data_root = Path(settings.filebrowser_account_data_dir)
    runtime_root = Path(settings.filebrowser_runtime_dir)
    for root in (data_root, runtime_root):
        root.parent.mkdir(parents=True, exist_ok=True)
        _directory(root, 0, 0, 0o755)
    data = data_root / username
    _directory(data, 0, 0, 0o755)
    _directory(data / "state", user.pw_uid, user.pw_gid, 0o700)
    _directory(runtime_root / username, user.pw_uid, api_gid, 0o750)
    # The provisioning daemon deliberately cannot set SUID/SGID bits. A
    # default ACL gives only the API UID access to newly bound sockets,
    # without adding the customer process to the privileged API group.
    run(["setfacl", "-m", "d:u:boron-api:rwx", str(runtime_root / username)], timeout=10, check=True)

    from daemon.filebrowser import build_config
    config = build_config()
    config["server"].update(
        socket=account_socket(username), database=str(data / "state" / "database.db"),
        cacheDir=str(data / "state" / "cache"), disableUpdateCheck=True,
    )
    # The source still uses /home/<proxy-user>, but this process's private
    # mount namespace contains only its own bind-mounted account home.
    config_path = data / "config.yaml"
    with open(config_path, "w", opener=lambda path, flags: os.open(path, flags | os.O_NOFOLLOW, 0o644)) as stream:
        yaml.safe_dump(config, stream, sort_keys=False)
    os.chmod(config_path, 0o644)
    run(["systemctl", "start", f"boron-filebrowser@{username}.service"], timeout=30, check=True)
    for _ in range(100):
        try:
            entry = Path(account_socket(username)).lstat()
            if stat.S_ISSOCK(entry.st_mode) and entry.st_uid == user.pw_uid:
                return
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    raise RuntimeError("account file manager did not become ready")


def stop(username: str) -> None:
    with _lifecycle_lock:
        _stop(username)


def _stop(username: str) -> None:
    validate_username(username)
    run(["systemctl", "stop", f"boron-filebrowser@{username}.service"], timeout=30, check=True)
    for root in (settings.filebrowser_account_data_dir, settings.filebrowser_runtime_dir):
        path = Path(root) / username
        if path.is_symlink():
            raise RuntimeError("file manager state path was substituted")
        if path.exists():
            shutil.rmtree(path)


def _bootstrap_frontend() -> None:
    """Tenant backends must never supply trusted executable panel assets."""
    name = 'boron-files-ui'
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        run(['useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent',
             '--shell', '/usr/sbin/nologin', name], timeout=30, check=True)
        user = pwd.getpwnam(name)
    api_gid = grp.getgrnam('boron-api').gr_gid
    data_root = Path(settings.filebrowser_account_data_dir)
    runtime_root = Path(settings.filebrowser_runtime_dir)
    for root in (data_root, runtime_root):
        root.parent.mkdir(parents=True, exist_ok=True)
        _directory(root, 0, 0, 0o755)
    data = data_root / '_frontend'
    _directory(data, 0, 0, 0o755)
    _directory(data / 'state', user.pw_uid, user.pw_gid, 0o700)
    _directory(data / 'empty', 0, 0, 0o755)
    runtime = runtime_root / '_frontend'
    _directory(runtime, user.pw_uid, api_gid, 0o750)
    run(['setfacl', '-m', 'd:u:boron-api:rwx', str(runtime)], timeout=10, check=True)
    from daemon.filebrowser import build_config
    config = build_config()
    config['server'].update(socket=frontend_socket(), database=str(data / 'state/database.db'),
                            cacheDir=str(data / 'state/cache'), disableUpdateCheck=True,
                            sources=[{'path': str(data / 'empty'), 'name': 'home',
                                      'config': {'defaultEnabled': True}}])
    (data / 'config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    (data / 'config.yaml').chmod(0o644)
    home = str(Path(settings.home_base) / '_frontend')
    unit = unit_content().replace('%i', '_frontend')
    unit = unit.replace('User=_frontend', 'User='+name).replace('Group=_frontend', 'Group='+name)
    unit = unit.replace('BindPaths='+home+'\n', '').replace('ReadWritePaths='+home+' ', 'ReadWritePaths=')
    Path(FRONTEND_TEMPLATE_PATH).write_text(unit)
