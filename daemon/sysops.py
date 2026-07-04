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
import os
import pwd

from shared.config import settings
from shared.validation import ValidationError, generate_strong_password, validate_username

from daemon.procutil import run

NOLOGIN_SHELL = "/usr/sbin/nologin"
# Phase 4 feature 6: SSH key management. ARCHITECTURE.md SS5 originally
# scoped v1 to no interactive shell at all -- superseded explicitly by the
# Phase 4 goal, which asks for real SSH login once a key is added. A plain
# login shell, not a restricted/jailed one: the goal's own DONE WHEN bar
# ("added key allows SSH login") describes ordinary shell access, and
# every account is already isolated by ordinary Linux DAC permissions
# (home dir 711, no sudo/root capability) -- the same isolation model a
# real multi-user Unix system already relies on, not something a
# restricted shell would meaningfully add to for this goal's stated scope.
LOGIN_SHELL = "/bin/bash"


def _assert_safe_username(username: str) -> str:
    return validate_username(username)


def user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def generate_password(length: int = 20) -> str:
    return generate_strong_password(length)


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
    ensure_tmp_dir(username)
    return pw.pw_uid, pw.pw_gid


def ensure_tmp_dir(username: str) -> str:
    """Creates (idempotently) this account's own private tmp dir, used by
    the rendered vhost's open_basedir/upload_tmp_dir/TMPDIR in place of the
    shared system /tmp (security fix: every account's open_basedir used to
    include the shared, world-writable-sticky system /tmp, letting one
    account enumerate another's temp/session-adjacent filenames). Mirrors
    handlers_domain.ensure_docroot's identical tmp_dir creation (0750,
    owned by the account) -- that call site only fires at domain-add time,
    so this one covers account creation itself and, via
    ols.refresh_all_vhosts's migration pass, every pre-existing account
    that predates this fix."""
    _assert_safe_username(username)
    pw = pwd.getpwnam(username)
    tmp_dir = f"{settings.home_base}/{username}/tmp"
    os.makedirs(tmp_dir, exist_ok=True)
    os.chown(tmp_dir, pw.pw_uid, pw.pw_gid)
    os.chmod(tmp_dir, 0o750)
    return tmp_dir


def set_initial_password(username: str, password: str) -> None:
    _assert_safe_username(username)
    run(["chpasswd"], input_text=f"{username}:{password}\n", check=True)


def get_shell(username: str) -> str:
    _assert_safe_username(username)
    return pwd.getpwnam(username).pw_shell


def set_shell(username: str, shell: str) -> None:
    _assert_safe_username(username)
    if shell not in (NOLOGIN_SHELL, LOGIN_SHELL):
        raise ValidationError(f"refusing to set an unrecognized shell '{shell}'")
    run(["usermod", "-s", shell, username], check=True)


def lock_user(username: str) -> None:
    _assert_safe_username(username)
    run(["usermod", "-L", username], check=True)


def unlock_user(username: str) -> None:
    _assert_safe_username(username)
    run(["usermod", "-U", username], check=True)


def recycle_php_workers(username: str) -> None:
    """Forces this account's own persistent LSAPI PHP worker pool
    (extProcessor persistConn/autoStart, Phase 2 feature 4) to restart so
    a php_ini override change takes effect immediately, rather than
    waiting on the pool's own natural recycle schedule.

    A real, live-observed gap: confirmed during Phase 4 feature 9's own
    verification that removing a memory_limit override kept reporting the
    old value for well over a minute after a full `systemctl restart
    lshttpd` -- already-warm LSAPI worker processes keep serving whatever
    ini values they were first invoked with, and a graceful (or even
    full) web-server restart alone doesn't force them to re-read a
    per-vhost override change. delete_linux_user's own `pkill -9 -u`
    already documents this same worker pool as a known, previously-
    observed leak in a different context (account termination). Scoped
    to `-u <username> -f lsphp` -- the same "only this account's own uid"
    boundary delete_linux_user relies on, further narrowed to just PHP
    workers so an active SSH session (Phase 4 feature 6) or a running
    cron/git-deploy job for this same account is left alone."""
    run(["pkill", "-u", username, "-f", "lsphp"], timeout=15)


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
