# Checkpoint: Phase 4 feature 1 — SpamAssassin

## What was built

- **SpamAssassin installed and wired into Postfix's inbound path only.**
  `content_filter=scan:` is set as a **per-service master.cf override** on
  the `smtp inet` line (mail arriving from the internet) and deliberately
  **not** on `submission` (mail customers send *through* Forgehost) or as
  a `main.cf` global — a global would also re-scan the message spamc's
  own `-e sendmail` reinjects after scanning, looping forever; scoping it
  to one inbound service line avoids that without any special-casing.
- **Per-domain enable/disable + custom threshold, without a second
  scanning path.** Both collapse into SpamAssassin's own `required_score`,
  looked up per-recipient via spamd's `--virtual-config-dir=
  /etc/forgehost/spamassassin/%d` (spamd's own documented mechanism for
  recipients with no real Unix account — the same problem shape Phase 3
  feature 4's autoresponder Sieve already solved for vacation replies).
  "Disabled" sets `required_score` to 1000 (no real message reaches it) —
  the message is still scanned (X-Spam-Status stays visible) but never
  *acted on*. Settings are per **mail domain**, matching this project's
  existing catchall/forwarder/autoresponder API shape, not per mailbox.
- **One global Dovecot Sieve script** (`sieve_before`, runs before every
  mailbox's own personal script), the only place the actual Junk-filing
  decision lives: `if header :contains "X-Spam-Flag" "YES" { fileinto
  :create "Junk"; stop; }`. Identical for every mailbox — the interesting
  per-account behavior already happened at scan time.
- **Global admin default threshold** (`SpamGlobalSettings`, single row)
  feeds `/etc/spamassassin/local.cf`'s `required_score` — used by any
  domain that hasn't set its own override (no per-domain prefs file is
  written for that case at all, so a later change to the global default
  is picked up automatically).
- **API**: `GET`/`PATCH /accounts/{u}/domains/{d}/email/spam-filter`
  (matches the goal's literal shape), plus an admin-only
  `GET`/`PATCH /api/v1/spamfilter/global-default`. UI: a new "Spam
  filter" card on the existing email-features page (enable checkbox +
  optional threshold override).
- **One-time bootstrap** (`system.bootstrap_spamassassin` RPC op, same
  pattern as `system.bootstrap_ols`/`bootstrap_pma`): writes spamd's
  `/etc/default/spamd` OPTIONS, `local.cf`, adds the Postfix `scan` pipe
  service + content_filter override (validated via a scratch-copy
  `postfix check`, ARCHITECTURE.md SS7), and wires `sieve_before` into
  Dovecot's `90-sieve.conf` (validated via `doveconf -n -c`) — both
  through `daemon/configtx.py`'s existing validate/backup/apply/reload/
  verify/rollback engine, idempotent (safe to re-run).

## Testing

`tests/test_spamfilter.py` (new, 21 tests): per-domain prefs-file
rendering (enabled/disabled/default, including the disabled-wins-over-a-
stale-override case), a real `sievec` compile of the global script (fast,
offline, same discipline as Phase 3's autoresponder tests), and the
`master.cf` text transform in isolation — including a **real bug caught
by the idempotency test**: `_render_master_cf` duplicated the
`content_filter` override when called twice against the same content
(the earlier IF-check only tracked state within a single call, not
whether the very next line already had it) — fixed by peeking at the
following line before appending. `tests/test_handlers_mail.py` (+5): the
`get_spam_filter`/`set_spam_filter` handlers against a real SQLite
`MailDomain` row. 541 tests passing (up from 515 after the IDOR
checkpoint).

## Live verification performed (the actual Definition of Done)

1. Ran the real bootstrap op against this server: confirmed
   `master.cf` gained the `scan` service + `-o content_filter=scan:`
   only on `smtp inet` (not `submission`, not the outbound `smtp unix`
   relay transport), `90-sieve.conf` gained `sieve_before`, the global
   sieve script compiled in place, `spamd`/`postfix`/`dovecot` all
   confirmed active after reload.
2. **Real GTUBE test**: created a real account/domain/mailbox, sent a
   real GTUBE message via a genuine SMTP session to port 25 (not a
   direct RPC/mock). Mail log confirms the full real pipeline:
   `spamd: identified spam (999.8/5.0) ... GTUBE`, Postfix
   `relay=scan ... delivered via scan service`, Dovecot
   `sieve: fileinto action: stored mail into mailbox 'Junk'`. The
   message's own headers confirm both halves of the goal's "tag in
   subject or move to Junk" independently: `X-Spam-Flag: YES`,
   `Subject: [SPAM] GTUBE test`, delivered into `.Junk`, Inbox empty (no
   double-delivery).
3. **Real disable-toggle test** (the feature-specific behavior, not just
   SpamAssassin's own default): called `mail.spamfilter.set` with
   `enabled=False` for the same domain, confirmed the prefs file changed
   to `required_score 1000.0`, then sent the identical GTUBE message
   again — landed in the **Inbox**, unflagged
   (`X-Spam-Status: No, score=999.8 required=1000.0`, subject
   unmodified) — proving the message is still genuinely scanned (same
   999.8 score both times) but the disable toggle correctly suppresses
   the action, not the scan.
4. Terminated the test account; confirmed
   `/etc/forgehost/spamassassin/<domain>` was removed by the
   termination hook (no orphaned per-domain state left behind).

## A real gap in the live database, fixed as part of deploying this feature

`shared/models.py`'s `MailDomain` model gained two new columns
(`spam_filter_enabled`, `spam_filter_threshold`). This project's
`Base.metadata.create_all()` only creates *new* tables, never adds
columns to existing ones (a gap already flagged twice before — Phase
2's cgroups review, and again here) — so the live
`/var/lib/forgehost/forgehost.db`'s `mail_domains_cache` table needed a
manual `ALTER TABLE ... ADD COLUMN` before the deployed code could use
it, done here the same way Phase 2 feature 6 did it. The new
`spam_global_settings` table (a genuinely new table) was picked up by
`create_all()` automatically on the next daemon restart, no manual step
needed for that one. **Third occurrence of this exact gap now** — worth
promoting from "worth deciding" to "should actually adopt Alembic" in
STATUS.md's review priorities.

## What's untested / explicitly out of scope

- SpamAssassin's Bayesian learning / `sa-learn` (mark-as-spam/ham
  feedback loop) — not asked for in the goal, not built.
- DNS-block-list-based tests (SpamAssassin's stock ruleset includes
  several) were not individually exercised — GTUBE is a deliberate,
  guaranteed, standard trigger specifically because it doesn't depend on
  network reachability/blocklist state, matching how this sandbox's
  network characteristics were already documented as inconsistent in
  earlier phases (Phase 3's WordPress download-speed finding).
- A message addressed to a mailbox whose `.Junk` folder doesn't already
  exist was exercised implicitly (this test account's `.Junk` didn't
  exist beforehand and was auto-created by Sieve's `:create` — confirmed
  in the live test above) but not as a dedicated separate test case.
- Concurrent spam-filter setting changes for the same domain — not
  stress-tested (matches this project's existing, documented low-risk
  posture on concurrent RPC calls against the same resource).
