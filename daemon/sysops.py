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
import re
import stat
import subprocess

from shared.config import settings
from shared.validation import ValidationError, generate_strong_password, validate_password_text, validate_username

from daemon.procutil import run
from daemon.safeio import secure_mkdirs

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
            "--comment", "Boron hosting account",
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
    ensure_php_session_dir(username)
    # The default file-backup scope must exist even before a first domain.
    # Keep it private; domain provisioning adds the web-server ACL later.
    secure_mkdirs(home_dir, "public_html", pw.pw_uid, pw.pw_gid, 0o750)
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
    home = f"{settings.home_base}/{username}"
    return secure_mkdirs(home, "tmp", pw.pw_uid, pw.pw_gid, 0o750)


def ensure_php_session_dir(username: str) -> str:
    """Create the account-only PHP session directory, idempotently."""
    _assert_safe_username(username)
    pw = pwd.getpwnam(username)
    home = f"{settings.home_base}/{username}"
    ensure_tmp_dir(username)
    return secure_mkdirs(home, "tmp/sessions", pw.pw_uid, pw.pw_gid, 0o700)


# Per-vhost OLS access/error logs. OLS opens them as the shared ``nobody``
# identity and follows symlinks, so they must never live in a directory a
# tenant can write: under ~/logs a planted ``<vhost>-access.log ->
# /home/<other>/logs/...`` symlink redirected one account's log lines into
# another account's files. Each account gets a root-owned directory here
# that only ``nobody`` may write; the account itself can read it (ACL), and
# the panel log viewer/downloads (daemon/logs.py) read it on its behalf.
VHOST_LOG_ROOT = "/var/log/boron-vhosts"
# <vhost>-access.log / <vhost>-error.log (+ rotations). A vhost name is a
# domain with dots replaced by "_", so it always contains one ("php-error.log",
# the account's own PHP log, never matches).
_VHOST_LOG_NAME_RE = re.compile(
    r"\A(?P<log>[a-z0-9-]+(?:_[a-z0-9-]+)+-(?:access|error)\.log)(?:\.(?P<archive>[A-Za-z0-9_.-]{1,64}))?\Z"
)
# OLS rolls each vhost log at 50 MB and deletes archives older than 90 days
# itself (templates/vhost.conf.j2), but it has no limit on how many archives
# a busy site collects inside that window, and they all live on /var. Only
# the newest archives within both bounds are kept per log (prune_vhost_logs,
# run by every usage refresh; the ~/logs migration moves no more than that).
VHOST_LOG_KEEP_ARCHIVES = 10
VHOST_LOG_KEEP_ARCHIVE_BYTES = 512 * 1024 * 1024
# Copying old logs across file systems stops while /var would keep less
# free space than this (the larger of the two).
_VHOST_LOG_COPY_RESERVE_BYTES = 1024 ** 3
_VHOST_LOG_COPY_RESERVE_FRACTION = 0.05


def _archives_to_keep(entries: list[tuple[str, float, int]]) -> set[str]:
    """Names of the rotated archives that stay, from (name, mtime, size) of
    every file of one account's vhost logs: per log, the newest ones within
    VHOST_LOG_KEEP_ARCHIVES and VHOST_LOG_KEEP_ARCHIVE_BYTES."""
    by_log: dict[str, list[tuple[float, str, int]]] = {}
    for name, mtime, size in entries:
        match = _VHOST_LOG_NAME_RE.match(name)
        if match is not None and match["archive"] is not None:
            by_log.setdefault(match["log"], []).append((mtime, name, size))
    keep = set()
    for archives in by_log.values():
        total = 0
        for position, (_mtime, name, size) in enumerate(sorted(archives, reverse=True)):
            total += size
            if position >= VHOST_LOG_KEEP_ARCHIVES or total > VHOST_LOG_KEEP_ARCHIVE_BYTES:
                break
            keep.add(name)
    return keep


