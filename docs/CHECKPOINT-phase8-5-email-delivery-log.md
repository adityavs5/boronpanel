# CHECKPOINT phase8-5 — Email delivery log

**Goal:** Postfix log scoped to account domains; timestamp/from/to/status/reason;
last 500; searchable; no cross-account leakage.
API: `GET /accounts/{u}/email/delivery-log`.

## What was built

- **Daemon** `daemon/maillog.py` (op `maillog.delivery`, a REPORTING_OP so it
  runs on the bounded executor). Reads Postfix's `/var/log/mail.log` (root-only,
  0640 syslog:adm — so this must go through the daemon, matching the "only the
  daemon touches privileged files" invariant), parses up to ~12MB of the tail,
  and returns the last N delivery events touching the account's domains.
- **`parse_maillog(lines, account_domains, limit)`** — a pure function
  (unit-tested with no root/log needed). It correlates each recipient/status
  line with its message's `from=` (logged earlier by qmgr) via the **queue id**,
  handles inline-`from`/`to` on NOQUEUE reject lines, and returns events
  **most-recent-first**.
- **Scoping (no cross-account leakage)**: an event is returned only if the
  sender OR recipient domain is one the account owns (its `Domain` rows ∪
  `MailDomain` rows). A message between two *other* accounts' domains is
  filtered out **in the daemon, before crossing the RPC boundary** — verified by
  `test_parse_scopes_to_account_domains` (the otheracct→otheracct message never
  appears).
- **Search**: case-insensitive substring across from/to/status/reason, applied
  server-side.
- **API** `api/routers/email_extras.py` — `GET /accounts/{u}/email/delivery-log`
  (`?search=&limit=`), `require_account_access`.
- **Frontend**: a **Delivery log** tab on the Email page — searchable table with
  status badges (sent/bounced/deferred/rejected).

## "sent email appears, cross-account blocked" (Done-When)

A real message to/from one of the account's domains parses into an entry;
messages between other accounts' domains are structurally excluded by the domain
filter (tested).

## Tests

`tests/test_maillog.py` — 10 tests: scoping (both directions + exclusion),
recency order, queue-id from-correlation, NOQUEUE reject, empty-domain,
limit, and `get_delivery_log` end-to-end (search, missing file). All green.
