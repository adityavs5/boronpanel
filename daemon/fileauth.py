"""Per-directory password protection (Phase 4 feature 4), OLS's native
`realm`/`userDB` mechanism -- the real syntax was confirmed empirically
against this server's own installed OLS (its docs describe the fields but
not the exact block layout), not assumed from documentation prose alone:

    realm <name> {
      userDB {
        location  <path-to-.htpasswd>
      }
    }
    context /<subpath>/ {
      location  <docroot>/<subpath>/
      realm     <name>
    }

`openlitespeed -t` accepted this, and a live `curl` test against a real
protected path (with a real hash written by `htpasswd`) confirmed all of
bcrypt (`-B`), apr1-MD5 (`-m`), and classic DES crypt (`-d`) verify
correctly through OLS's own auth check -- bcrypt is used here since there
is no compatibility reason left to prefer a weaker format.

Users/passwords are NEVER recorded in Boron's own database (the goal's
explicit requirement) -- they live only in the protected directory's own
`.htpasswd` file, managed entirely through the real `htpasswd` CLI, with
every password passed via stdin (`-i`), never a command-line argument (this
project's hard "passwords never logged anywhere" rule --
daemon/procutil.py's run() logs every command's argument list in full).
"""
from __future__ import annotations

import os
import pwd
import re

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, FileAuthDir
from shared.validation import (
    ValidationError,
    validate_htpasswd_username,
    validate_password_strength,
    validate_protected_dir_relative_path,
    validate_username,
)

from daemon import filemanager, ols
from daemon.procutil import run

HTPASSWD_FILENAME = ".htpasswd"
HTPASSWD_BIN = "/usr/bin/htpasswd"


class FileAuthError(Exception):
    pass


