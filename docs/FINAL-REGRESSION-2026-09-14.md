# Initial-goal regression checkpoint

The initial goal remains active; the queued expansion follows its completion. Earlier entries below are chronological checkpoints, superseded by the final verification result.

- Deployed mailbox and SSH-key management controls as `1e5a8dc`; authenticated deployment checks passed. See RECOVERY-DEPLOYMENT-2026-09-14.md.
- Thirty browser checks passed against that build: backup job configuration, destinations and recovery information, notification preferences, customer history, file/database/mailbox/email-routing restore and undo, and DNS/database/SSL management. Coverage includes both themes and light/dark layouts. Log: `/tmp/boron-backup-final-ui-tests.log`.
- The shared table audit found that a nested control's keyboard event could also activate its row. The fix leaves nested controls to handle their own events, while retaining Enter/Space activation on the focused row. A new production build passed, followed by four mailbox/SSH keyboard checks and four subdomain checks. Logs: `/tmp/boron-table-keyboard-build.log` and `/tmp/boron-table-keyboard-tests.log`.
- Four long resource-management scenarios exceeded their aggregate 45-second test limit at differing points. All four reruns passed with a 120-second total scenario limit and unchanged assertions, taking 42.5–54.0 seconds each. Log: `/tmp/boron-resource-final-tests.log`.
- The isolated backend integration suite for jobs, encrypted storage, file/database recovery, SSH workflows and routing retention passed all 66 tests in 978.85 seconds. Its only warning is a Starlette TestClient dependency deprecation. Log: `/tmp/boron-backup-final-core-tests.log`.

The table fix passed its resource regression and was deployed as 389367f. HTTPS, authenticated admin configuration on port 2222 and backup-page health passed. Rollback copy: `/root/boron-setup/mail-recovery-before-20260914-080045`; log: `/root/boron-setup/table-keyboard-deploy.log`. Broader recovery regressions, full requirement audit and GitHub/release/self-update verification remain outstanding. GitHub authentication was verified as adityavs5; the latest published release is v1.1.3 with its tarball and SHA256 assets. Cloudflare recovery has no live provider mutation evidence because no provider account is connected.

## Release checks and fresh live health

Update versioning, finalizer success/rollback, update API and archive-release tests completed with 89 passes and one skip in 259.56 seconds (`/tmp/boron-update-final-tests.log`). The skipped optional check requires ShellCheck, which is absent. Full repository regression is now running with skip reasons enabled in `/tmp/boron-full-final-tests.log`; it is not yet a passing result.

Fresh authenticated HTTPS checks confirmed both configured panel ports are 2222, the backup page and 2FA status endpoint respond successfully, and the deployed table source matches the tested checkout. API, provisioner, OpenLiteSpeed, PowerDNS, Dovecot and chrony are active; the clock is synchronized. Read-only proof: `/root/boron-setup/final-health-20260914.json`.

The saved live DNS, PHP, cron and mail-routing proofs were inspected again: all record restoration of their original QA state; the routing proof also records test-mailbox removal and disabling its temporary policy. No existing installation or hosting configuration was changed in this verification pass.

## Complete browser regression and schema timing

All 114 browser checks passed in 15.0 minutes against the production build (`/tmp/boron-full-final-browser-tests.log`). This includes WordPress install/management/backup/clone/search, both roles and themes, PHP defaults and per-site presets, terminal welcome, 2FA, clock diagnostics, local font budgets, and backup recovery workflows.

An isolated schema-creation timing check measured 14.006 seconds with separate implicit DDL commits versus 0.990 seconds inside one explicit transaction. Schema creation now uses an explicit transaction, improving startup time and preventing partially created tables/indexes after an error. Seven schema atomicity/data-preservation and legacy WordPress migration checks passed in 22.34 seconds (`/tmp/boron-schema-transaction-tests.log`). Two warnings concern cleanup of an older pytest temporary directory.

The earlier full backend run was deliberately interrupted after this validated implementation change; its partial result is not a full-suite pass. A new complete run must verify the revised schema implementation before deployment or release. The live panel still uses the prior verified revision.

The revised complete suite is running in `/tmp/boron-full-schema-final-tests.log`. Ubuntu's ShellCheck package was installed, and both previously skipped static checks now pass: release (`/tmp/boron-release-shellcheck-tests.log`, one test in 2.00 seconds) and installer (`/tmp/boron-installer-shellcheck-tests.log`, one test in 3.02 seconds). Their skip conditions were collected before package installation, so the ongoing full run still reports them as skipped. The complete suite remains the outstanding regression gate.

## Final regression and deployment result

The revised complete backend suite passed: **2,655 passed, two skipped, seven warnings in 2,914.53 seconds** (`/tmp/boron-full-schema-final-tests.log`). Both skips were ShellCheck checks collected before installation; the installer and release checks passed separately afterward as recorded above. All **114 browser checks** passed against the production build. No test failure remains from these final runs.

The tested schema change was deployed successfully on September 14. Additive migration, trusted HTTPS, authenticated admin login and configuration RPC on port 2222 passed. The deployment preserved configuration and created rollback copy `/root/boron-setup/mail-recovery-before-20260914-092314`; log: `/root/boron-setup/schema-final-deploy.log`.

Both authoritative Cloudflare nameservers returned the expected phpMyAdmin A record on the final recheck, and trusted HTTPS returned the expected sign-in redirect. The requested backup functionality is verified; see BACKUP-COMPLETION-AUDIT.md. GitHub publication and an actual panel self-update remain the final release gates.
