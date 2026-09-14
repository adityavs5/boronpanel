# Initial-goal regression checkpoint

The initial goal remains active; the queued expansion follows its completion.

- Deployed mailbox and SSH-key management controls as `1e5a8dc`; authenticated deployment checks passed. See RECOVERY-DEPLOYMENT-2026-09-14.md.
- Thirty browser checks passed against that build: backup job configuration, destinations and recovery information, notification preferences, customer history, file/database/mailbox/email-routing restore and undo, and DNS/database/SSL management. Coverage includes both themes and light/dark layouts. Log: `/tmp/boron-backup-final-ui-tests.log`.
- The shared table audit found that a nested control's keyboard event could also activate its row. The fix leaves nested controls to handle their own events, while retaining Enter/Space activation on the focused row. A new production build passed, followed by four mailbox/SSH keyboard checks and four subdomain checks. Logs: `/tmp/boron-table-keyboard-build.log` and `/tmp/boron-table-keyboard-tests.log`.
- Four long resource-management scenarios exceeded their aggregate 45-second test limit at differing points. A rerun with a 120-second total scenario limit and unchanged assertions is in progress, logged to `/tmp/boron-resource-final-tests.log`. This is not yet a passing result.
- The isolated backend integration suite for jobs, encrypted storage, file/database recovery, SSH workflows and routing retention is still running in `/tmp/boron-backup-final-core-tests.log`. Inspect its terminal result before claiming verification; do not restart merely because an observation times out.

The table fix requires completion of its remaining resource regression and deployment. Final backend results, full requirement audit and GitHub/release/self-update verification remain outstanding. Cloudflare recovery has no live provider mutation evidence because no provider account is connected.
