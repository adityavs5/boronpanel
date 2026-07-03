# Checkpoint: Phase 4 pre-work — plaintext password log audit

Mandatory pre-work per the Phase 4 goal, done before any of the 12 numbered
features: "check daemon logs for plaintext passwords, redact/rotate any
found."

## What was found

Phase 3's own checkpoint (`CHECKPOINT-phase3-10.md`) documented finding and
fixing a bug where `daemon/mail.py`'s `hash_password()` passed the plaintext
mailbox password as a `doveadm pw -s ARGON2ID -p <password>` command-line
argument, which `daemon/procutil.py`'s `run()` unconditionally logs in full
(`logger.info("exec: %s", " ".join(args))`). That fix (switching to
`doveadm`'s stdin mode) is real and correctly in place in both the source
tree and the deployed copy — confirmed by reading `hash_password()` directly,
not just trusting the checkpoint's prose.

**However, a live grep of `/var/log/forgehost/daemon.log` found the exact
same `doveadm pw -s ARGON2ID -p <plaintext>` pattern still present, 15
times, spanning 2026-06-30 21:52 through 2026-07-01 12:04** — hours *after*
the checkpoint's stated fix. Comparing file mtimes against timestamps
resolved the apparent contradiction: the fixed code was written to disk at
13:38 the same day, and the daemon process serving requests before that
point was still running the *old*, pre-fix code already loaded in memory
(this project's earlier tests spun up fresh `forgehostd` processes
per-test-run rather than always going through a persistent systemd service
with a restart-on-deploy step). The fix is genuinely effective from
`13:52:31` onward (confirmed: log lines from that point on show
`doveadm pw -s ARGON2ID` with no `-p` at all, the stdin form) — but 15
historical plaintext exposures from before that point were sitting in the
log file.

## Root cause fix (not just cleanup): the leak path can recur for *any* future subprocess call, and half of it (journald) can't be redacted after the fact

Fixing `hash_password()` again was unnecessary (it's already fixed). The
actual gap: `daemon/server.py` configured the root logger with **both** a
`FileHandler(daemon.log)` **and** a `StreamHandler()`, and `forgehostd.proc`
(the logger that prints every subprocess's full argument list) propagates to
root by default — so *any* future call site that puts a secret in `argv`
instead of `input_text` (the exact mistake `hash_password()` made once
already) leaks it not only to `daemon.log` (a plain file we can inspect and
redact, as this checkpoint does below) but *also*, via the `StreamHandler`,
to whatever captures the daemon's stdout — for a systemd service with no
`StandardOutput=` override, that's the systemd journal. journald's storage
is append-only and per-entry checksummed; there is no supported way to edit
or delete a single entry out of a `.journal` file, only whole-file
deletion/vacuuming. So the *first* time this bug reintroduces itself, the
resulting leak would be permanently unredactable from the journal even after
the code is fixed again.

**Fix**: `daemon/server.py` now has a `configure_logging(log_dir)` function
(moved out of module scope, into `main()`, so importing `daemon.server` for
its `OP_TABLE`/handlers has no filesystem side effects and needs no
`/var/log/forgehost` to exist — a prerequisite for testing this without
root). It explicitly detaches `forgehostd.proc` from the root logger
(`propagate = False`) and gives it its own `FileHandler` only, no
`StreamHandler`. Every other logger (`forgehostd`, `forgehostd.dkim`,
`forgehostd.cgroups`, etc.) is unaffected and still reaches both
`daemon.log` and the journal exactly as before — this closes the one
channel that actually caused the exposure, without losing any lifecycle/
error visibility for anything else. This is a durable fix: even if a future
subprocess call site reintroduces a secret in `argv`, it can now only ever
reach the one file Forgehost already treats as sensitive and controls
directly, never the un-redactable journal.

## Testing

`tests/test_daemon_logging.py` (new, 4 tests): calls `configure_logging()`
directly against a `tmp_path` (never touching the real `/var/log/forgehost`,
consistent with this project's "no root/live service required" test
philosophy) and asserts `forgehostd.proc.propagate is False`, that its only
handler is a `FileHandler` (never a `StreamHandler`), that a logged message
actually reaches the file, and that calling `configure_logging()` twice
(as could happen under `Restart=on-failure`) doesn't accumulate duplicate
handlers. 499 tests passing (up from 495 at the end of Phase 3).

## Remediation performed on the live server

1. **`/var/log/forgehost/daemon.log`**: all 15 plaintext-password lines
   redacted in place (password value replaced with
   `***REDACTED-PHASE4-AUDIT***`; everything else on each line, including
   the timestamp and the fact that a hash operation occurred, left intact
   for audit continuity). Verified after: zero lines anywhere in the file
   still contain a `doveadm pw -s ARGON2ID -p <anything-but-the-redaction-
   marker>` pattern. File permissions/ownership (`root:root 0644`, matching
   the original) are unchanged — same inode, truncated and rewritten, not
   replaced.
2. **Credential rotation**: checked whether any of the 15 exposed passwords
   still protects a live resource. **None do** — every account name
   appearing in the surrounding log context (`mailtest`, `mailtest2`,
   `webuitest`, `e2efinal`, `webmailtest`, `backuptest`, `bktest`,
   `p3mailtest`, `p3mailui`) is `terminated` in the control-plane DB, with
   its Linux user, home directory, and `/var/vmail/<domain>` Maildir already
   removed by that termination's own teardown sequence (confirmed
   independently via `getent passwd` for each name -- all gone -- and via
   the accounts table, all rows `status='terminated'`). There is nothing
   left to rotate; the exposure is purely historical.
3. **`journald`**: **not fully purged — a deliberate, documented decision,
   not an oversight.** All 15 lines live in a single active
   `system.journal` file for this boot (it has not yet auto-rotated by
   size/time since this VM's build began). That one file also contains this
   entire boot's *complete* systemd journal — 34,652 lines total, of which
   15,559 are unrelated, genuinely security-relevant SSH activity
   (`sshd` connection/auth log lines against this box's real public IP,
   including real unsolicited external connection attempts) — journald has
   no supported mechanism to delete a single entry from a `.journal` file,
   only whole-file vacuuming. Deleting or vacuuming this file to purge 15
   already-defunct test passwords would destroy roughly 2,300x as much
   legitimate audit history for a security benefit that's already fully
   realized elsewhere: every account the leaked passwords belonged to is
   confirmed terminated (point 2 above), so no live credential is protected
   by leaving these 15 lines in place, and root-only journal access is the
   same trust boundary `daemon.log` itself sits behind. **Weighed
   deliberately and decided against wholesale deletion** in favor of (a)
   the root-cause fix above, which prevents any *new* entry of this kind
   from ever reaching the journal again, and (b) this explicit writeup, in
   keeping with this project's standing practice of surfacing an honest
   open finding (e.g. STATUS.md's DNS-01 delegation-chain finding) rather
   than silently declaring full remediation when it isn't the actual
   tradeoff made.

## What's still true / untested

- If an operator's threat model requires zero plaintext-password residue in
  *any* log, including journald, at any point in this VM's history, the only
  available remedy is `journalctl --rotate && journalctl --vacuum-time=1s`
  (or an equivalent full vacuum) accepting the loss of this boot's entire
  SSH/systemd audit trail. Not done here for the reasons above; flagged for
  the operator to decide, not decided silently on their behalf.
- The same class of bug (a secret in `argv` instead of `input_text`) is now
  structurally contained to `daemon.log` only, but is not statically
  prevented — `daemon/procutil.py`'s `run()` still has no linting/allowlist
  that would catch a *future* call site doing this before it ships. Worth
  a lint rule or code-review checklist item, not built here (out of this
  audit's scope, which was to check for and remediate an existing leak, not
  to add new static-analysis tooling).
