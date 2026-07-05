"""forgehostd: the root-privileged provisioning daemon.

Listens only on a Unix domain socket (ARCHITECTURE.md SS2). Every accepted
connection is dispatched to one of OP_TABLE's handlers in a worker thread
(handlers do blocking subprocess/filesystem/SQLite work), and every call is
written to the audit log regardless of success or failure.
"""
from __future__ import annotations

import asyncio
import grp
import logging
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shared.config import settings
from shared.db import init_db
from shared.rpc import encode_response, read_frame
from shared.validation import ValidationError

from daemon import appinstaller, audit, backup, cgroups, disktree, fail2ban, fileauth, filemanager, firewall, gitrepo, handlers_account, handlers_auth, handlers_cron, handlers_database, handlers_dns, handlers_domain, handlers_ftp, handlers_hotlink, handlers_ipblock, handlers_mail, handlers_php_ini, handlers_redirect, handlers_usage, health, ipwhitelist, logs, mailqueue, nameservers, nsisolation, ols, pma, servicemgr, slowquery, spamfilter, sshkeys, ssl, totp, waf, wordpress
from daemon.logsetup import configure_logging

logger = logging.getLogger("forgehostd")

OP_TABLE = {
    "account.create": handlers_account.create_account,
    "account.get": handlers_account.get_account,
    "account.list": handlers_account.list_accounts,
    "account.suspend": handlers_account.suspend_account,
    "account.unsuspend": handlers_account.unsuspend_account,
    "account.terminate": handlers_account.terminate_account,
    "account.set_php_version": handlers_account.set_php_version,
    "account.set_limits": handlers_account.set_limits,
    "account.reactivate": handlers_account.reactivate_account,
    "cron.list": handlers_cron.list_cron_jobs,
    "cron.add": handlers_cron.add_cron_job,
    "cron.update": handlers_cron.update_cron_job,
    "cron.delete": handlers_cron.delete_cron_job,
    "cron.mailto.get": handlers_cron.get_cron_mailto,
    "cron.mailto.set": handlers_cron.set_cron_mailto,
    "domain.add": handlers_domain.add_domain,
    "domain.remove": handlers_domain.remove_domain,
    "domain.list": handlers_domain.list_domains,
    "usage.get": handlers_usage.get_account_usage,
    "namespace.enable": nsisolation.enable_namespace,
    "namespace.disable": nsisolation.disable_namespace,
    "namespace.status": nsisolation.namespace_status,
    "namespace.bulk_enable.trigger": nsisolation.trigger_bulk_enable,
    "namespace.bulk_enable.get": nsisolation.get_bulk_enable_job,
    "namespace.health_summary": nsisolation.health_summary,
    "system.bootstrap_ols": lambda params: (ols.bootstrap_baseline(), {"status": "ok"})[1],
    "system.bootstrap_webmail": lambda params: (ols.bootstrap_webmail(), {"status": "ok"})[1],
    # Security fix (Phase 6a research finding): one-time migration backfilling
    # pre-existing accounts with the per-account-/tmp + allowSymbolLink-0
    # template fix -- same "system.*", not-wired-to-any-UI-button category as
    # the bootstrap_* ops above.
    "system.refresh_all_vhosts": lambda params: (ols.refresh_all_vhosts(), {"status": "ok"})[1],
    # Phase 6b: template-only fix (namespace/namespaceConf directives added to
    # httpd_config.conf.j2) needs the shared main config re-rendered from the
    # current DB state, same one-off "system.*" category as the ops above --
    # refresh_main_config() touches only httpd_config.conf, not per-account
    # vhost.conf files, since no per-account template content changed.
    "system.refresh_main_config": lambda params: (ols.refresh_main_config(), {"status": "ok"})[1],
    "dns.create_zone": handlers_dns.create_zone,
    "dns.delete_zone": handlers_dns.delete_zone,
    "dns.list_records": handlers_dns.list_records,
    "dns.set_record": handlers_dns.set_record,
    "dns.delete_record": handlers_dns.delete_record,
    "nameservers.list": nameservers.list_nameservers,
    "nameservers.set": nameservers.set_nameservers,
    "nameservers.reset": nameservers.reset_nameservers,
    "db.create": handlers_database.create_database,
    "db.list": handlers_database.list_databases,
    "db.drop": handlers_database.drop_database,
    "db.change_password": handlers_database.change_password,
    "mail.create_domain": handlers_mail.create_mail_domain,
    "mail.delete_domain": handlers_mail.delete_mail_domain,
    "mail.create_mailbox": handlers_mail.create_mailbox,
    "mail.delete_mailbox": handlers_mail.delete_mailbox,
    "mail.list_mailboxes": handlers_mail.list_mailboxes,
    "mail.change_password": handlers_mail.change_mailbox_password,
    # Phase 3 feature 4: forwarders/catch-all/autoresponders
    "mail.forward.create": handlers_mail.create_forward,
    "mail.forward.delete": handlers_mail.delete_forward,
    "mail.forward.list": handlers_mail.list_forwards,
    "mail.catchall.set": handlers_mail.set_catchall,
    "mail.catchall.get": handlers_mail.get_catchall,
    "mail.catchall.delete": handlers_mail.delete_catchall,
    "mail.autoresponder.set": handlers_mail.set_autoresponder,
    "mail.autoresponder.get": handlers_mail.get_autoresponder,
    "mail.autoresponder.delete": handlers_mail.delete_autoresponder,
    # Phase 4 feature 1: SpamAssassin
    "mail.spamfilter.get": handlers_mail.get_spam_filter,
    "mail.spamfilter.set": handlers_mail.set_spam_filter,
    "spamfilter.global_default.get": lambda params: {"default_threshold": spamfilter.get_global_default_threshold()},
    "spamfilter.global_default.set": lambda params: spamfilter.set_global_default_threshold(params["threshold"]),
    "ssl.issue": ssl.issue_certificate,
    "ssl.status": ssl.certificate_status,
    "ssl.dashboard": ssl.get_ssl_dashboard,
    "file.list": filemanager.list_dir,
    "file.read": filemanager.read_file,
    "file.write": filemanager.write_file,
    "file.mkdir": filemanager.mkdir,
    "file.delete": filemanager.delete,
    "file.move": filemanager.move,
    "panel_user.create": handlers_auth.create_panel_user,
    "panel_user.set_password": handlers_auth.set_panel_user_password,
    "auth.create_session": handlers_auth.create_session,
    "auth.revoke_session": handlers_auth.revoke_session,
    # Security audit finding F2: login brute-force throttling
    "auth.check_login_lockout": handlers_auth.check_login_lockout,
    "auth.record_login_result": handlers_auth.record_login_result,
    "auth.create_api_token": handlers_auth.create_api_token,
    "auth.revoke_api_token": handlers_auth.revoke_api_token,
    # Phase 2 feature 7: backup.py's functions already validate their own
    # params and return handler-shaped dicts (like every other handlers_*.py
    # module) -- it's registered directly rather than through a redundant
    # pass-through handlers_backup.py, since it's already the "handler" as
    # well as the engine.
    "backup.destination.create": backup.create_destination,
    "backup.destination.list": backup.list_destinations,
    "backup.destination.delete": backup.delete_destination,
    "backup.schedule.set": backup.set_schedule,
    "backup.schedule.list": backup.list_schedules,
    "backup.job.trigger": backup.trigger_backup,
    "backup.job.get": backup.get_job,
    "backup.job.list": backup.list_jobs,
    "backup.job.browse": backup.browse_backup,
    "backup.restore.trigger": backup.trigger_restore,
    "backup.restore.get": backup.get_restore_job,
    "backup.restore.list": backup.list_restore_jobs,
    # Phase 3 feature 2: one-click WordPress installer
    "wordpress.install.trigger": wordpress.trigger_install,
    "wordpress.install.get": wordpress.get_job,
    "wordpress.install.list": wordpress.list_installs,
    # Phase 3 feature 3: phpMyAdmin single-signon
    "pma.token.create": pma.create_token,
    "system.bootstrap_pma": lambda params: (pma.bootstrap_pma(), {"status": "ok"})[1],
    "system.bootstrap_spamassassin": lambda params: spamfilter.bootstrap_spamassassin(),
    # Phase 3 feature 5: FTP sub-accounts
    "ftp.create": handlers_ftp.create_ftp_account,
    "ftp.list": handlers_ftp.list_ftp_accounts,
    "ftp.set_path": handlers_ftp.set_ftp_path,
    "ftp.change_password": handlers_ftp.change_ftp_password,
    "ftp.delete": handlers_ftp.delete_ftp_account,
    # Phase 3 feature 6: per-account PHP ini overrides
    "php_ini.get": handlers_php_ini.get_php_ini,
    "php_ini.set": handlers_php_ini.set_php_ini,
    "php_ini.reset": handlers_php_ini.reset_php_ini,
    # Phase 3 feature 7: per-domain redirects
    "redirect.create": handlers_redirect.create_redirect,
    "redirect.update": handlers_redirect.update_redirect,
    "redirect.delete": handlers_redirect.delete_redirect,
    "redirect.list": handlers_redirect.list_redirects,
    # Phase 4 feature 2: hotlink protection
    "hotlink.get": handlers_hotlink.get_hotlink_protection,
    "hotlink.set": handlers_hotlink.set_hotlink_protection,
    # Phase 4 feature 3: IP blocker
    "ipblock.list": handlers_ipblock.list_ip_blocks,
    "ipblock.add": handlers_ipblock.add_ip_block,
    "ipblock.remove": handlers_ipblock.remove_ip_block,
    # Phase 4 feature 4: directory privacy
    "fileauth.list": fileauth.list_protected_dirs,
    "fileauth.enable": fileauth.enable_protection,
    "fileauth.disable": fileauth.disable_protection,
    "fileauth.user.list": fileauth.list_users,
    "fileauth.user.add": fileauth.add_user,
    "fileauth.user.delete": fileauth.delete_user,
    # Phase 4 feature 5: git version control / push-to-deploy
    "git.repo.list": gitrepo.list_repos,
    "git.repo.create": gitrepo.create_repo,
    "git.repo.delete": gitrepo.delete_repo,
    "git.repo.set_deploy_target": gitrepo.set_deploy_target,
    "git.repo.push_log": gitrepo.get_push_log,
    # Phase 4 feature 6: SSH key management
    "sshkeys.list": sshkeys.list_keys,
    "sshkeys.add": sshkeys.add_key,
    "sshkeys.delete": sshkeys.delete_key,
    # Phase 4 feature 7: disk usage treemap
    "disktree.get": disktree.get_disk_tree,
    "disktree.top_files": disktree.get_top_files,
    # Phase 4 feature 8: app installer (Softaculous-equivalent)
    "apps.install.trigger": appinstaller.trigger_install,
    "apps.install.get": appinstaller.get_job,
    "apps.list": appinstaller.list_installed_apps,
    # Phase 3 feature 9: error log viewer
    "logs.get": logs.get_log,
    # Phase 5 feature 1: server health dashboard
    "health.get": health.get_live,
    "health.history": health.get_history,
    "health.snapshot": health.take_snapshot,
    # Phase 5 feature 2: service manager
    "services.list": servicemgr.list_services,
    "services.status": servicemgr.get_service,
    "services.control": servicemgr.control_service,
    # Phase 5 feature 3: mail queue viewer
    "mailqueue.list": mailqueue.list_queue,
    "mailqueue.flush": mailqueue.flush_message,
    "mailqueue.flush_all": mailqueue.flush_all,
    "mailqueue.delete": mailqueue.delete_message,
    "mailqueue.delete_all": mailqueue.delete_all,
    # Phase 5 feature 4: firewall UI (UFW)
    "firewall.list": firewall.list_rules,
    "firewall.add": firewall.add_rule,
    "firewall.delete": firewall.delete_rule,
    "firewall.status": firewall.get_status,
    "firewall.enable": firewall.enable_firewall,
    "firewall.disable": firewall.disable_firewall,
    # Phase 5 feature 5: fail2ban
    "fail2ban.bootstrap": fail2ban.bootstrap_jails,
    "fail2ban.list_jails": fail2ban.list_jails,
    "fail2ban.get_jail": fail2ban.get_jail,
    "fail2ban.unban_ip": fail2ban.unban_ip,
    "fail2ban.unban_all": fail2ban.unban_all_in_jail,
    "fail2ban.recent_events": fail2ban.recent_events,
    # Phase 5 feature 7: ModSecurity/WAF
    "waf.status": waf.get_status,
    "waf.set_enabled": waf.set_enabled,
    "waf.set_domain_override": waf.set_domain_override,
    "waf.add_custom_rule": waf.add_custom_rule,
    "waf.delete_custom_rule": waf.delete_custom_rule,
    "waf.blocked_requests": waf.list_blocked_requests,
    # Phase 5 feature 8: MySQL slow query viewer
    "slowquery.status": slowquery.get_status,
    "slowquery.bootstrap": slowquery.bootstrap_slow_query_log,
    "slowquery.list": slowquery.list_slow_queries,
    # Phase 5 feature 9: IP whitelist for panel login
    "ipwhitelist.list": ipwhitelist.list_entries,
    "ipwhitelist.add": ipwhitelist.add_entry,
    "ipwhitelist.delete": ipwhitelist.delete_entry,
    # Phase 5 feature 10: TOTP two-factor authentication
    "totp.status": totp.get_status,
    "totp.setup": totp.setup_totp,
    "totp.verify": totp.verify_totp,
    "totp.disable": totp.disable_totp,
    "totp.check_login_code": totp.check_login_code,
}

