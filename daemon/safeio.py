"""Symlink-safe filesystem primitives for the root daemon.

forgehostd runs as ROOT and routinely creates, chowns, and writes files
*inside an account's own home directory* -- a location the account itself can
write to (via the web terminal, Phase 8 f7, or an added SSH key, Phase 4 f6).

A naive ``os.makedirs(path, exist_ok=True); os.chown(path, uid, gid)`` is a
privilege-escalation primitive there: the account pre-plants a symlink at
``path`` (e.g. ``ln -s /etc/systemd/system ~/.ssh``), ``makedirs(exist_ok=True)``
silently no-ops because the target already exists, and ``os.chown``/``os.chmod``
(which follow symlinks) then re-own an arbitrary root-owned target to the
account. Arbitrary-file ownership is a full local root compromise.

These helpers create and re-own path components one at a time through
``O_NOFOLLOW`` ``openat``/``mkdirat`` relative to an already-open, trusted
directory fd, and do metadata changes via the resulting fd (``fchown``/
``fchmod``) -- so no symlink is ever traversed, and a concurrent swap can't
redirect the operation. The same daemon/filemanager.py._chown_recursive fix
uses the same primitives; this module generalizes them for the create+chown
path every other feature module shares.
"""
from __future__ import annotations

import os
import stat


class UnsafePathError(Exception):
    """A path component that had to be a real file/directory was a symlink (or
    otherwise the wrong type). Refused rather than followed."""


def _open_dir_nofollow(path: str, *, dir_fd: int | None = None) -> int:
    """Open a directory without following a symlink at its final component."""
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


def _reject_name(name: str) -> None:
    if not name or "/" in name or name in (".", ".."):
        raise UnsafePathError(f"'{name}' is not a single safe path component")


def secure_mkdirs(trusted_root: str, relative: str, uid: int, gid: int, mode: int = 0o750) -> str:
    """Ensure ``trusted_root/relative`` exists as a real directory tree, owned
    by (uid, gid) with ``mode`` on every component of ``relative``, without ever
    following a symlink.

    ``trusted_root`` must be a directory the account cannot replace -- i.e. one
    that sits under a root-owned parent, such as ``<home_base>/<user>`` beneath
    the root-owned ``<home_base>``. It is opened ``O_NOFOLLOW`` as the anchor and
    is itself left untouched (not re-chowned). Each component of ``relative`` is
    then created (``mkdirat``) and/or opened (``openat``) ``O_NOFOLLOW``: an
    existing symlink at any level raises :class:`UnsafePathError` instead of
    being traversed. Returns the absolute path of the leaf directory.
    """
    parts = [p for p in relative.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise UnsafePathError(f"'{relative}' contains a '..' segment")

    opened: list[int] = []
    try:
        parent_fd = _open_dir_nofollow(trusted_root)
        opened.append(parent_fd)
        for part in parts:
            try:
                os.mkdir(part, mode, dir_fd=parent_fd)
            except FileExistsError:
                pass
            try:
                child_fd = _open_dir_nofollow(part, dir_fd=parent_fd)
            except OSError as exc:
                # ELOOP (a symlink was planted here) or ENOTDIR (a plain file).
                raise UnsafePathError(
                    f"'{relative}': component '{part}' is not a real directory"
                ) from exc
            os.fchown(child_fd, uid, gid)
            os.fchmod(child_fd, mode)
            opened.append(child_fd)
            parent_fd = child_fd
        return os.path.join(trusted_root, *parts) if parts else trusted_root
    finally:
        for fd in opened:
            os.close(fd)


def secure_ensure_file(dir_path: str, name: str, uid: int, gid: int, mode: int = 0o640) -> None:
    """Ensure a plain file ``name`` exists inside the real directory
    ``dir_path`` (creating it empty if missing, never truncating an existing
    one) and set its owner/mode -- without following a symlink at either
    ``dir_path`` or ``name``. If ``name`` is already a symlink, the O_NOFOLLOW
    open fails and this raises :class:`UnsafePathError`."""
    _reject_name(name)
    dir_fd = _open_dir_nofollow(dir_path)
    try:
        try:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_CLOEXEC,
                mode,
                dir_fd=dir_fd,
            )
        except OSError as exc:
            raise UnsafePathError(f"'{name}' in '{dir_path}' is not a regular file") from exc
        try:
            os.fchown(fd, uid, gid)
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def secure_read_text(dir_path: str, name: str, max_bytes: int = 1024 * 1024) -> str | None:
    """Read up to ``max_bytes`` of text from ``name`` inside ``dir_path``
    without following a symlink at either. Returns ``None`` if the file is
    absent, a symlink, or otherwise not a regular file -- so a symlink planted
    at ``name`` (pointing at e.g. /etc/shadow) can never have its target read
    and later rewritten back out as an account-owned file."""
    _reject_name(name)
    try:
        dir_fd = _open_dir_nofollow(dir_path)
    except OSError:
        return None
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            return os.read(fd, max_bytes).decode("utf-8", "replace")
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def secure_unlink(dir_path: str, name: str) -> None:
    """Remove ``name`` inside the real directory ``dir_path`` without
    following a symlink at either -- ``unlinkat`` itself never follows a
    symlink at the final component (POSIX), so this only needs the
    O_NOFOLLOW-opened parent to be safe against a symlink *earlier* in
    ``dir_path``. Missing file/dir is not an error (idempotent, matching
    every other delete_*_for_domain cleanup hook in this project)."""
    _reject_name(name)
    try:
        dir_fd = _open_dir_nofollow(dir_path)
    except OSError:
        return
    try:
        try:
            os.unlink(name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(dir_fd)


def secure_replace_file(dir_path: str, name: str, data: bytes, uid: int, gid: int, mode: int = 0o600) -> None:
    """Atomically (re)write ``name`` inside the real directory ``dir_path`` with
    ``data``, owned (uid, gid) mode ``mode``, symlink-safe at every step:
    ``dir_path`` is opened ``O_NOFOLLOW`` as the anchor; the temp file is
    created ``O_EXCL | O_NOFOLLOW`` relative to it (so a pre-planted symlink/
    file can't be written through); and the ``renameat`` over ``name`` replaces
    a symlink at the destination rather than following it."""
    _reject_name(name)
    if isinstance(data, str):
        data = data.encode()
    dir_fd = _open_dir_nofollow(dir_path)
    try:
        tmp = f".{name}.tmp.{os.getpid()}.{os.urandom(4).hex()}"
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode,
            dir_fd=dir_fd,
        )
        try:
            os.write(fd, data)
            os.fchown(fd, uid, gid)
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
        try:
            os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except OSError:
            os.unlink(tmp, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)
