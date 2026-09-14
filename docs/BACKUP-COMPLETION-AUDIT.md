# Backup completion audit — 2026-09-14

The requested backup system is implemented and verified. The release and live self-update checks remain separate final gates. Chronological entries in PRODUCT-EXPANSION-GOAL.md contain superseded implementation gaps.

| Requirement | Implementation and verification |
| --- | --- |
| Jobs and scheduling | Persistent destinations, policies, account selection/exclusions and frozen run options; scheduler deduplication tests and both-theme admin/customer workflows passed. |
| Incremental and full scans | Real restic repositories verify unchanged-file reuse, restored bytes and full rereads with chunk deduplication. |
| Filters | Account exclusions, home-relative include paths and filename patterns; real SSH workflow verifies filtered archive contents and source-escape rejection. |
| SSH storage | Dedicated client credentials, pinned host key, SFTP and isolated SSH options; real isolated SSH backup/restore and wrong-key rejection passed. |
| Notifications | Selected email and signed webhook channels; loopback transport tests cover success, failure and duplicate suppression. No external test messages were sent. |
| File restore | Selected paths and all captured account files, tenant isolation, unprivileged application worker and encrypted previous-state copy; restored contents and retained unrelated files verified. |
| Database restore | Actual MariaDB export/import, existing/deleted databases, encrypted credential recovery, previous-state recovery and ownership isolation; integration suite passed. |
| Mailbox restore | Existing/deleted mailboxes, atomic message replacement, encrypted previous-message recovery, restart recovery, undo and cleanup; real IMAP QA and integration tests passed. |
| Retention | Per-policy recovery points and previous-state copies; active/failed recovery and pending displaced cleanup protect required data. Retention, UI/history and shared-repository isolation tests passed. |
| Account settings | PHP versions/limits/extensions, cron and owned DNS restore/undo are wired into the worker and both themes. Live QA verified PHP, cron and authoritative local DNS, then restored the original state. Administrator-only PHP function policy remains separately protected. |
| Email routing | Forwarders, catch-all, autoresponders and exact Sieve scripts; guarded restore/undo, blocked competing edits, automatic rollback and interrupted rollback continuation. Live QA run 8 / restore 12 / undo 13 verified exact recovery and preserved credentials/quota; fixture cleanup passed. |

The complete backend run passed 2,655 tests with two ShellCheck checks skipped because their conditions were collected before installation. Both static checks subsequently passed separately. All 114 browser tests passed. Logs and live evidence are indexed in FINAL-REGRESSION-2026-09-14.md.

Cloudflare-native DNS recovery is implemented and tested with native/legacy record formats and encrypted queue execution using a simulated provider. No Cloudflare account is connected on this server, so live provider mutation is unverified. This does not substitute for the real local/SSH storage and local-DNS recovery evidence, and no live Cloudflare write is claimed.

Portable whole-account backup/migration and cPanel/DirectAdmin import remain in the explicitly subsequent expansion scope.
