"""Shared per-account path-jail helpers.

Originally this module was Boron's custom file manager (Phase g, and the
Phase 8 f13 bulk/zip/search additions). That file manager was **retired
2026-07-09** and replaced by FileBrowser Quantum (daemon/filebrowser.py +
api/routers/filebrowser.py) after it was verified live end-to-end. What remains
here are only the realpath-based jail helpers that several *other* features
still reuse to keep their own file access inside an account's home dir:

  - daemon/fileauth.py   (directory privacy)
  - daemon/composerui.py (Composer working dir)
  - daemon/disktree.py   (disk-usage treemap)
  - daemon/gitrepo.py    (git repo paths)

`_resolve` / `_account_home` are kept byte-for-byte from the original so those
features' jail semantics are unchanged. ARCHITECTURE.md §10's invariant still
holds: every path is resolved with os.path.realpath and checked to remain within
the account's home dir before any read/write/delete touches disk.
"""
from __future__ import annotations

import os

from shared.config import settings
from shared.validation import validate_username


class FileManagerError(Exception):
    pass


def _account_home(username: str) -> str:
    validate_username(username)
    home = os.path.realpath(f"{settings.home_base}/{username}")
    if not os.path.isdir(home):
        raise FileManagerError(f"account '{username}' has no home directory")
    return home


def _resolve(username: str, relative_path: str) -> tuple[str, str]:
    """Returns (resolved_absolute_path, account_home). Raises
    FileManagerError if the resolved path would escape the account's home
    dir -- including via a symlink, since os.path.realpath follows them."""
    home = _account_home(username)
    relative_path = (relative_path or "").lstrip("/")
    candidate = os.path.normpath(os.path.join(home, relative_path))

    # Resolve symlinks on the existing portion of the path. For a
    # not-yet-existing target (write/mkdir), realpath resolves as much as
    # exists and appends the rest literally -- so also check the nearest
    # existing ancestor, which catches a symlinked *parent* directory
    # pointing outside the jail even before the new file/dir is created.
    resolved = os.path.realpath(candidate)
    ancestor = resolved
    while not os.path.exists(ancestor) and ancestor != home and len(ancestor) > len(home):
        ancestor = os.path.dirname(ancestor)
    check_target = os.path.realpath(ancestor)

    if check_target != home and not check_target.startswith(home + os.sep):
        raise FileManagerError("path escapes the account's home directory")
    if resolved != home and not resolved.startswith(home + os.sep):
        raise FileManagerError("path escapes the account's home directory")
    return resolved, home
