# Initial-goal regression checkpoint

The initial goal remains active; the queued expansion follows its completion.

- Deployed mailbox and SSH-key management controls as `1e5a8dc`; authenticated deployment checks passed. See RECOVERY-DEPLOYMENT-2026-09-14.md.
- Thirty browser checks passed against that build: backup job configuration, destinations and recovery information, notification preferences, customer history, file/database/mailbox/email-routing restore and undo, and DNS/database/SSL management. Coverage includes both themes and light/dark layouts. Log: `/tmp/boron-backup-final-ui-tests.log`.
- The shared table audit found that a nested control's keyboard event could also activate its row. The fix leaves nested controls to handle their own events, while retaining Enter/Space activation on the focused row. A new production build passed, followed by four mailbox/SSH keyboard checks and four subdomain checks. Logs: `/tmp/boron-table-keyboard-build.log` and `/tmp/boron-table-keyboard-tests.log`.
- Four long resource-management scenarios exceeded their aggregate 45-second test limit at differing points. All four reruns passed with a 120-second total scenario limit and unchanged assertions, taking 42.5–54.0 seconds each. Log: `/tmp/boron-resource-final-tests.log`.
- The isolated backend integration suite for jobs, encrypted storage, file/database recovery, SSH workflows and routing retention passed all 66 tests in 978.85 seconds. Its only warning is a Starlette TestClient dependency deprecation. Log: `/tmp/boron-backup-final-core-tests.log`.

The table fix passed its resource regression and was deployed as 389367f. HTTPS, authenticated admin configuration on port 2222 and backup-page health passed. Rollback copy: `/root/boron-setup/mail-recovery-before-20260914-080045`; log: `/root/boron-setup/table-keyboard-deploy.log`. Broader recovery regressions, full requirement audit and GitHub/release/self-update verification remain outstanding. GitHub authentication was verified as adityavs5; the latest published release is v1.1.3 with its tarball and SHA256 assets. Cloudflare recovery has no live provider mutation evidence because no provider account is connected.
