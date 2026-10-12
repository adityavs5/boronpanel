"""Pure-FTPd virtual (PureDB) sub-account provisioning (Phase 3 feature 5).

ARCHITECTURE.md/RESEARCH.md SS4 locked Pure-FTPd with `-l unix` (system-
account auth) for a hosting account's own FTP login -- that's still true
and unchanged. Sub-accounts scoped to a *path within* the account's home
dir need their own, narrower chroot than "the whole home dir," which a
second real Linux account can't give us without a separate per-user
chroot mechanism. Pure-FTPd's PureDB *virtual* user backend does exactly
this natively (confirmed live: a PureDB user's configured home directory
IS its chroot root, unlike unix/PAM users which need `ChrootEveryone`) --
enabled here as an *additional* auth source (`/etc/pure-ftpd/auth/30pdb`,
tried before the existing 65unix/70pam), so a hosting account's own
system-user FTP login keeps working exactly as before, layered with
virtual sub-accounts for anything scoped to a subdirectory.

All sub-account files are still owned by the hosting account's own
uid/gid (passed to `pure-pw useradd -u/-g`) -- disk usage/quota
accounting for a sub-account's uploads is identical to the account's own
uid, not a separate identity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import stat

from daemon.procutil import run
from daemon.safeio import secure_replace_file

PURE_PW_BIN = "/usr/bin/pure-pw"
PURE_FTPWHO_BIN = "/usr/bin/pure-ftpwho"
SUSPENDED_IP = "255.255.255.255/32"
SUSPENSION_STATE_DIR = Path("/var/lib/boron/ftp-suspension")


class FtpError(Exception):
    pass


def _run_pure_pw(args: list[str], password: str | None = None) -> None:
    input_text = f"{password}\n{password}\n" if password is not None else None
    result = run([PURE_PW_BIN, *args, "-m"], input_text=input_text, timeout=15)
    if not result.ok:
        raise FtpError(f"pure-pw {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")


def create_ftp_user(login: str, uid: int, gid: int, path: str, password: str) -> None:
    _run_pure_pw(["useradd", login, "-u", str(uid), "-g", str(gid), "-d", path], password=password)


def set_path(login: str, uid: int, gid: int, path: str) -> None:
    _run_pure_pw(["usermod", login, "-u", str(uid), "-g", str(gid), "-d", path])


def set_password(login: str, password: str) -> None:
    _run_pure_pw(["passwd", login], password=password)


def _state_name(login: str) -> str:
    return hashlib.sha256(login.encode("utf-8")).hexdigest() + ".json"


def _show_ip_constraints(login: str) -> dict[str, str]:
    result = run([PURE_PW_BIN, "show", login], timeout=15, output_limit=64 * 1024)
    if not result.ok:
        raise FtpError(f"pure-pw show failed: {result.stderr.strip() or result.stdout.strip()}")
    values = {"local": "", "client": ""}
    labels = {
        "allowed local ip": "local", "allowed local ips": "local",
        "allowed client ip": "client", "allowed client ips": "client",
    }
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        key = labels.get(label.strip().lower())
        if key:
            value = value.strip()
            if len(value) > 1024 or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise FtpError("pure-pw returned an invalid IP restriction")
            values[key] = value
    return values


def _saved_constraints(login: str) -> dict[str, str] | None:
    path = SUSPENSION_STATE_DIR / _state_name(login)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 4096:
            raise FtpError("invalid FTP suspension recovery state")
        payload = json.loads(os.read(fd, 4097))
        if payload.get("login") != login or set(payload) != {"login", "local", "client"}:
            raise ValueError()
        for key in ("local", "client"):
            if not isinstance(payload[key], str) or len(payload[key]) > 1024:
                raise ValueError()
        return payload
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise FtpError("invalid FTP suspension recovery state") from exc
    finally:
        os.close(fd)


def suspend_ftp_user(login: str) -> None:
    """Deny every client while retaining prior address restrictions."""
    saved = _saved_constraints(login)
    if saved is None:
        SUSPENSION_STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(SUSPENSION_STATE_DIR, 0, 0)
        os.chmod(SUSPENSION_STATE_DIR, 0o700)
        saved = {"login": login, **_show_ip_constraints(login)}
        secure_replace_file(
            str(SUSPENSION_STATE_DIR), _state_name(login),
            json.dumps(saved, sort_keys=True).encode("utf-8"), 0, 0, 0o600,
        )
    _run_pure_pw(["usermod", login, "-r", SUSPENDED_IP, "-i", SUSPENDED_IP])


def unsuspend_ftp_user(login: str, uid: int, gid: int, path: str) -> None:
    """Restore the mapping and the exact restrictions saved at suspension."""
    saved = _saved_constraints(login)
    args = [
        "usermod", login,
        "-u", str(uid), "-g", str(gid), "-d", path,
    ]
    if saved is not None:
        args.extend(["-r", saved["client"], "-i", saved["local"]])
    _run_pure_pw(args)
    if saved is not None:
        try:
            os.unlink(SUSPENSION_STATE_DIR / _state_name(login))
        except FileNotFoundError:
            pass


def _is_pure_ftp_process(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as handle:
            return handle.read(64).strip() in {"pure-ftpd", "pure-ftpd-wrap"}
    except FileNotFoundError:
        return False


def disconnect_ftp_sessions(logins: set[str]) -> int:
    """Terminate existing Pure-FTPd sessions for newly suspended logins."""
    if not logins:
        return 0
    result = run([PURE_FTPWHO_BIN, "-s", "-H"], timeout=10, output_limit=256 * 1024)
    if not result.ok:
        raise FtpError(f"pure-ftpwho failed: {result.stderr.strip() or result.stdout.strip()}")
    killed = 0
    for line in result.stdout.splitlines()[:4096]:
        fields = line.split("|", 3)
        if len(fields) < 2 or fields[1] not in logins or not fields[0].isdigit():
            continue
        pid = int(fields[0])
        try:
            if not _is_pure_ftp_process(pid):
                continue
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except (FileNotFoundError, ProcessLookupError):
            continue
    return killed


def delete_ftp_user(login: str) -> None:
    result = run([PURE_PW_BIN, "userdel", login, "-m"], timeout=15)
    if not result.ok and "unknown login" not in (result.stderr or "").lower():
        raise FtpError(f"pure-pw userdel failed: {result.stderr.strip()}")