def prune_vhost_logs(username: str) -> int:
    """Delete the account's oldest rotated vhost log archives beyond the
    per-log bound; active logs are never touched. Returns how many went."""
    from daemon.safeio import UnsafePathError, open_dir_beneath

    _assert_safe_username(username)
    try:
        directory = open_dir_beneath(VHOST_LOG_ROOT, username)
    except (OSError, UnsafePathError):
        return 0
    removed = 0
    try:
        archives = []
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 8192:
                    break
                match = _VHOST_LOG_NAME_RE.match(entry.name)
                if match is None or match["archive"] is None:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    archives.append((entry.name, info.st_mtime, info.st_size))
        keep = _archives_to_keep(archives)
        for name, _mtime, _size in archives:
            if name in keep:
                continue
            try:
                os.unlink(name, dir_fd=directory)
                removed += 1
            except FileNotFoundError:
                continue
    finally:
        os.close(directory)
    return removed


def vhost_log_dir(username: str) -> str:
    return f"{VHOST_LOG_ROOT}/{_assert_safe_username(username)}"


def _vhost_log_root() -> str:
    """Create and verify the shared root: a real root-owned directory that
    nobody else can write (0711: accounts cannot list who has logs)."""
    from daemon.safeio import UnsafePathError, open_dir_beneath

    os.makedirs(VHOST_LOG_ROOT, mode=0o711, exist_ok=True)
    fd = open_dir_beneath(VHOST_LOG_ROOT)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise UnsafePathError(f"'{VHOST_LOG_ROOT}' is not a root-owned private directory")
        if stat.S_IMODE(info.st_mode) != 0o711:
            os.fchmod(fd, 0o711)
    finally:
        os.close(fd)
    return VHOST_LOG_ROOT


def ensure_vhost_log_dir(username: str) -> str:
    """Create (idempotently) the account's root-owned vhost log directory:
    writable by OLS's ``nobody`` worker only, readable by the account."""
    _assert_safe_username(username)
    pw = pwd.getpwnam(username)
    root = _vhost_log_root()
    directory = f"{root}/{username}"
    existed = os.path.isdir(directory) and not os.path.islink(directory)
    # Owned by the daemon's own identity (root in production).
    secure_mkdirs(root, username, os.geteuid(), os.getegid(), 0o750)
    if not existed:
        # The directory and its parent are root-owned and not writable by
        # anyone else, so this path cannot be swapped under setfacl.
        run([
            "setfacl", "-m", f"u:nobody:rwx,u:{pw.pw_uid}:r-x",
            "-m", f"d:u:nobody:rw-,d:u:{pw.pw_uid}:r--", directory,
        ], check=True)
    return directory


def ensure_web_logs(username: str) -> str:
    """Provision both log locations before an OLS apply: the account's own
    ~/logs (PHP, application and Redis logs, written as the account) and
    the root-owned vhost log directory OLS writes. Also moves vhost logs
    from the pre-hardening ~/logs location and withdraws the web server's
    write access there, so this is the upgrade migration as well
    (ols.refresh_all_vhosts runs it for every account)."""
    _assert_safe_username(username)
    pw = pwd.getpwnam(username)
    home = f"{settings.home_base}/{username}"
    logs = secure_mkdirs(home, "logs", pw.pw_uid, pw.pw_gid, 0o750)
    target = ensure_vhost_log_dir(username)
    _migrate_legacy_vhost_logs(username, home)
    # Run as the account so a swapped path can only affect its own files.
    run(["setfacl", "-x", "u:nobody", "-x", "d:u:nobody", logs], uid=pw.pw_uid, gid=pw.pw_gid)
    return target


