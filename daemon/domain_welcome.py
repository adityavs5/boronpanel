"""Create and retire only Boron's own new-domain placeholder.

The tenant controls the document root. Provenance lives outside that tree;
all site operations use pinned no-follow directory descriptors. Cleanup
quarantines the candidate before checking it so a check/unlink race cannot
remove a replacement uploaded by the customer.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import logging
import os
import pwd
import secrets
import stat
import threading
from pathlib import Path

from daemon import safeio
from shared.config import settings
from shared.validation import validate_domain, validate_username

logger = logging.getLogger("borond.domain_welcome")
NAME = "welcome.html"
MAX_BYTES = 512 * 1024
_lock = threading.RLock()
_cursor = ""


def _state_dir() -> Path:
    return Path(settings.db_path).parent / "domain-welcome"


def _key(username: str, docroot: str) -> str:
    return hashlib.sha256((username + "\0" + os.path.abspath(docroot)).encode()).hexdigest() + ".json"


def _open_state(*, create: bool = False) -> int:
    path = _state_dir()
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    fd = safeio.open_dir_beneath(str(path))
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        os.close(fd)
        raise safeio.UnsafePathError("Welcome-page state directory must be private")
    return fd


def _read_record(username: str, docroot: str) -> dict | None:
    try:
        state_fd = _open_state()
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(_key(username, docroot), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=state_fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                raise safeio.UnsafePathError("Welcome-page state must be private")
            record = json.loads(os.read(fd, 8193))
            if record.get("username") != username or record.get("docroot") != os.path.abspath(docroot):
                raise safeio.UnsafePathError("Welcome-page state does not match the document root")
            return record
        finally:
            os.close(fd)
    finally:
        os.close(state_fd)


def _forget(username: str, docroot: str) -> None:
    try:
        state_fd = _open_state()
    except FileNotFoundError:
        return
    try:
        try:
            os.unlink(_key(username, docroot), dir_fd=state_fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(state_fd)


def _open_root(username: str, docroot: str) -> int:
    validate_username(username)
    home = os.path.abspath(os.path.join(settings.home_base, username))
    relative = os.path.relpath(os.path.abspath(docroot), home)
    if relative == ".." or relative.startswith("../"):
        raise safeio.UnsafePathError("Welcome page must stay inside the account home")
    return safeio.open_dir_beneath(home, relative)


def _matches(root_fd: int, record: dict, name: str = NAME) -> bool:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=root_fd)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            return False
        if (info.st_dev, info.st_ino) != (record["device"], record["inode"]):
            return False
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, 65536):
            total += len(chunk)
            if total > MAX_BYTES:
                return False
            digest.update(chunk)
        return digest.hexdigest() == record["sha256"]
    finally:
        os.close(fd)


def _has_site_content(root_fd: int, *, allow_welcome: bool) -> bool:
    with os.scandir(root_fd) as entries:
        for entry in entries:
            if allow_welcome and entry.name == NAME:
                continue
            if entry.name == ".well-known" and entry.is_dir(follow_symlinks=False):
                continue
            if entry.name == "error_pages" and entry.is_dir(follow_symlinks=False):
                child = os.open("error_pages", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
                try:
                    with os.scandir(child) as children:
                        if next(children, None) is None:
                            continue
                finally:
                    os.close(child)
            return True
    return False


def ensure_welcome(username: str, domain: str, docroot: str) -> bool:
    """Populate a genuinely empty new root. Never replace any existing file."""
    from daemon import site_templates

    username, domain = validate_username(username), validate_domain(domain)
    with _lock:
        root_fd = _open_root(username, docroot)
        try:
            if _has_site_content(root_fd, allow_welcome=False):
                return False
            content = site_templates.render_domain_welcome(domain).encode("utf-8")
            if not content or len(content) > MAX_BYTES:
                raise ValueError("Welcome template exceeds the size limit")
            user = pwd.getpwnam(username)
            try:
                fd = os.open(NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o640, dir_fd=root_fd)
            except FileExistsError:
                return False
            try:
                pending = memoryview(content)
                while pending:
                    written = os.write(fd, pending)
                    if written <= 0:
                        raise OSError("Welcome-page write made no progress")
                    pending = pending[written:]
                os.fchown(fd, user.pw_uid, user.pw_gid)
                # Preserve the docroot's inherited OLS ACL. Its mask permits
                # static reads without granting other hosting UIDs access.
                os.fchmod(fd, 0o640)
                os.fsync(fd)
                info = os.fstat(fd)
            finally:
                os.close(fd)
            state_fd = _open_state(create=True)
            os.close(state_fd)
            record = {"username": username, "domain": domain, "docroot": os.path.abspath(docroot),
                      "device": info.st_dev, "inode": info.st_ino, "sha256": hashlib.sha256(content).hexdigest()}
            safeio.secure_replace_file(str(_state_dir()), _key(username, docroot), json.dumps(record).encode(), os.geteuid(), os.getegid(), 0o600)
            return True
        finally:
            os.close(root_fd)


def is_managed_welcome(username: str, docroot: str) -> bool:
    """Installer admission ignores only the exact unchanged generated file."""
    with _lock:
        try:
            record = _read_record(username, docroot)
            if record is None:
                return False
            fd = _open_root(username, docroot)
            try:
                return _matches(fd, record)
            finally:
                os.close(fd)
        except (OSError, safeio.UnsafePathError, ValueError, KeyError):
            return False


def _rename_noreplace(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    # Ubuntu 24.04's renameat2 is atomic; a concurrent upload is never replaced.
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(source_fd, source.encode(), destination_fd, destination.encode(), 1)
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _remove_verified(root_fd: int, record: dict) -> bool:
    quarantine = ".boron-welcome-" + secrets.token_hex(12)
    os.mkdir(quarantine, 0o700, dir_fd=root_fd)
    private_fd = None
    info = None
    try:
        private_fd = os.open(quarantine, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        info = os.fstat(private_fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise safeio.UnsafePathError("Welcome cleanup directory is not private")
        try:
            _rename_noreplace(root_fd, NAME, private_fd, NAME)
        except FileNotFoundError:
            return False
        if _matches(private_fd, record):
            os.unlink(NAME, dir_fd=private_fd)
            return True
        # A custom upload won the race. Return it intact. If another upload
        # already occupies welcome.html, preserve both files instead.
        try:
            _rename_noreplace(private_fd, NAME, root_fd, NAME)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            preserved = "welcome-preserved-" + secrets.token_hex(12) + ".html"
            _rename_noreplace(private_fd, NAME, root_fd, preserved)
            logger.warning("Concurrent welcome-page upload preserved under an alternate filename")
        return False
    finally:
        if private_fd is not None:
            os.close(private_fd)
        try:
            current = os.stat(quarantine, dir_fd=root_fd, follow_symlinks=False)
            if info is not None and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                os.rmdir(quarantine, dir_fd=root_fd)
        except OSError:
            # Never recursively delete a directory controlled by a tenant.
            pass


def retire_welcome(username: str, docroot: str, *, installed: bool = False) -> bool:
    """Remove only a proven unchanged placeholder after a site is installed."""
    with _lock:
        try:
            record = _read_record(username, docroot)
            if record is None:
                return False
            root_fd = _open_root(username, docroot)
            try:
                if not _matches(root_fd, record):
                    _forget(username, docroot)
                    return False
                if not installed:
                    # Exact conventional entry points only: an uploaded readme
                    # or a .htaccess file does not mean a site is installed.
                    indexes = []
                    for name in ("index.html", "index.php"):
                        try:
                            indexes.append(os.stat(name, dir_fd=root_fd, follow_symlinks=False))
                        except FileNotFoundError:
                            continue
                    if not any(stat.S_ISREG(info.st_mode) for info in indexes):
                        return False
                removed = _remove_verified(root_fd, record)
                _forget(username, docroot)
                return removed
            finally:
                os.close(root_fd)
        except FileNotFoundError:
            _forget(username, docroot)
            return False
        except (OSError, safeio.UnsafePathError, ValueError, KeyError):
            # Customer uploads cannot turn a completed install into a failed
            # job, or persuade the daemon to fall back to unsafe unlinking.
            logger.warning("Welcome-page cleanup deferred; document root could not be verified")
            return False


def reconcile_pending(limit: int = 100) -> dict:
    """Bounded round-robin sweep; only pending placeholders are inspected.

    Real index files take precedence immediately in OLS. This removes the
    obsolete file following uploads through SFTP, file manager or deployment.
    """
    global _cursor
    removed = checked = 0
    try:
        state_fd = _open_state()
    except FileNotFoundError:
        return {"checked": 0, "removed": 0}
    try:
        names = sorted(name for name in os.listdir(state_fd) if len(name) == 69 and name.endswith(".json"))
        pending = [name for name in names if name > _cursor] + [name for name in names if name <= _cursor]
        for name in pending[:max(1, min(limit, 1000))]:
            _cursor = name
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=state_fd)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                        continue
                    record = json.loads(os.read(fd, 8193))
                finally:
                    os.close(fd)
                if name != _key(record["username"], record["docroot"]):
                    continue
                checked += 1
                removed += int(retire_welcome(record["username"], record["docroot"]))
            except (OSError, safeio.UnsafePathError, ValueError, KeyError, TypeError):
                logger.warning("Could not reconcile a pending domain welcome page")
    finally:
        os.close(state_fd)
    return {"checked": checked, "removed": removed}
