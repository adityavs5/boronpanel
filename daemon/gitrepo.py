"""Per-account bare git repos with push-to-deploy (Phase 4 feature 5).

Classic push-to-deploy pattern: a `post-receive` hook inside the bare repo
runs `git --work-tree=<deploy_target> --git-dir=<repo> checkout -f` after
every push, checking the pushed tree out directly into the configured
deploy target (no separate `git pull` step on the deploy side, and no
working copy inside the bare repo itself). The hook always runs as
whichever user performed the push -- for this project that's the account's
own Linux user (git itself invokes hooks as the pushing user, no separate
privilege involved), matching every other per-account operation's identity
model (ARCHITECTURE.md SS6's suEXEC-equivalent isolation).

WP-CLI is deliberately not installed server-wide (Phase 3 feature 2's own
finding: this environment's permission classifier denied downloading and
executing it from an agent-chosen source, and the WordPress installer was
redesigned around that rather than worked around). The post-receive hook
therefore only ever calls `wp cache flush` best-effort, gated on `wp`
actually being on PATH (`command -v wp`) -- if an operator installs WP-CLI
manually later (documented as possible in earlier phases), deploys pick it
up automatically with no Boron-side change needed; if not, the hook
silently skips that one step rather than failing the whole deploy.
"""
from __future__ import annotations

import os
import pwd
import stat

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, GitRepo
from shared.validation import ValidationError, validate_git_repo_name, validate_protected_dir_relative_path, validate_username

from daemon import filemanager, safeio
from daemon.procutil import run

REPOS_SUBDIR = "repos"
PUSH_LOG_FILENAME = "push-log.txt"
MAX_PUSH_LOG_LINES = 200

POST_RECEIVE_TEMPLATE = """#!/bin/bash
# Managed by Boron (daemon/gitrepo.py) -- do not hand-edit, this file is
# regenerated whenever the deploy target changes.
set -e
DEPLOY_TARGET="{deploy_target}"
GIT_DIR_ABS="{repo_path}"
PUSH_LOG="{push_log_path}"

while read oldrev newrev ref; do
  branch="${{ref#refs/heads/}}"
  if [ "$branch" != "main" ] && [ "$branch" != "master" ]; then
    echo "$(date -Iseconds) skipping ref=$ref (not main/master)" >> "$PUSH_LOG"
    continue
  fi
  mkdir -p "$DEPLOY_TARGET"
  git --work-tree="$DEPLOY_TARGET" --git-dir="$GIT_DIR_ABS" checkout -f "$branch"
  echo "$(date -Iseconds) ref=$ref commit=$newrev -> $DEPLOY_TARGET" >> "$PUSH_LOG"

  if [ -f "$DEPLOY_TARGET/wp-config.php" ] && command -v wp >/dev/null 2>&1; then
    wp --path="$DEPLOY_TARGET" --allow-root cache flush >> "$PUSH_LOG" 2>&1 || true
  fi
done
"""


class GitRepoError(Exception):
    pass


def _account_or_raise(session, username: str) -> Account:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise GitRepoError(f"account '{username}' not found")
    return account


def _account_home(username: str) -> str:
    from shared.config import settings

    return os.path.realpath(f"{settings.home_base}/{username}")


def _repo_path(username: str, name: str) -> str:
    return os.path.join(_account_home(username), REPOS_SUBDIR, f"{name}.git")


def _repo_to_dict(row: GitRepo) -> dict:
    return {"name": row.name, "deploy_target": row.deploy_target, "created_at": row.created_at.isoformat() if row.created_at else None}


