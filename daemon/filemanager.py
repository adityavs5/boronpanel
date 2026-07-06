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
import zipfile

from shared.config import settings
from shared.validation import validate_username

MAX_READ_BYTES = 10 * 1024 * 1024  # 10MB -- a text/code editor ceiling, not a download limit
MAX_WRITE_BYTES = 10 * 1024 * 1024
# Phase 8 feature 13: bounds so one search/zip can never walk the whole disk or
# build an unbounded archive.
SEARCH_MAX_FILES_SCANNED = 60_000
SEARCH_MAX_RESULTS = 500
SEARCH_CONTENT_MAX_FILE_BYTES = 2 * 1024 * 1024
ZIP_MAX_ENTRIES = 50_000
ZIP_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2GB


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


# --- Phase 8 feature 13: copy, bulk ops, zip, search -----------------------


def _chown_recursive(path: str, uid: int, gid: int) -> None:
    """A copy creates inodes owned by root (the daemon) -- re-own them to the
    account and set the same 0750 dir / 0640 file perms write_file/mkdir use, so
    a copied tree is never left root-owned (unreadable to the account) or with
    widened permissions (the shutil.copytree copystat-widening class of bug this
    project's STATUS.md already flags)."""
    if os.path.islink(path):
        os.lchown(path, uid, gid)
        return
    os.chown(path, uid, gid)
    if os.path.isdir(path):
        os.chmod(path, 0o750)
        for root, dirs, files in os.walk(path):
            for name in dirs:
                p = os.path.join(root, name)
                if os.path.islink(p):
                    os.lchown(p, uid, gid)
                else:
                    os.chown(p, uid, gid)
                    os.chmod(p, 0o750)
            for name in files:
                p = os.path.join(root, name)
                if os.path.islink(p):
                    os.lchown(p, uid, gid)
                else:
                    os.chown(p, uid, gid)
                    os.chmod(p, 0o640)
    else:
        os.chmod(path, 0o640)


def copy(params: dict) -> dict:
    username = params["username"]
    src, _ = _resolve_entry(username, params["src"])
    dst, _ = _resolve_entry(username, params["dst"])
    if not os.path.lexists(src):
        raise FileManagerError(f"'{params['src']}' does not exist")
    if os.path.lexists(dst):
        raise FileManagerError(f"'{params['dst']}' already exists")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isdir(src) and not os.path.islink(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)
    uid, gid = _account_uid_gid(username)
    _chown_recursive(dst, uid, gid)
    return {"src": params["src"], "dst": params["dst"], "status": "copied"}


def _bulk(username: str, paths: list, op) -> dict:
    """Apply `op(path)` to each of `paths`, jailed, collecting per-item results
    rather than aborting the whole batch on one failure."""
    results = []
    for path in paths:
        try:
            op(path)
            results.append({"path": path, "ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"path": path, "ok": False, "detail": str(exc)})
    ok = sum(1 for r in results if r["ok"])
    return {"results": results, "ok_count": ok, "total": len(results)}


def bulk_delete(params: dict) -> dict:
    username = params["username"]
    return _bulk(username, params.get("paths", []), lambda p: delete({"username": username, "path": p}))


def _dest_child(username: str, dest_dir: str, path: str) -> str:
    # dest is a directory (relative to home); each item keeps its basename.
    base = os.path.basename(path.rstrip("/"))
    return f"{dest_dir.rstrip('/')}/{base}" if dest_dir.strip("/") else base


def bulk_move(params: dict) -> dict:
    username = params["username"]
    dest = params["dest"]
    return _bulk(
        username, params.get("paths", []),
        lambda p: move({"username": username, "src": p, "dst": _dest_child(username, dest, p)}),
    )


def bulk_copy(params: dict) -> dict:
    username = params["username"]
    dest = params["dest"]
    return _bulk(
        username, params.get("paths", []),
        lambda p: copy({"username": username, "src": p, "dst": _dest_child(username, dest, p)}),
    )


def make_zip(params: dict) -> dict:
    """Zip the selected (jailed) paths into an archive within the account home.
    The archive is owned by the account (0640)."""
    username = params["username"]
    paths = params.get("paths", [])
    archive_rel = params["archive"]
    if not archive_rel.endswith(".zip"):
        archive_rel += ".zip"
    archive_abs, home = _resolve_entry(username, archive_rel)
    if os.path.lexists(archive_abs):
        raise FileManagerError(f"'{archive_rel}' already exists")

    entries = 0
    total_bytes = 0
    with zipfile.ZipFile(archive_abs, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            resolved, _ = _resolve(username, path)  # read the content -> reject escaping symlinks
            arc_base = os.path.basename(resolved.rstrip("/"))
            if os.path.isdir(resolved):
                for root, _dirs, files in os.walk(resolved):
                    for name in files:
                        fp = os.path.join(root, name)
                        if os.path.islink(fp):
                            continue
                        entries += 1
                        total_bytes += os.path.getsize(fp)
                        if entries > ZIP_MAX_ENTRIES or total_bytes > ZIP_MAX_TOTAL_BYTES:
                            raise FileManagerError("archive would exceed the size/entry limit")
                        arcname = os.path.join(arc_base, os.path.relpath(fp, resolved))
                        zf.write(fp, arcname)
            elif os.path.isfile(resolved):
                entries += 1
                total_bytes += os.path.getsize(resolved)
                if entries > ZIP_MAX_ENTRIES or total_bytes > ZIP_MAX_TOTAL_BYTES:
                    raise FileManagerError("archive would exceed the size/entry limit")
                zf.write(resolved, arc_base)

    uid, gid = _account_uid_gid(username)
    os.chown(archive_abs, uid, gid)
    os.chmod(archive_abs, 0o640)
    return {"archive": archive_rel, "entries": entries, "size_bytes": os.path.getsize(archive_abs)}


def search(params: dict) -> dict:
    """Search under a jailed root by file NAME (substring) or by CONTENT (grep
    of text files). Bounded in files scanned, per-file size, and results."""
    username = params["username"]
    root, home = _resolve(username, params.get("path", ""))
    if not os.path.isdir(root):
        raise FileManagerError("search root is not a directory")
    query = (params.get("query") or "").strip()
    if not query:
        raise FileManagerError("search query must not be empty")
    mode = params.get("mode", "name")  # name | content
    q_lower = query.lower()

    results = []
    scanned = 0
    for dirpath, dirs, files in os.walk(root):
        # never descend into a symlinked directory (stay in the jail)
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(dirpath, d))]
        for name in files:
            scanned += 1
            if scanned > SEARCH_MAX_FILES_SCANNED or len(results) >= SEARCH_MAX_RESULTS:
                break
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, home)
            if mode == "name":
                if q_lower in name.lower():
                    results.append({"path": rel, "name": name})
            else:  # content
                try:
                    if os.path.islink(full) or os.path.getsize(full) > SEARCH_CONTENT_MAX_FILE_BYTES:
                        continue
                    with open(full, "r", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            if q_lower in line.lower():
                                results.append({"path": rel, "name": name, "line": lineno, "text": line.strip()[:300]})
                                break
                except OSError:
                    continue
        if scanned > SEARCH_MAX_FILES_SCANNED or len(results) >= SEARCH_MAX_RESULTS:
            break
    return {"results": results, "count": len(results), "truncated": len(results) >= SEARCH_MAX_RESULTS}
