"""Linux system-account operations for hosting accounts.

ARCHITECTURE.md SS5: one Linux user per account, no interactive shell (FTP/
web/mail only in v1), suspend = password lock, terminate = full userdel.
All inputs here are expected to have already passed shared.validation -- this
module re-checks the username shape defensively (cheap, and this is exactly
the kind of boundary where defense in depth matters) but does not re-derive
authorization.
"""
from __future__ import annotations

import grp
import pwd
import secrets
import string

from shared.config import settings
from shared.validation import ValidationError, validate_username

from daemon.procutil import run

NOLOGIN_SHELL = "/usr/sbin/nologin"


def _assert_safe_username(username: str) -> str:
    return validate_username(username)


def user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_linux_user(username: str) -> tuple[int, int]:
    """Create the account's Linux user + home dir. Returns (uid, gid)."""
    _assert_safe_username(username)
    if user_exists(username):
        raise RuntimeError(f"linux user '{username}' already exists")

    home_dir = f"{settings.home_base}/{username}"
    run(
        [
            "useradd",
            "--create-home",
            "--home-dir", home_dir,
            "--shell", NOLOGIN_SHELL,
            "--comment", "Forgehost hosting account",
            "--user-group",
            username,
        ],
        check=True,
    )
    # 711: owner rwx, group/other execute-only (traverse but not list/read).
    # OLS's worker process resolves the path to the vhost docroot while still
    # running as the shared "nobody" user *before* handing off to the
    # account's own uid (suEXEC-equivalent only kicks in for the LSAPI PHP
    # child and DocRoot-UID file serving, not the initial path lookup) --
    # confirmed empirically in Phase b testing: mode 750 produced a 403 on
    # every request because "nobody" couldn't even traverse into the home
    # dir. 711 is the same pattern cPanel/DirectAdmin use for this reason:
    # traversal without listing or reading still protects file contents,
    # since opening a file or listing a directory both require the read bit
    # this intentionally omits.
    run(["chmod", "711", home_dir], check=True)

    pw = pwd.getpwnam(username)
    return pw.pw_uid, pw.pw_gid


def set_initial_password(username: str, password: str) -> None:
    _assert_safe_username(username)
    run(["chpasswd"], input_text=f"{username}:{password}\n", check=True)


def lock_user(username: str) -> None:
    _assert_safe_username(username)
    run(["usermod", "-L", username], check=True)


def unlock_user(username: str) -> None:
    _assert_safe_username(username)
    run(["usermod", "-U", username], check=True)


def delete_linux_user(username: str) -> None:
    _assert_safe_username(username)
    if not user_exists(username):
        return
    # `userdel` never kills running processes owned by the user on its
    # own -- a real, previously-latent gap this project's own Phase 1
    # accounts rarely surfaced (short-lived processes), but Phase 2
    # feature 6's long-lived, pooled LSAPI workers (persistConn/autoStart)
    # made it a real, observed leak: worker processes kept running under
    # a now-unassigned uid indefinitely after `account.terminate`,
    # confirmed live. `pkill -9 -u` first, so `userdel` always removes a
    # genuinely process-free account.
    run(["pkill", "-9", "-u", username], timeout=15)
    run(["userdel", "--remove", "--force", username], check=True)


def set_quota(username: str, soft_mb: int, hard_mb: int, mount: str = "/") -> None:
    _assert_safe_username(username)
    soft_blocks = soft_mb * 1024
    hard_blocks = hard_mb * 1024
    run(
        [
            "setquota",
            "-u", username,
            str(soft_blocks), str(hard_blocks),
            "0", "0",
            mount,
        ],
        check=True,
    )


def remove_quota(username: str, mount: str = "/") -> None:
    _assert_safe_username(username)
    run(["setquota", "-u", username, "0", "0", "0", "0", mount], check=False)


def group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False