# Security audit finding F7: disktree.get/top_files and usage.get run real
# `du`/`find` subprocess calls with 30-120s timeouts, but are ordinary
# self-service, no-cooldown RPCs -- dispatch() otherwise runs every op
# through the asyncio loop's single shared default executor, so enough
# concurrent requests from one account (no elevated capability needed to
# trigger this) saturate that shared pool and starve every *other*
# account's unrelated calls (login, DNS edits, anything) behind them, a
# cross-tenant DoS. backup/wordpress/appinstaller jobs already use their
# own dedicated bounded executors for exactly this reason; this gives the
# disk/usage-reporting path the same treatment -- a small, separate pool
# so a burst of usage polling can never starve the rest of the daemon.
REPORTING_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="reporting")
REPORTING_OPS = {
    "disktree.get", "disktree.top_files", "usage.get",
    # Phase 5: admin-only polling/dashboard ops that shell out or sample
    # live system state -- same isolation reasoning as disktree/usage
    # above, just for the admin surface instead of the customer one.
    "health.get", "health.history",
    "services.status", "services.list",
    "mailqueue.list",
    "firewall.list",
    "fail2ban.list_jails", "fail2ban.get_jail", "fail2ban.recent_events",
    "waf.status", "waf.blocked_requests",
    "slowquery.list", "slowquery.status",
}

