"""Phase 8 feature 2: admin account editor.

Admin-only changes an operator can make without entering the customer panel:
  - account (Linux/FTP/SSH) password, contact email, primary domain
  - username RENAME -- atomic across Linux user + home dir + OLS/PHP + cgroups,
    with rollback on failure (the xhigh part)
  - any mailbox / database / FTP password (dispatched to the existing
    per-resource change-password ops via the admin `passwords` endpoint)

Every op routes through server.py's dispatch(), so all of these are
audit-logged with the acting admin as the actor.
"""
from __future__ import annotations

import logging
import os

from daemon import database_operations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import (
    Account,
    AccountNotificationPrefs,
    Domain,
    FtpAccount,
    GitRepo,
    NodeApp,
    PythonApp,
    PanelUser,
    Session,
    RedisInstance,
)
from shared.validation import (
    ValidationError,
    validate_domain,
    validate_email_address,
    validate_password_strength,
    validate_username,
)

from daemon import account_mutation, appunits, cgroups, ols, sysops

logger = logging.getLogger("borond.identity_admin")


def _account_or_raise(session, username: str) -> Account:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    return account


# --- account (Linux) password ---------------------------------------------


@account_mutation.locked
def set_account_password(params: dict) -> dict:
    """Reset the account's own Linux/system password (its FTP main login +
    SSH credential), via chpasswd -- never handled in Python (ARCHITECTURE.md
    SS5). If the account is suspended, its password hash is currently locked
    (`usermod -L`), and chpasswd would silently unlock it -- so re-lock after
    setting, keeping suspend semantics intact."""
    username = validate_username(params["username"])
    password = validate_password_strength(params["password"])
    with write_session() as session:
        account = _account_or_raise(session, username)
        status = account.status
        login = session.scalar(select(PanelUser).where(PanelUser.username == username))
        if login is not None and (login.role != "customer" or login.account_id != account.id):
            raise ValidationError("The panel login name is already used by another account")
    if status not in ("active", "suspended"):
        raise RuntimeError(f"cannot set password for an account in status '{status}'")
    sysops.set_initial_password(username, password)
    if status == "suspended":
        sysops.lock_user(username)
    # Legacy account creation provisioned only the Linux password. An
    # explicit account password reset repairs that missing panel identity.
    from daemon.handlers_account import ensure_customer_login
    with write_session() as session:
        account = _account_or_raise(session, username)
        ensure_customer_login(session, account, password)
    return {"username": username, "status": "password_changed"}


# --- contact email ---------------------------------------------------------


def set_contact_email(params: dict) -> dict:
    """The account's contact/notification email (AccountNotificationPrefs.
    customer_email) -- lazily creates the prefs row, same "no row = defaults"
    convention that table already uses. Empty clears it (no notifications
    are then sent, as documented on the model)."""
    username = validate_username(params["username"])
    raw = (params.get("contact_email") or "").strip()
    email = validate_email_address(raw) if raw else None
    with write_session() as session:
        account = _account_or_raise(session, username)
        prefs = session.scalar(
            select(AccountNotificationPrefs).where(AccountNotificationPrefs.account_id == account.id)
        )
        if prefs is None:
            prefs = AccountNotificationPrefs(account_id=account.id)
            session.add(prefs)
        prefs.customer_email = email
    return {"username": username, "contact_email": email}


# --- primary domain --------------------------------------------------------


