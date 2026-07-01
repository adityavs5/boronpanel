# Checkpoint: Phase 2, Feature 7 — Backup system (JetBackup-equivalent)

The largest feature in this phase. Full-featured per the goal, not a stub:
full-account and granular (file/database/mailbox) backup and restore,
local and rclone-backed remote destinations, per-account and server-default
scheduling with retention, async jobs with live progress, a backup
browser, and admin-wide job history.

## Architecture

- **One artifact file per backup, regardless of kind or destination.** A
  "full" backup bundles `manifest.json` (everything needed to recreate the
  account's structure: domains, databases, mail domains/users, cron jobs,
  DNS zone records, resource limits) alongside `home.tar.gz`/
  `databases/*.sql.gz`/`mail/*.tar.gz`, all wrapped in one outer `.tar`.
  Granular backups (file/database/mailbox) are a single `.tar.gz`/`.sql.gz`.
  This uniform model makes storage, transfer, retention, and browsing
  identical regardless of destination kind (local path vs. rclone remote)
  or backup kind — retention is just "delete the oldest artifact files
  past the configured count."
- **Destinations**: local paths are plain directories; rclone-backed
  remotes (S3-compatible, SFTP, Google Drive service-account, or anything
  else rclone supports) are created non-interactively via
  `rclone config create <remote> <type> key=value ...` — credentials live
  entirely in rclone's own config file (`/etc/rclone.conf`), never
  duplicated into Forgehost's DB, the same "secrets live in one
  restricted-permission place" pattern already used for MariaDB/mail/SSL
  credentials elsewhere in this project. Google Drive specifically needs a
  service-account JSON key for non-interactive setup (documented as such);
  full interactive OAuth "connect your Google account" is out of scope.
- **Async jobs, no new job-queue dependency**: `trigger_backup`/
  `trigger_restore` create a DB row (status=`pending`) and submit the
  actual work to a small bounded `ThreadPoolExecutor`
  (`settings.backup_concurrency`, default 2) rather than blocking the RPC
  call — a multi-minute backup never blocks forgehostd's dispatch loop.
  `BackupJob`/`RestoreJob.progress_message` is updated at each stage
  (backing up files/databases/mail, uploading, restoring files/databases/
  mail, recreating account/domains/mail/cron/DNS) and is what the UI polls
  for live progress.
