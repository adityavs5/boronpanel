# Checkpoint: Phase 3, Feature 4 — Email forwarders, autoresponders, catch-all

## What was built

- **Forwarders**: routed through Postfix's `virtual_alias_maps` (SQL-
  backed, `proxy:mysql:`), wired for the first time in this project --
  Phase e only ever set up `virtual_mailbox_domains`/`virtual_mailbox_maps`
  (real mailbox delivery), never aliasing. New `mail_forward` table in
  `forgehost_mail` (`daemon/mail.py`'s `create_forward`/`delete_forward`/
  `list_forwards`); routine CRUD needs no Postfix/Dovecot reload, the
  same "SQL row change takes effect on the next lookup" property every
  other mail CRUD in this project already has. A forwarder does not
  require (or preclude) a real mailbox at the same address -- Postfix
  checks `virtual_alias_maps` *before* `virtual_mailbox_maps`, so if both
  exist the forward wins (documented in the UI, not a "forward + keep a
  copy" model, which the goal didn't ask for).
- **Catch-all**: new `mail_catchall` table (one row per domain, `UNIQUE`
  on `domain_id` enforces "at most one"). A single SQL view (a `UNION` of
  three branches inside one Postfix lookup query,
  `/etc/postfix/forgehost/mysql-virtual-forwards.cf`) answers all three
  shapes Postfix ever queries for one recipient: the full address
  (forwarders), the full address again but only for existing real
  mailboxes *when a catch-all is active* (see bug #1 below), and the
  bare `@domain` fallback (the actual catch-all).
- **Autoresponders**: Dovecot's own Pigeonhole `vacation` Sieve
  extension, **not** the classic `vacation(1)` Unix binary (which needs
  a real system account + `.forward` file + its own database -- none of
  which fits this project's virtual, non-Unix-account mailbox model).
  `daemon/autoresponder.py` renders a per-mailbox `.dovecot.sieve` script
  (subject/body/optional date range), validates it with Pigeonhole's own
  `sievec` compiler before writing it into the mailbox's Maildir home
  (this project's usual validate-before-apply discipline, without
  needing `daemon/configtx.py`'s shared machinery -- there's no service
  to reload; Dovecot recompiles/reloads a mailbox's active script on its
  own at the next delivery). New `mail_autoresponder` table is
  bookkeeping/display state only; enforcement lives entirely in the
  Sieve file. Dovecot's own loop-prevention (`:days 1`, tracked
  per-sender) came for free rather than needing to be hand-rolled.
- **API**: `GET`/`POST`/`DELETE` at `/accounts/{u}/domains/{d}/email/
  {forwarders,catchall}`, `GET`/`POST`/`DELETE` at `.../autoresponders`
  (per the goal). **UI**: one combined page
  (`email_features.html`, linked from the mail-domain page) covering all
  three, since they're all facets of "how mail for this domain is
  routed/answered."

## Three real bugs found by live testing

1. **Catch-all silently intercepted mail for real, existing mailboxes.**
   The first version of the Postfix lookup query only had two branches
   (forwarders, catch-all). Live test: with a catch-all enabled, mail
   sent to `john@domain` -- a real mailbox that had *nothing* to do with
   the catch-all -- was rewritten to the catch-all destination instead
   of being delivered to john's own inbox
   (`to=<target@domain>, orig_to=<john@domain>` in the mail log). Root
   cause: this is Postfix's actual, documented behavior --
   `virtual_alias_maps` is consulted independently of
   `virtual_mailbox_maps`, and Postfix's own fallback-to-`@domain`
   lookup fires whenever the full-address lookup returns nothing,
   regardless of whether that address is *also* a real mailbox
   elsewhere. Postfix's own `VIRTUAL_README` explicitly warns about this
   exact trap for catch-all setups. **Fixed** by adding a third query
   branch: when a catch-all is active for a domain, every real mailbox
   address in that domain gets an explicit self-referential alias
   (`john@domain -> john@domain`) in the *same* lookup -- Postfix's
   full-address lookup then matches that self-alias first (more
   specific than the bare `@domain` fallback) and stops there, leaving
   normal mailbox delivery undisturbed. Inactive-without-a-catch-all
   domains are unaffected (this branch only ever matches rows joined
   through an active `mail_catchall` row).
2. **A freshly created mailbox has no home directory yet, so setting an
   autoresponder on it immediately failed.** Dovecot creates a mailbox's
   Maildir lazily, at first delivery or first login -- not at
   mailbox-creation time. `apply_autoresponder` originally required the
   mailbox's home directory to already exist, which is a perfectly
   reasonable thing to want to do *before* any mail has ever arrived
   ("set my out-of-office before I leave"). **Fixed** by having
   `apply_autoresponder` create the mailbox's home directory itself
   (vmail:vmail, mode 700) if missing, only requiring that the *mail
   domain* itself is provisioned.
3. **The autoresponder silently never fired for a real test message.**
   Dovecot logged `vacation action: discarding vacation response ... no
   known (envelope) recipient address found in message headers` for a
   message with no `To:` header at all matching the mailbox's address
   verbatim -- RFC 5230's default anti-loop safety check. **Fixed** by
   adding an explicit `:addresses ["<local_part>@<domain>"]` clause to
   the generated Sieve script, telling Dovecot's vacation extension this
   response is valid for this mailbox regardless of how literally the
   headers spell out the recipient (covers mail that arrived via a
   forwarder rule pointed at this mailbox, BCC, etc., not just the exact
   literal case already caught above).

## Real end-to-end mail delivery testing performed (not just API calls)

Using Postfix's own `sendmail` wrapper to inject real messages into the
real mail queue (not test mocks), then reading the real Postfix/Dovecot
logs and the real Maildir contents on disk:

1. **Forwarder**: sent to `sales@domain` (a forwarder to
   `target@domain`) -- confirmed in the mail log
   (`to=<target@domain>, orig_to=<sales@domain>`) and the message
   physically present in `target`'s Maildir.
2. **Catch-all**: sent to a nonexistent address at the domain --
   confirmed delivered to the catch-all destination the same way.
3. **Catch-all vs. real mailbox** (the bug above): sent to `john@domain`
   (a real mailbox) while a catch-all was active -- confirmed delivered
   to john's own inbox, not the catch-all, after the fix.
4. **Autoresponder**: sent a message with a proper `To:` header to a
   mailbox with an always-on autoresponder -- confirmed
   `vacation action: sent vacation response to <sender>` in the log,
   and the original message still landed in the real inbox (auto-reply
   doesn't replace normal delivery).
5. **Duplicate-reply suppression**: sent a second message from the same
   sender within the same day -- confirmed
   `discarded duplicate vacation response` (Dovecot's own `:days 1`
   throttle, not something this project implemented itself).
6. **Date-range exclusion**: set an autoresponder with a date range
   entirely in the past -- confirmed a new message delivered normally
   with **no** vacation log line at all (correctly inactive outside its
   configured range).
7. Deleted the forwarder/catch-all/autoresponder via the RPC layer,
   confirmed each is gone (`mail.forward.list` empty, `mail.catchall.get`
   returns `null`, the `.dovecot.sieve` file removed from disk).
8. Exercised the full UI page (`/ui/accounts/{u}/domains/{d}/email`)
   through real HTTP: add a forwarder, set an autoresponder, reload the
   page and confirm both show up correctly rendered.
9. `account.terminate` confirmed the whole `/var/vmail/<domain>` tree
   (mailboxes, forwarders' underlying rows via FK cascade, autoresponder
   Sieve files) is gone afterward.

## Testing

`tests/test_autoresponder.py` (new, 14 tests): Sieve rendering/validation
for every date-range shape (none/start-only/end-only/both), quote
escaping, dot-stuffing, the `:addresses` clause, invalid-script
rejection, lazy mailbox-home creation, and idempotent removal -- all
against the real `sievec` compiler (fast, offline, no root needed, same
"mock system/network, not pure local computation" split as
`test_dkim.py`). `tests/test_handlers_mail.py` extended (19 new tests)
for forwarder/catch-all/autoresponder CRUD, destination-format
validation, and date-range validation, mocking `daemon.mail`'s SQL calls
and `daemon.autoresponder`'s file I/O (this project's established
"forgehost_mail SQL isn't touched directly by the unit suite" pattern --
no test MariaDB fixture exists, matching how existing mailbox/domain
tests already work). 376 tests passing (up from 353).

## What's untested / explicitly out of scope

- Genuine external-internet delivery (a real Gmail/Outlook address) --
  this sandbox's outbound network characteristics for this kind of test
  weren't explored; forwarding/catch-all were verified end-to-end using
  a second real mailbox on a different domain on this same server as the
  "external" target, which exercises the identical Postfix rewriting
  mechanism a real external address would (Postfix doesn't distinguish
  "local" vs. "external" until its own transport-routing step, well
  after the `virtual_alias_maps` rewrite this feature is responsible
  for).
- Forwarding to multiple destinations for one address in a single
  delivery (the schema/query support it via `GROUP_CONCAT`; not
  exercised live, only reasoned about from the query's own design).
- A concurrent redemption/race on the same autoresponder file (single
  daemon, single writer per mailbox in practice -- not stress-tested).
