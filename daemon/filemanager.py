"""Web-based file manager, scoped/jailed to each account's home dir
(Phase g, v1 scope item 3).

ARCHITECTURE.md SS10: file I/O happens here, in forgehostd (root), not in
the unprivileged forgehost-api process -- "only the daemon touches
account-owned files" stays an absolute invariant, and every file-manager
action becomes an audited RPC call for free (server.py's dispatch() audits
every op uniformly). Every path is resolved with os.path.realpath and
checked to remain within the account's home dir before any read/write/
delete touches disk -- this is the actual jail, applied here regardless of
whatever checking the API layer above also does (defense in depth: even a
buggy API-layer check can't escape this one).
"""
from __future__ import annotations

import os
import pwd
import shutil
import stat as stat_module

from shared.config import settings
from shared.validation import validate_username

MAX_READ_BYTES = 10 * 1024 * 1024  # 10MB -- a text/code editor ceiling, not a download limit
MAX_WRITE_BYTES = 10 * 1024 * 1024


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


def _resolve_entry(username: str, relative_path: str) -> tuple[str, str]:
    """Like _resolve(), but for operations on the directory entry itself
    (delete, move/rename) rather than its content: the parent directory is
    fully resolved and jail-checked, but the final path component is NOT
    followed if it's a symlink. Deleting/renaming a symlink the account
    created -- even one pointing outside the jail -- only ever touches that
    directory entry, never the target, so it must be allowed; only _resolve()
    (read/write, which open the target) needs to reject an escaping symlink.
    Caught by real testing: an account that created an escaping symlink (to
    confirm it couldn't be read through) then couldn't delete that same
    symlink to clean up after itself."""
    home = _account_home(username)
    relative_path = (relative_path or "").lstrip("/")
    candidate = os.path.normpath(os.path.join(home, relative_path))
    if candidate == home:
        return candidate, home

    parent = os.path.dirname(candidate)
    basename = os.path.basename(candidate)
    resolved_parent = os.path.realpath(parent)
    if resolved_parent != home and not resolved_parent.startswith(home + os.sep):
        raise FileManagerError("path escapes the account's home directory")
    return os.path.join(resolved_parent, basename), home


def _account_uid_gid(username: str) -> tuple[int, int]:
    pw = pwd.getpwnam(username)
    return pw.pw_uid, pw.pw_gid


def list_dir(params: dict) -> dict:
    username = params["username"]
    path, home = _resolve(username, params.get("path", ""))
    if not os.path.isdir(path):
        raise FileManagerError(f"'{params.get('path', '')}' is not a directory")

    entries = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "is_dir": stat_module.S_ISDIR(st.st_mode),
                "is_symlink": stat_module.S_ISLNK(st.st_mode),
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "mode": stat_module.S_IMODE(st.st_mode),
            }
        )
    return {"path": params.get("path", ""), "entries": entries}


def read_file(params: dict) -> dict:
    username = params["username"]
    path, _ = _resolve(username, params["path"])
    if not os.path.isfile(path):
        raise FileManagerError(f"'{params['path']}' is not a file")
    size = os.path.getsize(path)
    if size > MAX_READ_BYTES:
        raise FileManagerError(f"file is {size} bytes, exceeds the {MAX_READ_BYTES}-byte read limit")
    with open(path, "rb") as f:
        raw = f.read()
    try:
        content = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        import base64

        content = base64.b64encode(raw).decode("ascii")
        encoding = "base64"
    return {"path": params["path"], "encoding": encoding, "content": content, "size": size}


def write_file(params: dict) -> dict:
    username = params["username"]
    path, _ = _resolve(username, params["path"])
    if os.path.isdir(path):
        raise FileManagerError(f"'{params['path']}' is a directory")

    content = params["content"]
    encoding = params.get("encoding", "utf-8")
    if encoding == "base64":
        import base64

        raw = base64.b64decode(content)
    else:
        raw = content.encode("utf-8")
    if len(raw) > MAX_WRITE_BYTES:
        raise FileManagerError(f"content is {len(raw)} bytes, exceeds the {MAX_WRITE_BYTES}-byte write limit")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)
    uid, gid = _account_uid_gid(username)
    os.chown(path, uid, gid)
    os.chmod(path, 0o640)
    return {"path": params["path"], "size": len(raw)}


def mkdir(params: dict) -> dict:
    username = params["username"]
    path, _ = _resolve(username, params["path"])
    uid, gid = _account_uid_gid(username)
    os.makedirs(path, exist_ok=True)
    os.chown(path, uid, gid)
    os.chmod(path, 0o750)
    return {"path": params["path"]}


def delete(params: dict) -> dict:
    username = params["username"]
    path, home = _resolve_entry(username, params["path"])
    if path == home:
        raise FileManagerError("refusing to delete the account home directory itself")
    if not os.path.lexists(path):
        raise FileManagerError(f"'{params['path']}' does not exist")
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)  # plain file, or a symlink (removes the link itself, never its target)
    return {"path": params["path"], "status": "deleted"}


def move(params: dict) -> dict:
    username = params["username"]
    src, _ = _resolve_entry(username, params["src"])
    dst, _ = _resolve_entry(username, params["dst"])
    if not os.path.lexists(src):
        raise FileManagerError(f"'{params['src']}' does not exist")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return {"src": params["src"], "dst": params["dst"], "status": "moved"}