@database_operations.serialized
@account_mutation.locked
def set_primary_domain(params: dict) -> dict:
    """Rename the account's primary domain (the apex served from
    public_html). If the account has no primary domain yet, this creates it.
    The docroot stays public_html (a primary domain's docroot is not
    domain-name-derived), so only the Domain.domain label + Account.
    primary_domain change; DNS for an apex domain is operator-managed and is
    deliberately not touched here."""
    username = validate_username(params["username"])
    new_domain = validate_domain(params["domain"])

    with write_session() as session:
        account = _account_or_raise(session, username)
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot change primary domain for an account in status '{account.status}'")
        from daemon import domain_ownership
        domain_ownership.require_available(session, new_domain, account.id)
        clash = session.scalar(select(Domain).where(Domain.domain == new_domain))
        if clash is not None and clash.account_id != account.id:
            raise RuntimeError(f"domain '{new_domain}' is already in use")
        primary = session.scalar(
            select(Domain).where(Domain.account_id == account.id, Domain.kind == "primary")
        )
        old_domain = primary.domain if primary is not None else None
        previous_primary = account.primary_domain
        account_id = account.id
        previous_rows = {row.id: (row.domain, row.kind, row.docroot) for row in (primary, clash) if row is not None}
        promoting = clash is not None
        created_id = None
        docroot = f"{settings.home_base}/{username}/public_html"
        if promoting:
            # Promotion is a metadata change, not a request to move files.
            # Preserve both websites' document roots and the former primary.
            if clash.kind == "subdomain":
                raise ValidationError("Choose an existing main domain, not a subdomain, as the primary domain")
            if primary is not None and primary.id != clash.id:
                primary.kind = "addon"
            clash.kind = "primary"
        elif primary is None:
            from daemon import handlers_domain

            handlers_domain.ensure_docroot(username, docroot)
            primary = Domain(account_id=account.id, domain=new_domain, kind="primary", docroot=docroot)
            session.add(primary)
            session.flush()
            created_id = primary.id
        else:
            primary.domain = new_domain
        account.primary_domain = new_domain
        account_snapshot = account

    try:
        ols.refresh_vhost(account_snapshot)
    except Exception:
        with write_session() as session:
            session.get(Account, account_id).primary_domain = previous_primary
            if created_id is not None:
                session.delete(session.get(Domain, created_id))
            for row_id, (domain, kind, root) in previous_rows.items():
                row = session.get(Domain, row_id)
                row.domain, row.kind, row.docroot = domain, kind, root
        raise
    # Remove the old primary domain's now-stale vhost directory (keyed by the
    # old domain name; refresh_vhost already regenerated httpd_config without
    # it, but the per-domain vhconf dir must be swept too).
    if old_domain and old_domain != new_domain and not promoting:
        _remove_vhost_dir(old_domain)
    return {"username": username, "primary_domain": new_domain, "previous": old_domain}


def _remove_vhost_dir(domain_name: str) -> None:
    import shutil
    from pathlib import Path

    vhost_dir = Path(ols.OLS_SERVER_BASE) / "conf" / "vhosts" / ols._vhost_name(domain_name)
    shutil.rmtree(vhost_dir, ignore_errors=True)


# --- username rename (xhigh: atomic, rollback on failure) ------------------