def _realm_name(username: str, relative_path: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", relative_path).strip("_") or "root"
    return f"{username}_{slug}"[:80]


def _account_or_raise(session, username: str) -> Account:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise FileAuthError(f"account '{username}' not found")
    return account


def _resolve_protected_dir(username: str, relative_path: str) -> tuple[str, str, str]:
    """Reuses daemon/filemanager.py's own realpath-based jail check rather
    than reimplementing it -- that function is the actual, carefully-tested
    traversal-safety invariant (symlinked ancestors included), not a
    one-line transform worth duplicating."""
    relative_path = validate_protected_dir_relative_path(relative_path)
    resolved, home = filemanager._resolve(username, relative_path)
    if not os.path.isdir(resolved):
        raise FileAuthError(f"'{relative_path}' is not a directory")
    normalized_relative = os.path.relpath(resolved, home)
    return resolved, home, normalized_relative


def _domain_for_path(session, account_id: int, resolved_path: str) -> Domain:
    """A protected directory only has any real effect if it falls under one
    of the account's own domains' docroots -- that's the only tree OLS ever
    serves, so it's the only tree a vhost-level realm/context block could
    ever apply to. Reject up front rather than silently accepting a setting
    that would never take effect.

    The docroot itself is also rejected (not just directories with no
    domain at all): the vhost template already declares a `context / { }`
    block for the whole site (daemon/ols.py's render_vhost_conf); a second
    `context / { }` for the docroot root would collide with it. Protecting
    the entire site is not what this feature is for -- protect a
    subdirectory instead."""
    domains = session.scalars(select(Domain).where(Domain.account_id == account_id)).all()
    for domain in domains:
        docroot = os.path.realpath(domain.docroot)
        if resolved_path == docroot:
            raise FileAuthError(
                "the domain's own docroot cannot be protected directly (it would collide with the site's "
                "main context block) -- protect a subdirectory instead"
            )
        if resolved_path.startswith(docroot + os.sep):
            return domain
    raise FileAuthError("directory is not inside any of this account's domain docroots -- protecting it would have no effect")


def list_protected_dirs(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = _account_or_raise(session, username)
        rows = session.scalars(select(FileAuthDir).where(FileAuthDir.account_id == account.id)).all()
        return {"protected": [{"path": r.path} for r in rows]}


def enable_protection(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = _account_or_raise(session, username)
        resolved, _home, relative_path = _resolve_protected_dir(username, params.get("path", ""))
        _domain_for_path(session, account.id, resolved)

        existing = session.scalar(
            select(FileAuthDir).where(FileAuthDir.account_id == account.id, FileAuthDir.path == relative_path)
        )
        if existing is not None:
            raise ValidationError(f"'{relative_path}' is already protected")

        row = FileAuthDir(account_id=account.id, path=relative_path, realm_name=_realm_name(username, relative_path))
        session.add(row)
        session.flush()
        account_snapshot = account

    htpasswd_path = os.path.join(resolved, HTPASSWD_FILENAME)
    if not os.path.exists(htpasswd_path):
        pw = pwd.getpwnam(username)
        with open(htpasswd_path, "w"):
            pass
        os.chmod(htpasswd_path, 0o640)
        os.chown(htpasswd_path, pw.pw_uid, pw.pw_gid)
        # The docroot's own recursive default ACL (daemon/handlers_domain.py's
        # _grant_webserver_acl, applied at domain-add time) already grants
        # OLS's worker uid read access to anything created later inside the
        # docroot tree -- no separate ACL call needed here.

    ols.refresh_vhost(account_snapshot)
    return {"path": relative_path, "status": "protected"}


def disable_protection(params: dict) -> dict:
    """Removes the DB row (and therefore the vhost's realm/context block on
    next render) -- deliberately does NOT delete the .htpasswd file itself,
    same "don't destroy customer content on a routing/config change" stance
    daemon/handlers_redirect.py already takes. Re-enabling later reuses it."""
    username = validate_username(params["username"])
    with write_session() as session:
        account = _account_or_raise(session, username)
        relative_path = validate_protected_dir_relative_path(params.get("path", ""))
        row = session.scalar(
            select(FileAuthDir).where(FileAuthDir.account_id == account.id, FileAuthDir.path == relative_path)
        )
        if row is None:
            raise FileAuthError(f"'{relative_path}' is not currently protected")
        session.delete(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return {"path": relative_path, "status": "unprotected"}


def list_users(params: dict) -> dict:
    username = validate_username(params["username"])
    resolved, _home, _relative_path = _resolve_protected_dir(username, params.get("path", ""))
    htpasswd_path = os.path.join(resolved, HTPASSWD_FILENAME)
    if not os.path.isfile(htpasswd_path):
        return {"users": []}
    users = []
    with open(htpasswd_path) as f:
        for line in f:
            line = line.strip()
            if line and ":" in line and not line.startswith("#"):
                users.append(line.split(":", 1)[0])
    return {"users": users}


def add_user(params: dict) -> dict:
    username = validate_username(params["username"])
    resolved, _home, _relative_path = _resolve_protected_dir(username, params.get("path", ""))
    htpasswd_path = os.path.join(resolved, HTPASSWD_FILENAME)
    if not os.path.isfile(htpasswd_path):
        raise FileAuthError("directory protection is not enabled for this path -- enable it first")

    htuser = validate_htpasswd_username(params["htuser"])
    password = validate_password_strength(params["password"])

    result = run([HTPASSWD_BIN, "-i", "-B", htpasswd_path, htuser], input_text=f"{password}\n", timeout=15)
    if not result.ok:
        raise FileAuthError(f"htpasswd failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"htuser": htuser, "status": "set"}


def delete_user(params: dict) -> dict:
    username = validate_username(params["username"])
    resolved, _home, _relative_path = _resolve_protected_dir(username, params.get("path", ""))
    htpasswd_path = os.path.join(resolved, HTPASSWD_FILENAME)
    htuser = validate_htpasswd_username(params["htuser"])
    if not os.path.isfile(htpasswd_path):
        raise FileAuthError("directory protection is not enabled for this path")

    result = run([HTPASSWD_BIN, "-D", htpasswd_path, htuser], timeout=15)
    if not result.ok:
        raise FileAuthError(f"htpasswd failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"htuser": htuser, "status": "deleted"}


def terminate_account_fileauth(account: Account) -> None:
    """TERMINATE_HOOKS entry: FileAuthDir rows have no ON DELETE CASCADE
    (SQLite, no FK enforcement by default in this project's engine config)
    -- clean them up explicitly so a future account reusing the same
    username doesn't inherit stale protected-directory rows. The
    .htpasswd files themselves disappear along with the whole home dir via
    `userdel --remove`, same as every other account-owned file."""
    with write_session() as session:
        rows = session.scalars(select(FileAuthDir).where(FileAuthDir.account_id == account.id)).all()
        for row in rows:
            session.delete(row)