def _migrate_legacy_vhost_logs(username: str, home: str) -> int:
    """Move OLS-written vhost logs (regular files owned by ``nobody`` with a
    single link -- the account can create neither) out of ~/logs. Anything
    the account created itself stays where it is. Same-filesystem renames
    keep OLS's open descriptors writing to the moved file until the vhost
    is re-rendered; across filesystems the file is copied.

    The move is bounded like the logs themselves: each active log plus only
    the archives prune_vhost_logs would keep. Older archives, and any file
    whose copy would leave /var with less than its reserve, stay in ~/logs
    (still the account's to read or delete) and are logged."""
    import errno
    import logging
    import shutil

    from daemon.safeio import UnsafePathError, open_dir_beneath

    log = logging.getLogger("borond.sysops")
    try:
        nobody_uid = pwd.getpwnam("nobody").pw_uid
        legacy_fd = open_dir_beneath(home, "logs")
    except (KeyError, FileNotFoundError, UnsafePathError, OSError):
        return 0
    moved = 0
    left = 0
    try:
        target_fd = open_dir_beneath(VHOST_LOG_ROOT, username)
        try:
            with os.scandir(legacy_fd) as entries:
                names = [entry.name for entry in entries if _VHOST_LOG_NAME_RE.match(entry.name)]
            candidates = []
            for name in names[:4096]:
                try:
                    info = os.stat(name, dir_fd=legacy_fd, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode) and info.st_uid == nobody_uid and info.st_nlink == 1:
                    candidates.append((name, info.st_mtime, info.st_size))
            keep = _archives_to_keep(candidates)
            for name, _mtime, size in candidates:
                if _VHOST_LOG_NAME_RE.match(name)["archive"] is not None and name not in keep:
                    left += 1
                    continue
                try:
                    try:
                        os.stat(name, dir_fd=target_fd, follow_symlinks=False)
                        continue  # never overwrite a log OLS already writes
                    except FileNotFoundError:
                        pass
                    try:
                        os.rename(name, name, src_dir_fd=legacy_fd, dst_dir_fd=target_fd)
                    except OSError as exc:
                        if exc.errno != errno.EXDEV:
                            raise
                        space = os.fstatvfs(target_fd)
                        reserve = max(_VHOST_LOG_COPY_RESERVE_BYTES,
                                      int(space.f_blocks * space.f_frsize * _VHOST_LOG_COPY_RESERVE_FRACTION))
                        if space.f_bavail * space.f_frsize - size < reserve:
                            left += 1
                            continue
                        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=legacy_fd)
                        try:
                            destination = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                                  | os.O_CLOEXEC, 0o640, dir_fd=target_fd)
                            try:
                                with os.fdopen(source, "rb", closefd=False) as reader, \
                                        os.fdopen(destination, "wb", closefd=False) as writer:
                                    shutil.copyfileobj(reader, writer)
                                os.fchown(destination, nobody_uid, -1)
                            finally:
                                os.close(destination)
                        finally:
                            os.close(source)
                        os.unlink(name, dir_fd=legacy_fd)
                    moved += 1
                except OSError:
                    log.exception("could not move vhost log '%s' for account '%s'", name, username)
        finally:
            os.close(target_fd)
    finally:
        os.close(legacy_fd)
    if left:
        log.warning("left %d older or oversized vhost log file(s) in %s/logs for account '%s'; only each "
                    "active log and its newest archives move to %s", left, home, username, VHOST_LOG_ROOT)
    return moved


