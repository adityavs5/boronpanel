# Backup completion audit — 2026-09-14

The backup requirement remains open. This is the current capability audit;
chronological notes in PRODUCT-EXPANSION-GOAL.md include superseded limitations.

| Requirement | Current implementation and evidence | Remaining work |
| --- | --- | --- |
| Reusable jobs, schedules and account selection | snapshot_jobs persists destinations, policies and frozen per-account runs; scheduler queues due policies. Job tests cover schedule deduplication and frozen options. | Final admin/customer workflow audit. |
| Incremental and full scans | Real restic tests compare unchanged-file reuse and restored bytes. Full mode rereads files while retaining chunk deduplication. | No storage implementation gap identified. |
| Include/exclude filters | Account exclusions, included home-relative paths and filename patterns are applied by the job worker; source escapes fail. | End-to-end SSH job test now covers actual filtered archive contents. |
| SSH destinations | Dedicated generated client key, pinned server host key, SFTP storage, isolated connection options. Real SSH tests cover restoration and host-key rejection. | Final shared-fixture regression result is recorded in the product checkpoint. |
| Notification channels | Selected email and signed webhook channels use account preferences/settings. | End-to-end loopback transport test covers success/failure and duplicate suppression; no external messages are sent. |
| File restoration | Account-scoped selected file/directory restores, unprivileged application worker and encrypted pre-restore safety copy. | Included in SSH job workflow proof. |
| Database restoration | Actual SQL export/import, encrypted credential metadata, existing/deleted database handling, previous-version recovery and ownership isolation. | Final broad regression. |
| Mailbox restoration | Existing/deleted mailbox handling, guarded atomic switch, encrypted previous-message recovery, restart recovery, undo, and completed-work cleanup. | Final broad regression. |
| Retention | Policy snapshots and pre-restore copies, active/failed recovery protection, shared repository ownership. Pending displaced cleanup protects its encrypted copy. | Final UI/history audit. |
| Account configuration recovery | The config component captures domain/PHP metadata, cron jobs and owned DNS zones in manifest.json. | **Partially complete:** scheduled-task restore/undo is wired into the worker and snapshot dialog and verified live. DNS/PHP settings recovery remains incomplete. |
| Mail routing recovery | Private mail metadata captures forwarding, catch-all and autoresponder records. | **Incomplete:** current mailbox restore deliberately restores mailbox accounts/messages, not these routing records. |

Remaining DNS/PHP configuration recovery and mail routing recovery must be addressed before calling
this a complete backup product. The separate queued portable whole-account
backup/import expansion is still subsequent work; it is not substituted for these
current recovery gaps.