def list_repos(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = _account_or_raise(session, username)
        rows = session.scalars(select(GitRepo).where(GitRepo.account_id == account.id)).all()
        return {"repos": [_repo_to_dict(r) for r in rows]}


def create_repo(params: dict) -> dict:
    username = validate_username(params["username"])
    name = validate_git_repo_name(params["name"])

    with write_session() as session:
        account = _account_or_raise(session, username)
        existing = session.scalar(select(GitRepo).where(GitRepo.account_id == account.id, GitRepo.name == name))
        if existing is not None:
            raise ValidationError(f"repo '{name}' already exists for account '{username}'")

        repo_path = _repo_path(username, name)
        result = run(["runuser", "-u", username, "--", "git", "init", "--bare", repo_path], timeout=30)
        if not result.ok:
            raise GitRepoError(f"git init --bare failed: {result.stderr.strip() or result.stdout.strip()}")

        row = GitRepo(account_id=account.id, name=name, deploy_target=None)
        session.add(row)
        session.flush()
        return _repo_to_dict(row)


def _resolve_deploy_target(username: str, relative_path: str) -> tuple[str, str]:
    relative_path = validate_protected_dir_relative_path(relative_path)
    resolved, home = filemanager._resolve(username, relative_path)
    return resolved, os.path.relpath(resolved, home)


def set_deploy_target(params: dict) -> dict:
    username = validate_username(params["username"])
    name = validate_git_repo_name(params["name"])
    resolved_target, normalized_relative = _resolve_deploy_target(username, params["deploy_target"])

    with write_session() as session:
        account = _account_or_raise(session, username)
        row = session.scalar(select(GitRepo).where(GitRepo.account_id == account.id, GitRepo.name == name))
        if row is None:
            raise GitRepoError(f"repo '{name}' not found for account '{username}'")

    pw = pwd.getpwnam(username)
    # Symlink-safe create+chown. _resolve() jail-checked the target, but that
    # was a check-then-act against a live path the account can still race a
    # symlink into before this makedirs/chown; secure_mkdirs re-derives it
    # under the account's home through O_NOFOLLOW fds (see daemon/safeio.py).
    home = filemanager._account_home(username)
    safeio.secure_mkdirs(home, normalized_relative, pw.pw_uid, pw.pw_gid, 0o750)

    repo_path = _repo_path(username, name)
    push_log_path = os.path.join(repo_path, PUSH_LOG_FILENAME)
    hook_content = POST_RECEIVE_TEMPLATE.format(deploy_target=resolved_target, repo_path=repo_path, push_log_path=push_log_path)

    # Validate before installing -- a plain bash syntax check (`bash -n`),
    # the same "validate before apply" discipline every other config-writing
    # feature in this project follows (ARCHITECTURE.md SS7), scaled to what
    # actually applies here: there's no service to reload, just a script git
    # invokes on the next push, so a syntax error would otherwise only
    # surface at that point, silently breaking every future deploy.
    check = run(["bash", "-n"], input_text=hook_content, timeout=10)
    if not check.ok:
        raise GitRepoError(f"generated post-receive hook failed syntax check: {check.stderr.strip()}")
    safeio.secure_write_file_beneath(
        home, f"{REPOS_SUBDIR}/{name}.git/hooks/post-receive",
        hook_content.encode(), pw.pw_uid, pw.pw_gid, 0o750,
    )
    with write_session() as session:
        row = session.scalar(select(GitRepo).where(GitRepo.account_id == account.id, GitRepo.name == name))
        if row is None:
            raise GitRepoError("repository was removed while updating deployment")
        row.deploy_target = normalized_relative
        session.flush()
        return _repo_to_dict(row)


def get_push_log(params: dict) -> dict:
    username = validate_username(params["username"])
    name = validate_git_repo_name(params["name"])
    with write_session() as session:
        account = _account_or_raise(session, username)
        row = session.scalar(select(GitRepo).where(GitRepo.account_id == account.id, GitRepo.name == name))
        if row is None:
            raise GitRepoError(f"repo '{name}' not found for account '{username}'")

    directory = safeio.open_dir_beneath(_account_home(username), f"{REPOS_SUBDIR}/{name}.git")
    try:
        try:
            fd = os.open(PUSH_LOG_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError:
            return {"name": name, "lines": []}
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise GitRepoError("push log must be a regular file")
            limit = 256 * 1024
            start = max(0, info.st_size - limit)
            os.lseek(fd, start, os.SEEK_SET)
            text = os.read(fd, limit).decode('utf-8', errors='replace')
            lines = text.splitlines()
            if start and lines:
                lines.pop(0)  # Discard the potentially partial first line.
            return {"name": name, "lines": lines[-MAX_PUSH_LOG_LINES:]}
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def delete_repo(params: dict) -> dict:
    username = validate_username(params["username"])
    name = validate_git_repo_name(params["name"])

    with write_session() as session:
        account = _account_or_raise(session, username)
        row = session.scalar(select(GitRepo).where(GitRepo.account_id == account.id, GitRepo.name == name))
        if row is None:
            raise GitRepoError(f"repo '{name}' not found for account '{username}'")
        import shutil
        directory = safeio.open_dir_beneath(_account_home(username), REPOS_SUBDIR)
        try:
            try:
                shutil.rmtree(f"{name}.git", dir_fd=directory)
            except FileNotFoundError:
                pass
        finally:
            os.close(directory)
        session.delete(row)
    return {"name": name, "status": "deleted"}


def terminate_account_git(account: Account) -> None:
    """TERMINATE_HOOKS entry: GitRepo rows have no ON DELETE CASCADE
    (SQLite, no FK enforcement in this project's engine config) -- clean
    them up explicitly. The bare repos themselves disappear along with the
    whole home dir via `userdel --remove`, same as every other
    account-owned file."""
    with write_session() as session:
        rows = session.scalars(select(GitRepo).where(GitRepo.account_id == account.id)).all()
        for row in rows:
            session.delete(row)