def rename_vhost_log_dir(old_username: str, new_username: str) -> None:
    """Account rename: the vhost log directory lives outside the home, so
    move it explicitly (DB-independent, so it doubles as its own undo)."""
    _assert_safe_username(old_username)
    _assert_safe_username(new_username)
    if not os.path.isdir(VHOST_LOG_ROOT):
        return
    from daemon.safeio import open_dir_beneath

    root_fd = open_dir_beneath(_vhost_log_root())
    try:
        try:
            info = os.stat(old_username, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(info.st_mode):
            os.rename(old_username, new_username, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    finally:
        os.close(root_fd)
    pw = pwd.getpwnam(new_username)
    run(["setfacl", "-m", f"u:{pw.pw_uid}:r-x", "-m", f"d:u:{pw.pw_uid}:r--", vhost_log_dir(new_username)])


def remove_vhost_log_dir(username: str) -> None:
    """Account termination: the logs used to go with the home directory."""
    import shutil

    _assert_safe_username(username)
    if not os.path.isdir(VHOST_LOG_ROOT):
        return
    from daemon.safeio import open_dir_beneath

    root_fd = open_dir_beneath(_vhost_log_root())
    try:
        try:
            info = os.stat(username, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(info.st_mode) and shutil.rmtree.avoids_symlink_attacks:
            shutil.rmtree(username, dir_fd=root_fd)
        elif not stat.S_ISDIR(info.st_mode):
            os.unlink(username, dir_fd=root_fd)
    finally:
        os.close(root_fd)


def set_initial_password(username: str, password: str) -> None:
    _assert_safe_username(username)
    validate_password_text(password)
    run(["chpasswd"], input_text=f"{username}:{password}\n", check=True)


def get_shell(username: str) -> str:
    _assert_safe_username(username)
    return pwd.getpwnam(username).pw_shell


def set_shell(username: str, shell: str) -> None:
    _assert_safe_username(username)
    if shell not in (NOLOGIN_SHELL, LOGIN_SHELL):
        raise ValidationError(f"refusing to set an unrecognized shell '{shell}'")
    run(["usermod", "-s", shell, username], check=True)


def rename_login(old_username: str, new_username: str) -> None:
    """Phase 8 feature 2: rename a Linux login (`usermod -l`). uid/gid are
    preserved (so quotas and namespace, both keyed by uid, need no change).
    Does NOT move the home dir -- see move_home. Split into discrete
    primitives so the rename saga (daemon/identity_admin.py) can compensate
    each step independently on failure."""
    _assert_safe_username(old_username)
    _assert_safe_username(new_username)
    run(["usermod", "-l", new_username, old_username], check=True)


def rename_group(old_name: str, new_name: str) -> None:
    """Rename the account's primary group (same name as the login on Ubuntu
    with USERGROUPS_ENAB) via `groupmod -n`."""
    _assert_safe_username(old_name)
    _assert_safe_username(new_name)
    run(["groupmod", "-n", new_name, old_name], check=True)


def move_home(login: str, new_home: str) -> None:
    """Move `login`'s home dir to `new_home` (`usermod -d <new_home> -m`).
    Same-filesystem move preserves ownership/mode/POSIX ACLs (the
    setfacl u:nobody:rX grant survives), so no re-ACL is needed afterward."""
    _assert_safe_username(login)
    run(["usermod", "-d", new_home, "-m", login], check=True)


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


def quiesce_user(username: str) -> None:
    """End account sessions before an explicitly requested identity change."""
    import time
    _assert_safe_username(username)
    owner = pwd.getpwnam(username)
    if owner.pw_uid < 1000:
        raise ValidationError("Refusing to stop a system user's processes")
    run(["loginctl", "terminate-user", str(owner.pw_uid)], timeout=15)
    run(["pkill", "-TERM", "-u", str(owner.pw_uid)], timeout=15)
    deadline = time.monotonic() + 5
    while run(["pgrep", "-u", str(owner.pw_uid)], timeout=5).ok:
        if time.monotonic() >= deadline:
            run(["pkill", "-KILL", "-u", str(owner.pw_uid)], timeout=15)
            reap_deadline = time.monotonic() + 3
            while run(["pgrep", "-u", str(owner.pw_uid)], timeout=5).ok:
                if time.monotonic() >= reap_deadline:
                    raise ValidationError("Account processes are still shutting down. Retry the rename shortly.")
                time.sleep(0.1)
            break
        time.sleep(0.1)


def delete_linux_user(username: str, *, remove_home: bool = True) -> None:
    _assert_safe_username(username)
    if not user_exists(username):
        if remove_home:
            _remove_orphaned_home(username)
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
    try:
        # WordPress/plugin trees can contain many thousands of files.  The
        # generic 30-second command limit can expire after userdel has already
        # removed passwd/group entries but while it is still removing $HOME.
        run(["userdel", *(["--remove"] if remove_home else []), "--force", username], timeout=600, check=True)
    except subprocess.TimeoutExpired:
        if user_exists(username):
            raise
        if remove_home:
            _remove_orphaned_home(username)


def _remove_orphaned_home(username: str) -> None:
    """Finish a timed-out userdel without following links or crossing mounts."""
    expected = os.path.join(settings.home_base, username)
    try:
        info = os.lstat(expected)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode) or os.path.dirname(expected) != os.path.normpath(settings.home_base):
        raise RuntimeError('Refusing to remove an unsafe account home path')
    run(["find", expected, "-xdev", "-depth", "-delete"], timeout=600, check=True)


def set_quota(
    username: str,
    soft_mb: int,
    hard_mb: int,
    mount: str = "/",
    *,
    inode_soft: int = 0,
    inode_hard: int = 0,
) -> None:
    _assert_safe_username(username)
    if inode_soft < 0 or inode_hard < 0 or (inode_hard and inode_soft > inode_hard):
        raise ValidationError("inode quota must be zero or a soft limit no greater than the hard limit")
    soft_blocks = soft_mb * 1024
    hard_blocks = hard_mb * 1024
    run(
        [
            "setquota",
            "-u", username,
            str(soft_blocks), str(hard_blocks),
            str(inode_soft), str(inode_hard),
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