- **Scheduling**: one `BackupSchedule` row per account, or one row with
  `account_id=NULL` for the server-wide default any account without its
  own override falls back to. `scripts/backup_scheduler.py` (system cron,
  hourly) triggers a full backup for any account whose effective schedule
  is due (enough time elapsed since its last completed full backup for
  that schedule's frequency) — mirrors `scripts/usage_snapshot.py`'s
  established pattern (Phase 2 feature 5) exactly.
- **Restore never requires termination first** (explicit goal
  requirement): restoring onto a still-active account overwrites its
  files/databases/mail in place; restoring a terminated account's full
  backup recreates it end-to-end. Granular (file/database/mailbox)
  restores were verified live against a fully active account with no
  termination involved at all.

## Four real bugs found by live testing — all in the "terminated account
restore" path specifically

Every one of these passed its unit tests (which mocked the exact calls
that turned out to be wrong) and only surfaced once actually run against
this live server end-to-end — a strong argument for why the Definition of
Done's live "terminate → restore → site serves again" test mattered more
than the mocked test suite alone.

1. **`create_account`'s "already exists" guard silently blocked account
   reactivation.** Terminating an account never deletes its historical DB
   row (an established, deliberate pattern — see `terminate_account`), so
   calling `create_account` to "recreate" a terminated account always hit
   its own already-exists guard. The restore code caught that
   `RuntimeError` and treated it as a harmless no-op — meaning the Linux
   user was never actually recreated, and the restore failed several steps
   later with `getpwnam(): name not found`. **Fixed** with a new, dedicated
   `handlers_account.reactivate_account()`: finds the existing terminated
   row, recreates the Linux user, and flips the row back to active instead
   of trying to insert a new one.
2. **The same class of bug, one level down, for domains.** `Domain` rows
   also survive termination, so `add_domain`'s own "already in use" guard
   fired for every domain during a restore — but that guard fires *before*
   `add_domain`'s own `ols.provision_vhost()` side effect runs, which the
   restore code again silently swallowed as a no-op. Result: the domain
   correctly showed up in `domain.list` after restore, but the site kept
   404ing, since its OLS vhost was never actually recreated. **Fixed** by
   forcing one unconditional `provision_vhost()` call covering every
   domain in the manifest, regardless of whether its row was newly created
   or pre-existing.
3. **Vhost provisioning was ordered before file restoration.** Fixing bug
   #2 alone still failed: `provision_vhost()` ran immediately after the
   domain-recreation loop, but at that point `home.tar.gz` hadn't been
   extracted yet, so the docroot didn't exist on disk and
   `openlitespeed -t` correctly rejected it ("Path for document root is
   not accessible"). **Fixed** by moving vhost (re)provisioning to *after*
   the files-restoration step.
4. **A plain tar archive doesn't capture POSIX ACLs.** Even with #1–#3
   fixed, the site still 404ed. Root cause: `add_domain`'s early-exit for
   a pre-existing domain also skips `ensure_docroot()`'s "nobody" ACL
   grant (the mechanism OLS's actual worker process needs to read a
   docroot at all, per Phase b's established pattern) — and a plain
   `tar czf` doesn't capture/restore ACL entries either, so the restored
   directory had normal owner/group perms but zero access for "other"
   (which is what OLS's worker, running as `nobody`, falls under).
   Diagnosed with `getfacl`, which showed the grant simply wasn't there.
   **Fixed** by promoting `handlers_domain._ensure_docroot` to a public,
   reusable `ensure_docroot()` and calling it explicitly for every domain
   in the manifest during restore, after files are extracted.

Each of these compounds on the last — a good illustration of why "run the
actual Definition of Done scenario live" surfaces failure modes that
mocked unit tests, however thorough, structurally cannot: every mock in
the test suite was mocking the *correct* function signature, just not
catching that the *sequence/side-effects* around those calls were wrong
for this one specific, easy-to-miss case (restoring onto a row that
already exists but whose on-disk state doesn't match it).

## A fifth, smaller bug: raw SQL error on destination deletion

Deleting a `BackupDestination` with completed jobs recorded against it hit
`BackupJob`'s own foreign key and surfaced a raw, unhelpful
`sqlite3.IntegrityError` instead of a clean explanation — the existing
guard only checked for referencing `BackupSchedule` rows, not `BackupJob`
rows. Fixed with the same "never delete, keep history" reasoning already
applied to Account/Domain rows throughout this project: a destination with
job history can't be deleted while any job still references it (those are
restorable backup points, not disposable state).

## Real end-to-end verification performed

Full scenario, exactly per the Definition of Done:

1. Created a real account with a domain, database (with a real row
   inserted), mail domain + mailbox (with a real message delivered), and a
   cron job.
2. Created a real local backup destination, triggered a full backup via
   the REST API, polled for completion.
3. Independently inspected the resulting `.tar` artifact with plain `tar`/
   `python3 -m json.tool` (not through Forgehost's own code) — confirmed
   `manifest.json` accurately captured every domain/database/mail user/
   cron job, and `home.tar.gz`/`databases/*.sql.gz`/`mail/*.tar.gz` were
   all present.
4. Verified the browse API returns the same manifest + archive member
   list.
5. **Terminated the account** — confirmed the site 404s, the home
   directory and database are gone.
6. **Triggered a full restore from the backup point** — found and fixed
   the four bugs above across several iterations of this exact step.
7. **Confirmed the site serves the original content again** (`HTTP 200`,
   exact original body), the database row is back, the mail message is
   back, the cron job is back, and the account's cgroup slice is active
   again.
8. Repeated the full terminate → restore cycle one more time end-to-end
   after all fixes, cleanly, to confirm the fixes actually compose
   correctly together rather than just individually.
9. **Granular restores against a still-active account (no termination
   involved)**: corrupted a file, database row, and deleted a mail
   message independently; restored each via its own individual backup
   point; confirmed each came back correctly without touching anything
   else.
10. **rclone destination**: created a destination using rclone's own
    `local` backend type (to exercise the real `rclone config create`/
    `rclone copyto` code path without needing real cloud credentials),
    triggered a database backup to it, confirmed the artifact actually
    landed via rclone's copy mechanism.
11. Full 306-test suite (298 existing + additional backup/restore
    regression tests written after each bug found) passing.

## What's untested

- A genuine cloud destination (real S3/SFTP/Google Drive credentials) —
  the rclone code path itself was exercised via its `local` backend type,
  which is functionally identical from Forgehost's side (same
  `rclone config create`/`copyto`/`copyto`-from-remote calls), but no real
  cloud round-trip was performed in this pass.
- The scheduler's actual hourly cron firing in production (verified the
  script runs cleanly and its due-date logic is unit-tested, but didn't
  wait a real hour to observe a live scheduled trigger fire).
- Very large accounts: `mysqldump`/`tar` calls run synchronously inside a
  background thread via this project's existing `run()` helper
  (`subprocess.run(capture_output=True)`), which holds the entire dump in
  memory before writing it out gzip-compressed — consistent with how
  every other subprocess call in this project already works, but a
  genuinely huge database could be a real memory-scaling concern; not
  hit at this project's tested scale, flagged rather than solved here.
- Concurrent backup/restore operations on the *same* account (the bounded
  thread pool allows different accounts' jobs to run in parallel, but two
  simultaneous jobs for one account could race on the same staging
  directory naming scheme in edge cases — not exercised).
