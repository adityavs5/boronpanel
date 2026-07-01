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

from daemon.procutil import run

PURE_PW_BIN = "/usr/bin/pure-pw"


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


def delete_ftp_user(login: str) -> None:
    result = run([PURE_PW_BIN, "userdel", login, "-m"], timeout=15)
    if not result.ok and "unknown login" not in (result.stderr or "").lower():
        raise FtpError(f"pure-pw userdel failed: {result.stderr.strip()}")