# Each phase wires its own account-scoped teardown/suspend behavior here
# instead of handlers_account.py importing every phase directly (avoids an
# import cycle: ols/dns/db/mail modules all need handlers_account's Account
# type, not the other way around).
handlers_account.SUSPEND_HOOKS.append(lambda account: ols.suspend_vhost(account))
handlers_account.UNSUSPEND_HOOKS.append(lambda account: ols.unsuspend_vhost(account))
handlers_account.PHP_VERSION_HOOKS.append(lambda account: ols.refresh_vhost(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: ols.terminate_vhost(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_dns.terminate_account_zones(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_database.terminate_account_databases(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_mail.terminate_account_mail(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: ssl.terminate_account_certs(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_cron.terminate_account_cron(account))
handlers_account.CREATE_HOOKS.append(
    lambda account: cgroups.apply_limits(account.username, account.cpu_pct, account.mem_mb, account.io_mb, account.pids_max)
)
handlers_account.LIMITS_HOOKS.append(
    lambda account: cgroups.apply_limits(account.username, account.cpu_pct, account.mem_mb, account.io_mb, account.pids_max)
)
handlers_account.TERMINATE_HOOKS.append(lambda account: cgroups.remove_slice(account.username))
# Phase 6b: new accounts (and reactivated ones, same CREATE_HOOKS list) get
# namespace isolation by default; termination tears down the per-uid
# lsnsctl gate + any persisted /var/lsns/<uid> state. No suspend/unsuspend
# hook -- docs/NAMESPACE-DESIGN.md SS5 explicitly recommends no change there,
# mirroring how suspend already leaves a warm LSAPI worker running.
handlers_account.CREATE_HOOKS.append(lambda account: nsisolation.enable_for_account(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: nsisolation.teardown_account(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_ftp.terminate_account_ftp(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_php_ini.terminate_account_php_ini(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_redirect.terminate_account_redirects(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: fileauth.terminate_account_fileauth(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: gitrepo.terminate_account_git(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: sshkeys.terminate_account_sshkeys(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: appinstaller.terminate_account_apps(account))


def register_op(name: str, handler) -> None:
    """Used by later phases (vhost/dns/db/mail/ssl) to add ops without
    server.py growing a giant import list at the top -- each phase's
    __init__ calls this once at daemon startup."""
    OP_TABLE[name] = handler


async def dispatch(op: str, params: dict) -> dict:
    actor = params.pop("_actor", "unknown")
    role = params.pop("_role", "unknown")
    handler = OP_TABLE.get(op)
    if handler is None:
        audit.record(actor, role, op, None, params, "failed", "unknown op")
        raise LookupError(f"unknown op '{op}'")

    loop = asyncio.get_running_loop()
    executor = REPORTING_EXECUTOR if op in REPORTING_OPS else None
    try:
        result = await loop.run_in_executor(executor, handler, params)
    except (ValidationError, ValueError) as exc:
        audit.record(actor, role, op, params.get("username"), params, "failed", str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("handler for %s failed", op)
        audit.record(actor, role, op, params.get("username"), params, "failed", str(exc))
        raise
    else:
        audit.record(actor, role, op, params.get("username"), params, "ok")
        return result


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername", "unix-peer")
    try:
        request = await read_frame(reader)
    except (asyncio.IncompleteReadError, ValueError) as exc:
        logger.warning("bad frame from %s: %s", peer, exc)
        writer.close()
        return

    op = request.get("op", "")
    params = request.get("params", {}) or {}

    try:
        result = await dispatch(op, dict(params))
        writer.write(encode_response(True, result=result))
    except (ValidationError, ValueError, LookupError) as exc:
        writer.write(encode_response(False, error_code="bad_request", error_message=str(exc)))
    except Exception as exc:  # noqa: BLE001
        writer.write(encode_response(False, error_code="internal_error", error_message=str(exc)))
    finally:
        await writer.drain()
        writer.close()


CGROUP_RECONCILE_INTERVAL_SECONDS = 5


async def _cgroup_reconcile_loop() -> None:
    """Moves any LSAPI worker still sitting in lshttpd's own cgroup into
    its owning account's slice -- see daemon/cgroups.py's module docstring
    for why this periodic-scan approach was chosen over a setuid/capability
    helper binary. Runs for the daemon's whole lifetime alongside the RPC
    server; a single reconcile failure must not kill this loop, since a
    transient error here (a PID exiting mid-scan, systemd being briefly
    busy) is expected background noise, not a fatal condition."""
    while True:
        try:
            moved = await asyncio.get_running_loop().run_in_executor(None, cgroups.reconcile_processes)
            if moved:
                logger.info("cgroup reconcile: moved %d process(es) into their account slice", moved)
        except Exception:
            logger.exception("cgroup reconcile pass failed")
        await asyncio.sleep(CGROUP_RECONCILE_INTERVAL_SECONDS)


async def amain() -> None:
    init_db()
    try:
        await asyncio.get_running_loop().run_in_executor(None, cgroups.bootstrap_all_slices)
    except Exception:
        logger.exception("cgroup slice bootstrap failed at startup")
    asyncio.create_task(_cgroup_reconcile_loop())

    socket_path = settings.rpc_socket
    Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
    if Path(socket_path).exists():
        os.unlink(socket_path)

    server = await asyncio.start_unix_server(handle_client, path=socket_path)

    # group-readable/writable by forgehost-api, nothing for "other"
    os.chmod(socket_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    try:
        gid = grp.getgrnam("forgehost-api").gr_gid
        os.chown(socket_path, 0, gid)
        # The socket's own group bit means nothing if the directory
        # containing it isn't traversable by that group too -- systemd's
        # RuntimeDirectory= creates /run/forgehost as root:root (this
        # service runs as root, no Group= override), so forgehost-api could
        # see the socket file's permissions but never reach it, getting a
        # generic "Permission denied" with no indication why. Caught by the
        # first real login attempt through forgehost-api, not by reasoning
        # about systemd's RuntimeDirectory semantics in advance.
        socket_dir = str(Path(socket_path).parent)
        os.chown(socket_dir, 0, gid)
        os.chmod(socket_dir, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        logger.info("socket dir %s now group=%s mode=%o", socket_dir, gid, stat.S_IMODE(os.stat(socket_dir).st_mode))
    except KeyError:
        logger.warning("forgehost-api group not found; socket left root-only")
    except OSError:
        logger.exception("failed to chown/chmod %s for forgehost-api access", socket_path)

    logger.info("forgehostd listening on %s", socket_path)
    async with server:
        await server.serve_forever()


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("forgehostd must run as root")
    configure_logging(settings.log_dir)
    asyncio.run(amain())


if __name__ == "__main__":
    main()
