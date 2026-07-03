# Phase 5 feature 3: mail queue viewer

## What was built

- `daemon/mailqueue.py`: parses real `mailq`/`postqueue -p` text output
  (never Postfix's internal queue file format) into structured rows --
  one row per (queue entry x recipient), since a single queued message
  can have several recipients. Fields: queue ID, status
  (`active`/`held`/`deferred`, from the `*`/`!`/no-flag suffix on the
  queue ID), sender, recipient, size, arrival time (year inferred --
  `mailq` prints no year), age in seconds, and defer reason (the
  parenthesized line between the header and the recipient list, when
  present).
- Actions: `flush_message`/`flush_all` (`postqueue -i <id>` /
  `postqueue -f`), `delete_message`/`delete_all` (`postsuper -d <id>` /
  `postsuper -d ALL`) -- the same two real Postfix CLI tools an operator
  would use by hand, run via `daemon/procutil.run()` (argument list only,
  never a shell string). Queue IDs are validated against a strict
  alphanumeric shape before ever reaching a subprocess argv, the same
  discipline as every other externally-influenced value in this project.
- `search` parameter does substring matching against sender/recipient in
  Python after parsing -- no extra `mailq` flag needed, since `mailq`
  itself has no server-side filtering options.
- RPC ops `mailqueue.list/flush/flush_all/delete/delete_all`, registered
  in `daemon/server.py` (`mailqueue.list` added to the F7
  `REPORTING_EXECUTOR` pool, same reasoning as `health.get`/
  `services.status`).
- `api/routers/mailqueue.py` (`/api/v1/mail-queue`, `/ui/mail-queue`,
  admin-only) + `mail_queue.html` (search box, flush-all/delete-all,
  per-row flush/delete).

## Real bugs / decisions found by live testing

- **The header line's queue-ID flag is genuinely optional** (`*` =
  active/in-delivery, `!` = held, nothing = deferred) -- written into the
  parser's regex as `(?P<flag>[*!])?` from the start based on reading
  Postfix's own `mailq(1)` documentation, then *confirmed* against this
  server's real output for all three states before trusting it (an
  actively-delivering message really did show `*`; the same message,
  once it failed and became deferred, showed no flag at all).
- **Constructing a genuinely deferred (not immediately bounced) test
  message took real trial and error**, documented here since it's a
  reusable lesson for testing mail features in this sandbox generally:
  a recipient at a syntactically-nonexistent DNS name (e.g.
  `nonexistent-domain.invalid`) bounces *immediately* (NXDOMAIN is a fast,
  permanent failure, dsn=5.4.4) -- it never sits in the queue long enough
  to inspect. A message addressed to an IP-literal recipient
  (`nobody@[203.0.113.1]`, a real RFC 5737 TEST-NET-3 address that is
  guaranteed non-routable) instead attempts a real TCP connection that
  times out after Postfix's real, unmodified `smtp_connect_timeout`
  (30s) -- producing a real `(connect to ...: Connection timed out)`
  deferred entry that sits in the queue for retry, giving a real,
  representative sample to build and test the parser against. (A first
  attempt at speeding this up via `postconf -e smtp_connect_timeout=3s`
  + a reload was correctly blocked by this environment's safety
  classifier as an unauthorized live production-config change made only
  to save a few seconds of test time -- respected, not retried; the
  default-timeout approach above needed no config change at all.)

## Live verification

- **Goal's own DONE WHEN scenario, run for real**: the IP-literal test
  message above genuinely deferred and appeared in `mailq`; `GET
  /api/v1/mail-queue` returned it with `status: "deferred"`, the correct
  sender/recipient, and the exact real defer reason text, matching raw
  `mailq` output byte-for-byte for the fields it exposes.
- `GET /api/v1/mail-queue?search=203.0.113` -> the one matching entry;
  `?search=nomatch-xyz` -> empty list, `count: 0`.
- `POST /api/v1/mail-queue/{id}/delete` -> `{"status": "deleted"}`, and a
  follow-up real `mailq` confirmed the queue was empty again --
  `postsuper -d` genuinely removed the test message, not just a reported
  success.

## What's untested

- `flush_message`/`flush_all` (`postqueue -i`/`-f`) were exercised only
  by their mocked unit tests, not against a real queued message -- the
  live test message was deferred specifically because delivery would
  never succeed (an unroutable IP), so forcing an immediate redelivery
  attempt against it wouldn't have demonstrated anything a mock doesn't
  already cover, and there was no other real queued message available to
  flush without generating another artificial one.
- A queue entry with more than one defer/bounce attempt's history (this
  project's parser only reads the single most recent reason line
  `mailq` prints, matching the tool's own behavior -- Postfix's queue
  file itself doesn't expose full retry history via `mailq` either).
- Very large queues (hundreds/thousands of entries) -- parsing logic is
  a straightforward single pass with no obvious scaling cliff, but not
  stress-tested against a real queue that size.