@database_operations.serialized
@account_mutation.locked
def rename_account(params: dict) -> dict:
    """Atomically rename an account: Linux login + group + home dir + OLS
    vhosts + PHP extProcessor + cgroup slice + the panel's own DB rows
    (Account.username and every Domain.docroot's /home/<old> prefix). uid/gid
    are preserved by `usermod -l`, so quotas and namespace isolation (both
    keyed by uid) require no change. Each step registers a compensation; on
    any failure every completed step is reversed and OLS is reconciled to the
    restored state, so the account is never left half-renamed.

    Refuses when the account has NodeJS/Python apps or FTP sub-accounts,
    because those embed the absolute home path in systemd EnvironmentFiles /
    Pure-FTPd's PureDB, which a v1 rename does not rewrite -- failing before
    any change is made, not leaving a broken app behind."""
    old = validate_username(params["username"])
    new = validate_username(params["new_username"])
    if old == new:
        raise ValidationError("new username is the same as the current one")

    old_home = f"{settings.home_base}/{old}"
    new_home = f"{settings.home_base}/{new}"

    with write_session() as session:
        account = _account_or_raise(session, old)
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot rename an account in status '{account.status}'")
        clash = session.scalar(select(Account).where(Account.username == new))
        login_clash = session.scalar(select(PanelUser).where(PanelUser.username == new))
        if clash is not None or login_clash is not None:
            raise ValidationError(f"account '{new}' already exists")
        node = session.scalar(select(NodeApp).where(NodeApp.account_id == account.id))
        py = session.scalar(select(PythonApp).where(PythonApp.account_id == account.id))
        ftp = session.scalar(select(FtpAccount).where(FtpAccount.account_id == account.id))
        if node is not None or py is not None:
            raise ValidationError(
                "cannot rename an account that hosts NodeJS/Python apps -- their systemd units "
                "reference the absolute home path; remove the apps first"
            )
        if ftp is not None:
            raise ValidationError(
                "cannot rename an account with FTP sub-accounts -- their chroot paths are stored "
                "in Pure-FTPd's PureDB; remove the FTP sub-accounts first"
            )
        redis = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account.id))
        if redis is not None:
            if redis.enabled:
                raise ValidationError('Disable the account Redis instance before renaming. Applications must use the new username-dependent socket path after renaming.')
            unit = appunits.unit_name('redis', old, redis.id)
            if appunits.status(unit)['active'] not in ('inactive', 'failed', 'unknown'):
                raise ValidationError('Redis is still running or stopping. Wait until it is inactive before renaming.')
        limits = (account.cpu_pct, account.mem_mb, account.io_mb, account.pids_max)
        status = account.status
        account_id = account.id

    if sysops.user_exists(new):
        raise ValidationError(f"a Linux user named '{new}' already exists outside this panel")

    undo: list = []
    resume = _pause_for_rename(account_id)
    try:
        sysops.rename_login(old, new)
        undo.append(lambda: sysops.rename_login(new, old))

        sysops.rename_group(old, new)
        undo.append(lambda: sysops.rename_group(new, old))

        sysops.move_home(new, new_home)
        undo.append(lambda: sysops.move_home(new, old_home))

        _rewrite_db_username(old, new, old_home, new_home)
        undo.append(lambda: _rewrite_db_username(new, old, new_home, old_home))

        # The native slice is keyed by UID, which rename preserves. Removing
        # it would stop the account and erase its aggregate limits.
        cgroups.apply_limits(new, *limits)
        _refresh_git_paths(account_id, new)

        # OLS is the last mutating step and is intentionally NOT on the undo
        # stack: ConfigWriterMulti self-rolls-back its own config files on a
        # failed apply, so on any failure we just reconcile OLS to the
        # restored (old) state once, after the other compensations run.
        with write_session() as session:
            account = _account_or_raise(session, new)
            snapshot = account
        ols.refresh_vhost(snapshot)
        # Retire the old account's now-stale LSAPI workers (uid unchanged, so
        # they'd otherwise keep serving on the old socket path).
        try:
            sysops.recycle_php_workers(new)
        except Exception:
            logger.warning("failed to recycle php workers after rename %s -> %s", old, new)
    except Exception as exc:
        logger.exception("rename %s -> %s failed; rolling back", old, new)
        for comp in reversed(undo):
            try:
                comp()
            except Exception:
                logger.exception("rollback step failed during rename %s -> %s", old, new)
        # Reconcile OLS with whatever state we restored to.
        try:
            with write_session() as session:
                account = session.scalar(select(Account).where(Account.username == old))
                if account is not None:
                    restored = account
                else:
                    restored = None
            if restored is not None:
                ols.refresh_vhost(restored)
        except Exception:
            logger.exception("OLS reconcile after failed rename %s -> %s also failed", old, new)
        raise RuntimeError("Rename failed; completed changes were rolled back") from exc
    finally:
        resume()

    return {"old_username": old, "new_username": new, "status": status}


