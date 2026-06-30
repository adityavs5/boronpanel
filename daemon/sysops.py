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
    # home dir mode 750: owner rwx, group rx (own private group only), world none
    run(["chmod", "750", home_dir], check=True)

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
