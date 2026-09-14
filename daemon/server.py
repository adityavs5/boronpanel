"""borond: the root-privileged provisioning daemon.

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
import pwd
import socket
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shared.config import settings
from shared.db import init_db
from shared.rpc import encode_response, read_frame
from shared.validation import ValidationError

from daemon import panel_jobs, snapshot_restores, snapshot_jobs, wpmanager, appinstaller, audit, backup, branding, bulkops, cgroups, cloudflare_accounts, cloudflare_ops, cmdjobs, composerui, cpanel_import, custom_pages, disktree, dbmonitor, events, fail2ban, fileauth, filebrowser, firewall, forwarding, gitrepo, handlers_account, handlers_auth, handlers_cron, handlers_database, handlers_dns, handlers_domain, handlers_email_routing, handlers_ftp, handlers_hotlink, handlers_ipblock, handlers_mail, handlers_maintenance, handlers_notes, handlers_php_ini, handlers_redirect, handlers_usage, handlers_wildcard, health, identity_admin, imapsync, impersonation, ipban, ipmanager, ipwhitelist, logs, lscache, maillog, mailqueue, malware, monitoring, nameservers, nodeapps, notifications, nsisolation, ols, onboarding, parked, phpext, phpfunctions, plans, pma, portable_archive, procmanager, pythonapps, redisacct, resellers, servicemgr, site_templates, sitestats, slowquery, spamfilter, sshkeys, ssl, staging, terminal, totp, updates, usage_alerts, waf, webhooks, wordpress, wpcli
from daemon.logsetup import configure_logging

logger = logging.getLogger("borond")

OP_TABLE = {
    "panel.config.status": panel_jobs.status,
    "panel.config.start": panel_jobs.start,
    "snapshot.restore.configuration": snapshot_restores.configuration_options,
    "snapshot.restore.databases": snapshot_restores.database_options,
    "snapshot.restore.mailboxes": snapshot_restores.mailbox_options,
    "snapshot.restore.mail_routing": snapshot_restores.routing_options,
    "snapshot.restore.trigger": snapshot_restores.trigger,
    "snapshot.restore.undo": snapshot_restores.undo,
    "snapshot.restore.list": snapshot_restores.list_restores,
    "snapshot.destination.list": snapshot_jobs.destinations,
    "snapshot.destination.create": snapshot_jobs.create_destination,
    "snapshot.destination.initialize": snapshot_jobs.initialize_destination,
    "snapshot.destination.recovery_key": snapshot_jobs.recovery_key,
    "snapshot.policy.list": snapshot_jobs.policies,
    "snapshot.policy.save": snapshot_jobs.save_policy,
    "snapshot.policy.run": snapshot_jobs.queue_policy,
    "snapshot.run.list": snapshot_jobs.runs,
    "snapshot.run.browse": snapshot_jobs.browse,
    "account.create": handlers_account.create_account,
    "account.get": handlers_account.get_account,
    "account.list": handlers_account.list_accounts,
    "account.suspend": handlers_account.suspend_account,
    "account.unsuspend": handlers_account.unsuspend_account,
    "account.terminate": handlers_account.terminate_account,
    "account.set_php_version": handlers_account.set_php_version,
    "account.set_limits": handlers_account.set_limits,
    "account.reactivate": handlers_account.reactivate_account,
    "ipmanager.list": ipmanager.list_state,
    "ipmanager.import": ipmanager.import_addresses,
    "ipmanager.update": ipmanager.update_ip,
    "ipmanager.delete": ipmanager.delete_ip,
    "ipmanager.policy.set": ipmanager.set_policy,
    "ipmanager.assign": ipmanager.assign_account,
    "ipmanager.assign_new": ipmanager.assign_for_new_account,
    "portable.import.trigger": portable_archive.trigger_import,
    "portable.import.get": portable_archive.get_job,
    "portable.import.list": portable_archive.list_jobs,
    "portable.export.prepare": portable_archive.prepare_download,
    "reseller.plan.create": resellers.create_plan,
    "reseller.plan.update": resellers.update_plan,
    "reseller.plan.list": resellers.list_plans,
    "reseller.plan.delete": resellers.delete_plan,
    "reseller.create": resellers.create_reseller,
    "reseller.list": resellers.list_resellers,
    "reseller.update": resellers.update_reseller,
    "reseller.dashboard": resellers.dashboard,
    "reseller.account.create": resellers.create_account,
    "reseller.account.lifecycle": resellers.lifecycle,
    "templates.suspension_designs.get": site_templates.get_suspension_designs,
    "templates.suspension_designs.apply": site_templates.apply_suspension_design,
    "cron.list": handlers_cron.list_cron_jobs,
    "cron.add": handlers_cron.add_cron_job,
    "cron.update": handlers_cron.update_cron_job,
    "cron.delete": handlers_cron.delete_cron_job,
    "cron.mailto.get": handlers_cron.get_cron_mailto,
    "cron.mailto.set": handlers_cron.set_cron_mailto,
    "domain.add": handlers_domain.add_domain,
    "domain.remove": handlers_domain.remove_domain,
    "domain.list": handlers_domain.list_domains,
    "domain.set_php_version": handlers_domain.set_domain_php_version,
    "usage.get": handlers_usage.get_account_usage,
    "bandwidth.get": handlers_usage.get_bandwidth,
    "bandwidth.ranking": handlers_usage.get_bandwidth_ranking,
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
    # Cloudflare DNS/CDN provider (docs/PLAN-cloudflare.md Phases 0/1)
    "cf.health": cloudflare_ops.health,
    "cf.zone_enable": cloudflare_ops.zone_enable,
    "cf.zone_status": cloudflare_ops.zone_status,
    "cf.zone_disable": cloudflare_ops.zone_disable,
    "cf.purge_cache": cloudflare_ops.purge_cache,
    # Phase 2+3 features 2/3/4: proxy toggle rails + edge-range materialization
    "cf.rails_status": cloudflare_ops.rails_status,
    "cf.refresh_ranges": cloudflare_ops.refresh_ranges,
    "cf.enable_proxy": cloudflare_ops.enable_proxy,
    # Phase 2+3 feature 6: auto-enable settings
    "cf.settings_get": cloudflare_ops.settings_get,
    "cf.settings_set": cloudflare_ops.settings_set,
    # Phase 2+3 features 7/8/9: bulk migrate, fleet overview, UFW lockdown
    "cf.bulk_migrate": cloudflare_ops.bulk_migrate,
    "cf.zones_overview": cloudflare_ops.zones_overview,
    "cf.bulk_purge": cloudflare_ops.bulk_purge,
    "cf.lockdown": cloudflare_ops.lockdown,
    # Phase 2+3 feature 1: Cloudflare account pool (multi-account)
    "cf.account_list": cloudflare_accounts.list_accounts,
    "cf.account_add": cloudflare_accounts.add_account,
    "cf.account_set": cloudflare_accounts.set_account,
    "cf.account_delete": cloudflare_accounts.delete_account,
    "cf.account_test": cloudflare_accounts.test_account,
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
    "ssl.issue_wildcard": ssl.issue_wildcard_certificate,
    "ssl.status": ssl.certificate_status,
    "ssl.dashboard": ssl.get_ssl_dashboard,
    "ssl.admin.dashboard": ssl.get_admin_ssl_dashboard,
    # File manager: FileBrowser Quantum (the custom file.* ops that used to live
    # here were retired 2026-07-09 after FB Quantum was verified live end-to-end;
    # daemon/filemanager.py now only provides the shared path-jail helpers that
    # fileauth/composer/disktree/gitrepo reuse). One shared /home source,
    # proxy-header auth, per-account scope.
    "fb.bootstrap": filebrowser.bootstrap,
    "fb.add_source": filebrowser.add_source,
    "fb.remove_source": filebrowser.remove_source,
    "fb.refresh_all": filebrowser.refresh_all_sources,
    "fb.status": filebrowser.status,
    "fb.open": filebrowser.open_access,
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
    # Phase 8 follow-up: per-account PHP extension enable/disable
    "php_ext.list": phpext.list_extensions,
    "php_ext.set": phpext.set_extensions,
    "php_ext.reset": phpext.reset_extensions,
    # QA round 2, item 9: admin-only disable_functions overrides, per-account
    # or per-domain, layered on the system-wide hardened default -- a
    # deliberately separate surface from php_ini.* above (customer-editable).
    "php_functions.get": phpfunctions.get_overrides,
    "php_functions.set": phpfunctions.set_override,
    "php_functions.delete": phpfunctions.delete_override,
    # QA round 2, item 10: admin-editable suspension page + welcome email template.
    "templates.suspended_page.get": site_templates.get_suspended_page,
    "templates.suspended_page.set": site_templates.set_suspended_page,
    "templates.welcome_email.get": site_templates.get_welcome_email_template,
    "templates.welcome_email.set": site_templates.set_welcome_email_template,
    "templates.welcome_email.reset": site_templates.reset_welcome_email_template,
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
    "firewall.bypass.list": firewall.list_bypass,
    "firewall.bypass.add": firewall.add_bypass,
    "firewall.bypass.delete": firewall.delete_bypass,
    "firewall.status": firewall.get_status,
    "firewall.enable": firewall.enable_firewall,
    "firewall.disable": firewall.disable_firewall,
    # Product expansion: account-scoped file/script malware scans. Scans run
    # on malware.py's own bounded executor; actions remain explicit.
    "malware.engine.status": malware.engine_status,
    "malware.scan.start": malware.trigger_scan,
    "malware.scan.list": malware.list_scans,
    "malware.scan.get": malware.get_scan,
    "malware.finding.list": malware.list_findings,
    "malware.finding.quarantine": malware.quarantine,
    "malware.finding.restore": malware.restore,
    "malware.finding.ignore": malware.ignore,
    "ols.admin.status": ols.admin_status,
    "ols.admin.settings.update": ols.update_admin_settings,
    "ols.admin.reload": ols.graceful_reload,
    "ols.admin.password.reset": ols.reset_admin_password,
    "ols.admin.password.reveal": ols.reveal_admin_password,
    # QA round 2, item 14: permanent server-wide IP/CIDR bans (daemon/ipban.py) --
    # distinct from firewall.* above (port-scoped rules) and from
    # ipwhitelist.* below (panel-login allowlist).
    "ipban.list": ipban.list_bans,
    "ipban.add": ipban.ban_ip,
    "ipban.delete": ipban.unban_ip,
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
    # Phase 7a feature 1: NodeJS app hosting
    "apps.node.create": nodeapps.create_app,
    "apps.node.update": nodeapps.update_app,
    "apps.node.delete": nodeapps.delete_app,
    "apps.node.start": nodeapps.start_app,
    "apps.node.stop": nodeapps.stop_app,
    "apps.node.restart": nodeapps.restart_app,
    "apps.node.npm_install": nodeapps.npm_install,
    "apps.node.get": nodeapps.get_app,
    "apps.node.list": nodeapps.list_apps,
    "apps.node.logs": nodeapps.get_logs,
    # Phase 7a feature 2: Python app hosting
    "apps.python.create": pythonapps.create_app,
    "apps.python.update": pythonapps.update_app,
    "apps.python.delete": pythonapps.delete_app,
    "apps.python.start": pythonapps.start_app,
    "apps.python.stop": pythonapps.stop_app,
    "apps.python.restart": pythonapps.restart_app,
    "apps.python.pip_install": pythonapps.pip_install,
    "apps.python.get": pythonapps.get_app,
    "apps.python.list": pythonapps.list_apps,
    "apps.python.logs": pythonapps.get_logs,
    # Phase 7a feature 3: per-account Redis
    "redis.enable": redisacct.enable_redis,
    "redis.disable": redisacct.disable_redis,
    "redis.set_mem_limit": redisacct.set_mem_limit,
    "redis.flush": redisacct.flush,
    "redis.status": redisacct.get_status,
    "redis.connection_info": redisacct.get_connection_info,
    # Phase 7a feature 4: LSCache
    "lscache.get": lscache.get_settings,
    "lscache.set": lscache.set_settings,
    "lscache.purge": lscache.purge,
    "lscache.stats": lscache.get_stats,
    # Phase 7b feature 1: cPanel backup import
    "cpanel_import.trigger": cpanel_import.trigger_import,
    "cpanel_import.get": cpanel_import.get_job,
    "cpanel_import.list": cpanel_import.list_jobs,
    # Phase 7b feature 3: email notifications
    "notifications.settings.get": notifications.get_settings,
    "notifications.settings.set": notifications.set_settings,
    "notifications.prefs.get": notifications.get_prefs,
    "notifications.prefs.set": notifications.set_prefs,
    # Phase 7b feature 4: webhooks
    "webhooks.create": webhooks.create_webhook,
    "webhooks.list": webhooks.list_webhooks,
    "webhooks.get": webhooks.get_webhook,
    "webhooks.update": webhooks.update_webhook,
    "webhooks.delete": webhooks.delete_webhook,
    "webhooks.deliveries.list": webhooks.list_deliveries,
    "webhooks.test": webhooks.test_webhook,
    # Phase 7b feature 5: account usage alerts
    "usage.limits.get": usage_alerts.get_limits,
    "usage.limits.set": usage_alerts.set_limits,
    "usage.alerts.get": usage_alerts.get_alerts,
    # Phase 8 feature 1: login-as-user (admin impersonation)
    "impersonation.create_token": impersonation.create_token,
    "impersonation.redeem_token": impersonation.redeem_token,
    "impersonation.end": impersonation.end,
    # Phase 8 feature 2: admin account editor (identity + passwords)
    "account.set_password": identity_admin.set_account_password,
    "account.set_contact_email": identity_admin.set_contact_email,
    "account.set_primary_domain": identity_admin.set_primary_domain,
    "account.rename": identity_admin.rename_account,
    # Phase 8 feature 3: parked (alias) domains
    "parked.add": parked.add_parked_domain,
    "parked.list": parked.list_parked_domains,
    "parked.remove": parked.remove_parked_domain,
    # Phase 8 feature 4: whole-domain forwarding
    "forwarding.set": forwarding.set_forwarding,
    "forwarding.get": forwarding.get_forwarding,
    "forwarding.delete": forwarding.delete_forwarding,
    # Phase 8 feature 5: email delivery log (Postfix log, scoped per account)
    "maillog.delivery": maillog.get_delivery_log,
    # Phase 8 feature 6: per-domain email routing (Local/Remote/Backup MX)
    "email_routing.get": handlers_email_routing.get_routing,
    "email_routing.set": handlers_email_routing.set_routing,
    # Phase 8 feature 7: web terminal (ephemeral SSH key inject/remove)
    "terminal.open": terminal.open_session,
    "terminal.close": terminal.close_session,
    "terminal.list": terminal.list_sessions,
    # Phase 8 feature 8: WP-CLI UI (async, as the account user)
    "wpmanager.inventory": wpmanager.inventory,
    "wpmanager.operation": wpmanager.operation,
    "wpmanager.login": wpmanager.login,
    "wpmanager.scan": wpmanager.scan,
    "wpmanager.refresh": wpmanager.refresh_site,
    "wpmanager.remove": wpmanager.remove,
    "wpcli.detect": wpcli.detect_installs,
    "wpcli.run": wpcli.run_wpcli,
    "wpcli.get": wpcli.get_run,
    "wpcli.list": wpcli.list_runs,
    # Phase 8 feature 9: Composer UI (async, as the account user)
    "composer.run": composerui.run_composer,
    "composer.get": composerui.get_run,
    "composer.list": composerui.list_runs,
    # Phase 8 feature 10: process manager (strictly uid-scoped)
    "processes.list": procmanager.list_processes,
    "processes.kill": procmanager.kill_process,
    # Phase 8 feature 11: admin-only account notes (append-only)
    "notes.add": handlers_notes.add_note,
    "notes.list": handlers_notes.list_notes,
    # Phase 8 feature 12: bulk account operations (async, stop on first failure)
    "bulk.trigger": bulkops.trigger_bulk_action,
    "bulk.get": bulkops.get_bulk_action,
    # Phase 7b feature 6: staging environments
    "staging.create": staging.create_staging,
    "staging.sync": staging.sync_staging,
    "staging.get": staging.get_staging,
    "staging.delete": staging.delete_staging,
    # Run A feature 1: plan templates
    "plan.create": plans.create_plan,
    "plan.list": plans.list_plans,
    "plan.get": plans.get_plan,
    "plan.update": plans.update_plan,
    "plan.delete": plans.delete_plan,
    "plan.apply": plans.apply_plan,
    # Run A feature 3: white-label branding
    "branding.get": branding.get_settings,
    "branding.set": branding.set_settings,
    "branding.logo.upload": branding.upload_logo,
    "branding.logo.remove": branding.remove_logo,
    "branding.favicon.upload": branding.upload_favicon,
    "branding.favicon.remove": branding.remove_favicon,
    # Run A feature 4: client onboarding wizard (once-only gate)
    "onboarding.get": onboarding.get_onboarding,
    "onboarding.set": onboarding.set_onboarding,
    # Run A feature 5: service health monitoring + admin alert emails
    "monitoring.settings.get": monitoring.get_settings,
    "monitoring.settings.set": monitoring.set_settings,
    "monitoring.history": monitoring.get_history,
    "monitoring.check": monitoring.check_services,
    # Panel update system: check/apply/rollback panel releases. start and
    # rollback are admin-only at the API layer (require_admin + conditional
    # 2FA confirmation before the RPC is ever sent -- ARCHITECTURE.md SS2's
    # trust model: authorization happens in boron-api).
    "update.check": updates.check,
    "update.status": updates.get_status,
    "update.start": updates.start_update,
    "update.rollback": updates.start_rollback,
    "update.history": updates.get_history,
    "update.log": updates.get_log,
    "update.cleanup": updates.cleanup_old_versions,
    # Missing-features batch, goal feature 2: per-domain maintenance mode.
    "maintenance.get": handlers_maintenance.get_maintenance,
    "maintenance.set": handlers_maintenance.set_maintenance,
    "maintenance.list_active": handlers_maintenance.list_active_maintenance,
    "maintenance.sweep_expired": lambda params: handlers_maintenance.sweep_expired(),
    # Missing-features batch, goal feature 3: wildcard domains.
    "wildcard.get": handlers_wildcard.get_wildcard,
    "wildcard.set": handlers_wildcard.set_wildcard,
    # Missing-features batch, goal feature 4: custom error pages.
    "errorpages.list": custom_pages.rpc_list_error_pages,
    "errorpages.get": custom_pages.rpc_get_error_page,
    "errorpages.set": custom_pages.rpc_set_error_page,
    "errorpages.delete": custom_pages.rpc_delete_error_page,
    # Missing-features batch, goal feature 5: per-mailbox spam filters.
    "spamfilter.entries.list": spamfilter.list_entries,
    "spamfilter.entries.add": spamfilter.add_entry,
    "spamfilter.entries.delete": spamfilter.delete_entry,
    "spamfilter.entries.import": spamfilter.import_entries,
    # Missing-features batch, goal feature 1: IMAPSync migrations.
    "imapsync.start": imapsync.start_migration,
    "imapsync.list_folders": imapsync.rpc_list_folders,
    "imapsync.status": imapsync.get_status,
    "imapsync.list": imapsync.list_jobs,
    "imapsync.cancel": imapsync.cancel_migration,
    "imapsync.list_active_admin": imapsync.list_active_admin,
    # Missing-features batch, goal feature 6: per-domain site statistics.
    "sitestats.get": sitestats.get_stats,
    "sitestats.admin_summary": sitestats.get_admin_summary,
    "sitestats.refresh": lambda params: sitestats.refresh_domain(params["domain"]),
    "sitestats.configure_geoip": sitestats.configure_geoip,
    # Missing-features batch, goal feature 7: live MariaDB monitor.
    "dbmonitor.processlist": lambda params: dbmonitor.get_processlist(),
    "dbmonitor.slow_queries": lambda params: dbmonitor.get_recent_slow_queries(),
    "dbmonitor.db_sizes": lambda params: dbmonitor.get_db_sizes(),
    "dbmonitor.connections": lambda params: dbmonitor.get_connection_summary(),
    "dbmonitor.kill_query": lambda params: dbmonitor.kill_query(params["thread_id"]),
    "dbmonitor.kill_privilege_status": lambda params: dbmonitor.kill_query_privilege_status(),
    "dbmonitor.bootstrap_kill_privilege": dbmonitor.bootstrap_kill_query_privilege,
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
    "snapshot.destination.initialize", "snapshot.run.browse", "snapshot.restore.databases", "snapshot.restore.mailboxes", "snapshot.restore.configuration", "snapshot.restore.mail_routing",
    "snapshot.restore.trigger", "snapshot.restore.undo",  # Preflight may decrypt recovery metadata.
    "disktree.get", "disktree.top_files", "usage.get",
    # Phase 5: admin-only polling/dashboard ops that shell out or sample
    # live system state -- same isolation reasoning as disktree/usage
    # above, just for the admin surface instead of the customer one.
    "health.get", "health.history",
    "ipmanager.list",
    "portable.import.get", "portable.import.list", "portable.export.prepare",
    # Run A feature 5: history is dashboard-polled; check shells out 7x
    # systemctl -- same isolation reasoning as health/services below.
    "monitoring.history", "monitoring.check",
    # Panel update system: check/status may do an outbound GitHub API call
    # (1h cache expiry) and status is dashboard-polled -- same isolation
    # reasoning as cf.health. start/rollback return instantly (their work
    # runs on updates.py's own single-worker executor) so they stay off
    # the reporting pool.
    "update.check", "update.status", "update.history", "update.log",
    "services.status", "services.list",
    "mailqueue.list",
    "firewall.list", "firewall.bypass.list",
    "malware.engine.status", "malware.scan.list", "malware.scan.get", "malware.finding.list", "ols.admin.status",
    "ssl.admin.dashboard",
    "fail2ban.list_jails", "fail2ban.get_jail", "fail2ban.recent_events",
    "waf.status", "waf.blocked_requests",
    "slowquery.list", "slowquery.status",
    # Phase 7a feature 3: redis.status shells out to `redis-cli INFO` --
    # same dashboard-polling isolation reasoning as services.status above.
    "redis.status",
    # Phase 7a feature 4: lscache.stats walks the domain's cache-storage
    # directory tree -- same reasoning as disktree.get/top_files above.
    "lscache.stats",
    # Phase 8 feature 5: parses up to ~12MB of the Postfix mail log -- same
    # isolation reasoning as disktree/usage above.
    "maillog.delivery",
    # Phase 8 feature 10: samples live process state (psutil, ~0.1s CPU sample)
    # -- same dashboard-polling isolation reasoning as health/services above.
    "processes.list",
    # Phase 8 feature 13: file search walks the account's home tree -- same
    # isolation reasoning as disktree.get/top_files above.
    "file.search",
    # Cloudflare health makes outbound HTTPS calls to api.cloudflare.com --
    # a slow/unreachable external API polled by a dashboard must never
    # stall the default executor (same reasoning as services.status above).
    "cf.health",
    # Phase 2+3 feature 1: adding/testing a pool account live-verifies its
    # token against api.cloudflare.com -- same outbound-HTTPS isolation.
    "cf.account_add", "cf.account_test",
    # Missing-features batch, goal feature 1: imapsync.list_folders makes a
    # real outbound IMAP connection to a customer-supplied external server
    # (could be slow/hanging); status/list/list_active_admin are polled by
    # the migration progress UI -- same dashboard-polling isolation
    # reasoning as slowquery.list/status above.
    "imapsync.list_folders", "imapsync.status", "imapsync.list", "imapsync.list_active_admin",
    # Missing-features batch, goal feature 7: DB monitor is explicitly
    # auto-refreshed every 10s per the goal -- same isolation reasoning as
    # processes.list/mailqueue.list above.
    "dbmonitor.processlist", "dbmonitor.slow_queries", "dbmonitor.db_sizes", "dbmonitor.connections",
    "dbmonitor.kill_privilege_status",
    # Missing-features batch, goal feature 6: site stats read from disk/DB
    # for a dashboard -- same reasoning as usage.get above.
    "sitestats.get", "sitestats.admin_summary",
    # Phase 2+3 feature 3: refresh_ranges fetches GET /ips over HTTPS then
    # re-renders OLS/fail2ban -- keep off the default executor.
    "cf.refresh_ranges",
    # Phase 2+3 features 7/8: fleet ops that make O(zones) outbound HTTPS
    # calls (migrate/purge, live overview) -- keep off the default executor.
    "cf.bulk_migrate", "cf.bulk_purge", "cf.zones_overview",
}

# Each phase wires its own account-scoped teardown/suspend behavior here
# instead of handlers_account.py importing every phase directly (avoids an
# import cycle: ols/dns/db/mail modules all need handlers_account's Account
# type, not the other way around).
handlers_account.SUSPEND_HOOKS.append(lambda account: ols.suspend_vhost(account))
handlers_account.UNSUSPEND_HOOKS.append(lambda account: ols.unsuspend_vhost(account))
# QA round 2, item 8 (critical): ols.suspend_vhost's rewrite-all-to-
# suspended-page context stops FUTURE requests from being served/cached,
# but does nothing about pages LSCache (or a proxied Cloudflare zone)
# already had cached on disk/at the edge BEFORE suspension -- a real
# suspended site kept serving stale cached content because nothing ever
# purged it. Run right after the vhost flip so "stop future caching" and
# "clear what's already cached" happen together as one suspend action.
# Also purged on unsuspend, for the same reason in reverse: caching stays
# possible while suspended (the vhost's rewrite-all context has its own
# cache-bypassing content, but a customer's own page could still get
# cached under the *unsuspended* domain name by something upstream while
# still suspended is not a real risk here -- this is about not serving a
# stale "suspended" page to a visitor for a few minutes right after
# reactivation).
handlers_account.SUSPEND_HOOKS.append(lambda account: lscache.purge_account_domains(account))
handlers_account.UNSUSPEND_HOOKS.append(lambda account: lscache.purge_account_domains(account))
handlers_account.SUSPEND_HOOKS.append(lambda account: cloudflare_ops.purge_account_zones(account))
handlers_account.UNSUSPEND_HOOKS.append(lambda account: cloudflare_ops.purge_account_zones(account))
handlers_account.PHP_VERSION_HOOKS.append(lambda account: ols.refresh_vhost(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: ols.terminate_vhost(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_dns.terminate_account_zones(account))
# Cloudflare zones (docs/PLAN-cloudflare.md SS2): terminate_account_zones
# above already deletes both backends per zone via dnsprovider.delete_zone;
# this hook additionally sweeps any CloudflareZone row whose DnsZone cache
# row is missing (belt and braces -- both are idempotent).
handlers_account.TERMINATE_HOOKS.append(lambda account: cloudflare_ops.terminate_account_cloudflare(account))
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
handlers_account.TERMINATE_HOOKS.append(lambda account: ipmanager.release_account(account))
# Phase 6b: new accounts (and reactivated ones, same CREATE_HOOKS list) get
# namespace isolation by default; termination tears down the per-uid
# lsnsctl gate + any persisted /var/lsns/<uid> state. No suspend/unsuspend
# hook -- docs/NAMESPACE-DESIGN.md SS5 explicitly recommends no change there,
# mirroring how suspend already leaves a warm LSAPI worker running.
handlers_account.CREATE_HOOKS.append(lambda account: nsisolation.enable_for_account(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: nsisolation.teardown_account(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_ftp.terminate_account_ftp(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_php_ini.terminate_account_php_ini(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: phpext.terminate_account_php_extensions(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_redirect.terminate_account_redirects(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: fileauth.terminate_account_fileauth(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: gitrepo.terminate_account_git(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: sshkeys.terminate_account_sshkeys(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: appinstaller.terminate_account_apps(account))
# Phase 7a feature 1: NodeJS app hosting. Runs after ols.terminate_vhost
# (already appended above) so the proxying vhost contexts are gone before
# each app's own systemd unit/env file is removed -- order doesn't change
# correctness here (removing the unit first would just mean a brief window
# where the vhost still points at a now-stopped backend), but matches this
# list's existing convention of tearing down the thing a later hook
# references before the thing that referenced it.
handlers_account.TERMINATE_HOOKS.append(lambda account: nodeapps.terminate_account_node_apps(account))
# Phase 7a feature 2: Python app hosting -- same ordering reasoning as
# NodeJS apps above.
handlers_account.TERMINATE_HOOKS.append(lambda account: pythonapps.terminate_account_python_apps(account))
# Phase 7a feature 3: per-account Redis -- same ordering reasoning as
# NodeJS/Python apps above.
handlers_account.TERMINATE_HOOKS.append(lambda account: redisacct.terminate_account_redis(account))
# Phase 7a feature 4: LSCache -- cleans up the primary domain's row/cache
# dir (addon/subdomain rows are already handled by handlers_domain.
# remove_domain, which never runs for a terminated account's own primary
# domain, same as Redirect/FileAuthDir before it).
handlers_account.TERMINATE_HOOKS.append(lambda account: lscache.terminate_account_lscache(account))
# Phase 7b features 3/4: account-lifecycle email notifications + webhooks,
# fanned out through the single daemon/events.py entry point (each channel
# does its own enabled/subscribed filtering, so this wiring never needs to
# change when a new notification/webhook event type is added elsewhere).
# CREATE_HOOKS' account_snapshot carries a transient `.initial_password`
# attribute (see handlers_account.create_account/reactivate_account) that
# only the "account created" email actually uses.
handlers_account.CREATE_HOOKS.append(
    lambda account: events.emit("account.created", account, initial_password=getattr(account, "initial_password", None))
)
handlers_account.SUSPEND_HOOKS.append(lambda account: events.emit("account.suspended", account))
handlers_account.UNSUSPEND_HOOKS.append(lambda account: events.emit("account.unsuspended", account))
handlers_account.TERMINATE_HOOKS.append(lambda account: events.emit("account.terminated", account))
# Phase 7b feature 6: staging environments -- the staging domain/database
# themselves are already torn down by the existing ols.terminate_vhost/
# handlers_database.terminate_account_databases hooks above (ordinary
# Domain/DatabaseGrant rows scoped to this account); this only cleans up
# this feature's own bookkeeping row.
handlers_account.TERMINATE_HOOKS.append(lambda account: staging.terminate_account_staging(account))
# Phase 8 feature 3: drop ParkedDomain bookkeeping rows on termination (the
# parked Domain rows + vhosts are already handled by ols.terminate_vhost).
handlers_account.TERMINATE_HOOKS.append(lambda account: parked.terminate_account_parked(account))
# File manager v2 (FileBrowser Quantum): on create, make the account available
# (apply the ownership-mitigation ACL so FB-created files stay account-usable);
# on terminate, the shared /home source needs no per-account teardown (the home
# dir + its index entries are removed by userdel), so remove_source is an
# idempotent lifecycle/audit marker. Both are best-effort (CREATE/TERMINATE
# hooks are already wrapped in try/except by handlers_account).
handlers_account.CREATE_HOOKS.append(lambda account: filebrowser.add_source_for_account(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: filebrowser.remove_source_for_account(account))


def register_op(name: str, handler) -> None:
    """Used by later phases (vhost/dns/db/mail/ssl) to add ops without
    server.py growing a giant import list at the top -- each phase's
    __init__ calls this once at daemon startup."""
    OP_TABLE[name] = handler


# Ops that change an account's lifecycle state. Successful dispatches of
# these additionally land in the dedicated account-events log (who/when/IP) —
# see audit.record_account_event.
LIFECYCLE_OPS = {
    "account.create": "created",
    "account.suspend": "suspended",
    "account.unsuspend": "unsuspended",
    "account.terminate": "terminated",
    # Reactivation recreates the Linux user and re-enables every associated
    # PanelUser login -- at least as security-relevant as the events above, so
    # it belongs in the who/when/IP account-events log too.
    "account.reactivate": "reactivated",
}


async def dispatch(op: str, params: dict) -> dict:
    actor = params.pop("_actor", "unknown")
    role = params.pop("_role", "unknown")
    ip = params.pop("_ip", None)
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
        if op in LIFECYCLE_OPS and params.get("username"):
            audit.record_account_event(LIFECYCLE_OPS[op], params["username"], actor=actor, role=role, ip=ip)
        return result


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername", "unix-peer")
    # The socket's group bit is necessary for API access but is not a
    # sufficient authorization boundary: any future process added to that
    # group would otherwise gain every root RPC. Require the kernel-reported
    # peer UID to be the dedicated API service account as defense in depth.
    sock = writer.get_extra_info("socket")
    if sock is not None and hasattr(socket, "SO_PEERCRED"):
        try:
            peer_pid, peer_uid, _peer_gid = __import__("struct").unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            api_uid = pwd.getpwnam("boron-api").pw_uid
            if peer_uid != api_uid:
                logger.warning("rejecting RPC peer uid=%s pid=%s", peer_uid, peer_pid)
                writer.close()
                return
        except (OSError, KeyError, ValueError):
            logger.exception("could not verify RPC peer credentials")
            writer.close()
            return
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
        from daemon.snapshot_databases import cleanup_abandoned_logins
        cleanup_abandoned_logins()
        snapshot_jobs.recover_runs()
        snapshot_restores.recover_restores()
    except Exception:
        logger.exception("Snapshot worker recovery failed at startup")
    try:
        await asyncio.get_running_loop().run_in_executor(None, cgroups.bootstrap_all_slices)
    except Exception:
        logger.exception("cgroup slice bootstrap failed at startup")
    asyncio.create_task(_cgroup_reconcile_loop())
    try:
        await asyncio.get_running_loop().run_in_executor(None, nodeapps.bootstrap_all_node_apps)
    except Exception:
        logger.exception("NodeJS app bootstrap failed at startup")
    try:
        await asyncio.get_running_loop().run_in_executor(None, pythonapps.bootstrap_all_python_apps)
    except Exception:
        logger.exception("Python app bootstrap failed at startup")
    try:
        await asyncio.get_running_loop().run_in_executor(None, redisacct.bootstrap_all_redis)
    except Exception:
        logger.exception("Redis bootstrap failed at startup")

    try:
        malware.recover_interrupted()
    except Exception:
        logger.exception("Malware scan recovery failed at startup")

    try:
        await asyncio.get_running_loop().run_in_executor(None, phpext.bootstrap_all_php_extensions)
    except Exception:
        logger.exception("PHP extension scan-dir bootstrap failed at startup")

    # File manager v2: ensure FileBrowser Quantum's config + systemd service
    # are in place and running (no-op/idempotent once bootstrapped; logged, not
    # fatal, if the binary isn't installed yet -- same pattern as above).
    try:
        await asyncio.get_running_loop().run_in_executor(None, filebrowser.bootstrap)
    except Exception:
        logger.exception("FileBrowser Quantum bootstrap failed at startup")

    # Phase 2+3 feature 1: fold a legacy single-token config into the account
    # pool on first start after upgrade (idempotent; no-op once pooled).
    try:
        cloudflare_accounts.migrate_single_token()
    except Exception:
        logger.exception("Cloudflare single-token pool migration failed at startup")

    # Cloudflare: catch any pending->active transition that happened while
    # the daemon was down (the */15 cron covers steady-state).
    try:
        await asyncio.get_running_loop().run_in_executor(None, cloudflare_ops.reconcile_pending_zones)
    except Exception:
        logger.exception("Cloudflare pending-zone reconcile failed at startup")

    socket_path = settings.rpc_socket
    Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
    if Path(socket_path).exists():
        os.unlink(socket_path)

    server = await asyncio.start_unix_server(handle_client, path=socket_path)

    # group-readable/writable by boron-api, nothing for "other"
    os.chmod(socket_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    try:
        gid = grp.getgrnam("boron-api").gr_gid
        os.chown(socket_path, 0, gid)
        # The socket's own group bit means nothing if the directory
        # containing it isn't traversable by that group too -- systemd's
        # RuntimeDirectory= creates /run/boron as root:root (this
        # service runs as root, no Group= override), so boron-api could
        # see the socket file's permissions but never reach it, getting a
        # generic "Permission denied" with no indication why. Caught by the
        # first real login attempt through boron-api, not by reasoning
        # about systemd's RuntimeDirectory semantics in advance.
        socket_dir = str(Path(socket_path).parent)
        os.chown(socket_dir, 0, gid)
        os.chmod(socket_dir, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        logger.info("socket dir %s now group=%s mode=%o", socket_dir, gid, stat.S_IMODE(os.stat(socket_dir).st_mode))
    except KeyError:
        logger.warning("boron-api group not found; socket left root-only")
    except OSError:
        logger.exception("failed to chown/chmod %s for boron-api access", socket_path)

    logger.info("borond listening on %s", socket_path)
    try:
        await asyncio.get_running_loop().run_in_executor(None, panel_jobs.recover_jobs)
    except Exception:
        logger.exception("Panel configuration recovery failed at startup")
    async with server:
        await server.serve_forever()


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("borond must run as root")
    # Same guard as boron-api's startup: the daemon signs the 2FA-pending
    # token with this key too, so refuse to run on the insecure default.
    from shared.config import require_secure_session_secret

    require_secure_session_secret()
    configure_logging(settings.log_dir)
    asyncio.run(amain())


if __name__ == "__main__":
    main()