def _pause_for_rename(account_id: int):
    """Close account sessions and pause managed workloads before usermod.

    This never stops OLS globally. The original website/shell/Redis state is
    restored under the final (or rolled-back) name, including on failure.
    """
    from daemon import appunits, cron, filebrowser_accounts, redisacct, nsisolation
    with write_session() as session:
        account = session.get(Account, account_id)
        old, status = account.username, account.status
        redis = session.scalar(select(RedisInstance).where(RedisInstance.account_id == account_id))
        redis_state = (redis.id, redis.mem_mb, redis.enabled) if redis else None
    shell = sysops.get_shell(old)
    if shell not in (sysops.LOGIN_SHELL, sysops.NOLOGIN_SHELL):
        raise ValidationError("Set a supported account shell before renaming this account")
    crontab = cron._read_raw(old)

    def resume():
        with write_session() as session:
            current = session.get(Account, account_id)
            current.status = status
            name = current.username
        sysops.set_shell(name, shell)
        _refresh_git_paths(account_id, name)
        old_home = f"{settings.home_base}/{old}"
        new_home = f"{settings.home_base}/{name}"
        cron._write_raw(name, [line.replace(old_home + "/", new_home + "/") for line in crontab])
        if redis_state:
            instance_id, memory, enabled = redis_state
            redisacct._provision_filesystem(name)
            unit = redisacct._write_unit(name, instance_id, memory)
            if enabled and status == "active":
                appunits.enable_start(unit)
            if name != old:
                old_unit = appunits.unit_name("redis", old, instance_id)
                appunits.remove_unit(old_unit)
                redisacct._conf_path(old_unit).unlink(missing_ok=True)
        ols.refresh_vhost(current)

    try:
        # Pause scheduled launches before waiting for UID processes to exit.
        cron.delete_all_jobs(old)
        sysops.set_shell(old, sysops.NOLOGIN_SHELL)
        with write_session() as session:
            paused = session.get(Account, account_id)
            paused.status = "suspended"
        ols.refresh_vhost(paused)
        filebrowser_accounts.stop(old)
        if redis_state:
            appunits.stop(appunits.unit_name("redis", old, redis_state[0]))
        sysops.quiesce_user(old)
        # Cached mount namespaces can still refer to /home/<old>. All account
        # workloads have stopped, so retire that cache before moving the home.
        namespace = nsisolation.get_status(old)
        if namespace.get("enabled"):
            nsisolation.unmount_uid(account.uid)
    except Exception:
        resume()
        raise
    return resume


def _refresh_git_paths(account_id, username):
    from daemon import gitrepo
    with write_session() as db:
        repos = db.scalars(select(GitRepo).where(GitRepo.account_id == account_id,
                                                GitRepo.deploy_target.is_not(None))).all()
    for repo in repos:
        gitrepo.set_deploy_target({'username': username, 'name': repo.name,
                                  'deploy_target': repo.deploy_target})


def _readd_old_slice(username: str, limits: tuple) -> None:
    cgroups.apply_limits(username, *limits)


def _rewrite_db_username(old: str, new: str, old_home: str, new_home: str) -> None:
    """Update the panel's own control-plane rows that key on the username:
    Account.username and every Domain.docroot's /home/<old> path prefix.
    Deliberately does NOT touch DatabaseGrant/WordPressInstall db_name/db_user
    (they mirror live MariaDB objects this rename does not rename) -- see the
    checkpoint for that documented v1 limitation."""
    old_prefix = old_home.rstrip("/") + "/"
    new_prefix = new_home.rstrip("/") + "/"
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == old))
        if account is None:
            raise RuntimeError(f"account '{old}' vanished mid-rename")
        account.username = new
        login = session.scalar(select(PanelUser).where(PanelUser.username == old,
                                                     PanelUser.account_id == account.id))
        if login is not None:
            login.username = new
        for row in session.scalars(select(Session).join(PanelUser, Session.panel_user_id == PanelUser.id)
                                   .where(PanelUser.account_id == account.id)).all():
            row.revoked = True
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        for d in domains:
            if d.docroot == old_home:
                d.docroot = new_home
            elif d.docroot.startswith(old_prefix):
                d.docroot = new_prefix + d.docroot[len(old_prefix):]
