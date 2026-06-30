# Checkpoint: Phase e — mail via Postfix/Dovecot

## What was built

- Real system configuration applied to this VM (structural, server-wide,
  not regenerated per mailbox — RESEARCH.md §6's whole point is that
  routine mailbox CRUD needs none of this once it's set up):
  - `forgehost_mail` MariaDB schema (`mail_domain`, `mail_user`, the latter
    `ON DELETE CASCADE` from the former) plus a dedicated SELECT-only
    `forgehost_mailro` user, used as the embedded credential in Postfix's
    and Dovecot's own config files (RESEARCH.md §6, the ISPConfig pattern).
  - Postfix: `virtual_mailbox_domains`/`virtual_mailbox_maps` as
    `proxy:mysql:` lookups against `forgehost_mail`, LMTP transport to
    Dovecot, SASL auth delegated to Dovecot.
  - Dovecot: SQL passdb/userdb (`dovecot-sql.conf.ext`) against the same
    schema, `ARGON2ID` as the default password scheme, Maildir storage,
    LMTP + auth Unix sockets exposed into Postfix's chroot
    (`/var/spool/postfix/private/`).
  - A dedicated `vmail` system account owns all Maildir storage under
    `/var/vmail/<domain>/<local-part>/` — no per-mailbox Linux users.
- `daemon/mail.py` — `forgehost_mail` schema operations (mail domain/
  mailbox CRUD, `doveadm pw -s ARGON2ID` for hashing rather than
  reimplementing crypt(), RESEARCH.md §6).
- `daemon/handlers_mail.py` — `mail.create_domain`/`delete_domain`/
  `create_mailbox`/`delete_mailbox`/`list_mailboxes`/`change_password`, a
  SQLite cache mirror (same pattern as DNS zones, ARCHITECTURE.md §4), and
  a `terminate_account_mail` hook.
- 14 new unit tests, 106 total passing.

## Two real bugs found by live testing, not by reasoning about it beforehand

1. **SQLite cache FK ordering.** Deleting a `MailDomain` cache row while its
   child `MailUser` cache rows still existed failed with `FOREIGN KEY
   constraint failed` — `session.delete()` on both rows in the same flush
   doesn't get ordered correctly without an ORM-level `relationship()`
   (only raw `ForeignKey` columns exist between the two models). Fixed by
   deleting children and calling `session.flush()` before deleting the
   parent, in a shared `_delete_mail_domain_cache` helper used by both
   `delete_mail_domain` and `terminate_account_mail`. A test
   (`test_terminate_account_drops_mail_domains_and_mailboxes`) caught this
   immediately — it was originally written expecting the naive approach to
   work, and failed against the real SQLite engine with `PRAGMA
   foreign_keys=ON`.
2. **`vmail`'s uid placement, twice.** First attempt put `vmail` at uid
   30000, reasoning it would never collide with hosting-account uids
   (which start at 1000). This was wrong in a way unit tests couldn't have
   caught: this Ubuntu's `useradd` allocates each *new* uid as
   highest-existing-plus-one, not the lowest free gap — so the very next
   hosting account created after `vmail` got uid **30001**, not 1000+.
   Caught immediately by a real `account.create` call and reading the
   returned `uid` field. Fix: `vmail` belongs in the low **system** uid
   range (under `SYS_UID_MAX`, here 999 — landed on uid/gid **150**,
   matching where `mysql`/`postfix`/`dovecot` themselves already live),
   confirmed by testing that a subsequent `account.create` correctly
   produced uid 1000 again. That fix then surfaced bug **2b**: Dovecot's
   own `first_valid_uid` safety floor (default 500, meant to stop mail
   being delivered to system/daemon accounts) rejected uid 150 outright —
   `Mail access for users with UID 150 not permitted`. This is Dovecot
   correctly doing its job against a now-intentional low uid; fixed by
   setting `first_valid_uid`/`last_valid_gid` to exactly 150 in
   `10-mail.conf`, since every mailbox in this design resolves to that
   exact uid/gid with no variance. `daemon/mail.py`'s `VMAIL_UID`/
   `VMAIL_GID` constants carry a comment explaining this so the mistake
   isn't repeated when the values are touched again.

## Real end-to-end verification performed

The complete mail pipeline was exercised for real, not mocked, in this
order: `account.create` → `mail.create_domain` → `mail.create_mailbox` →
`doveadm auth test` (real Dovecot auth, real ARGON2ID hash verification) →
a **real SMTP send** via Python's `smtplib` to `127.0.0.1:25` → confirmed
in `/var/log/mail.log` that Postfix queued it, handed it to Dovecot over
LMTP, and Dovecot logged `saved mail to INBOX` → confirmed the actual
Maildir file appeared on disk under `/var/vmail/.../john/new/` → **real
IMAP login and fetch** via Python's `imaplib` against `127.0.0.1:143`,
confirming the exact message (matching `Subject:` header) was retrievable.
Then `account.terminate` → re-ran `doveadm auth test` (now fails, as
expected) → confirmed the Maildir directory, the `forgehost_mail` MariaDB
rows, and the SQLite cache rows are all gone.

## What's untested / explicitly deferred

- TLS for IMAP/SMTP submission (port 587/993 with STARTTLS) uses the
  system's existing self-signed cert; per-domain SSL for mail specifically
  wasn't configured (mail TLS is host-wide, not per-vhost, so this is
  lower-priority than Phase f's per-website SSL).
- DKIM/SPF/DMARC automation: confirmed in RESEARCH.md §6 as acceptable to
  skip for v1 (ISPConfig itself ships with none) — not built.
- No mailbox-level quota enforcement test (the `quota_rule` is set in
  Dovecot's `user_query` via `quota_mb`, but no test sent enough mail to
  actually hit a quota limit and confirm Dovecot rejects further delivery).
- Roundcube itself was not installed (explicitly out of scope: "link out to
  Roundcube, don't build webmail") — `webmail_url` in `forgehost.toml` is
  the link-out point the admin UI will use in Phase h.

## What to review first on wake-up

- The `vmail` uid history (30000 → 150) is exactly the kind of thing that
  looks like a typo if read out of context — the comment in
  `daemon/mail.py` and this checkpoint exist so a future change to that
  constant doesn't accidentally reintroduce either bug.
