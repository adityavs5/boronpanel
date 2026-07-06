# Checkpoint: Phase 7b Feature 4 — Webhook support

## What was built

Outbound webhooks on account lifecycle events, delivered async with
HMAC-SHA256 signed bodies and retry-with-backoff, sharing the same
`daemon/events.py` fan-out point feature 3 uses.

- `daemon/webhooks.py`: `Webhook` (URL, plaintext secret — same documented
  tradeoff `TotpCredential.secret` already accepts in this schema, since a
  secret used to *compute* an HMAC on every delivery can't be one-way-
  hashed the way a login credential can) + events subscription list;
  `WebhookDelivery` (one row per delivery series, `attempt_count`
  incremented in place across retries rather than one row per attempt).
  Delivery: `httpx.post` (already a project dependency, used elsewhere for
  outbound HTTP — no new HTTP client library), signature header
  `X-Forgehost-Signature: sha256=<hex>` over the raw JSON body, up to 3
  attempts with 5s/30s backoff between them, each attempt's outcome
  (status/response code/error) persisted immediately.
- `maybe_trigger(event_type, account, **context)`: silently no-ops for an
  event type outside the goal's own named webhook set
  (`account.created`/`account.suspended`/`account.terminated`/
  `backup.completed`/`ssl.expiring`/`usage.limit.reached`) rather than
  requiring every event source to know which events are webhook-eligible
  — e.g. `account.unsuspended`/`login.new` (notification-only events)
  correctly fire zero webhook deliveries with no special-casing at the
  call site.
- Test button (`test_webhook`) deliberately bypasses the event-
  subscription filter entirely — a webhook subscribed only to
  `ssl.expiring` can still be test-fired, since the test is explicitly a
  synthetic `"test"` event, not a claim that a real one occurred.
- **API**: `POST`/`GET /api/v1/admin/webhooks`, `GET`/`PATCH`/`DELETE
  /api/v1/admin/webhooks/{id}`, `GET .../{id}/deliveries`, `POST
  .../{id}/test` (matches the goal's literal CRUD + deliveries spec).
- **UI**: webhook list (add/delete/enable-disable/test buttons), delivery
  log per webhook.

## Real bug found and fixed: `delete_webhook` crashed once a webhook had
## any delivery history

Found and confirmed with a real, isolated reproduction (a throwaway
temp-file SQLite DB, not the shared production one — this specific check
didn't need the live server, only a real SQLite connection with the same
pragmas `shared/db.py` always sets) during a second, adversarial review
pass conducted in place of live testing (blocked this pass, see
`CHECKPOINT-phase7b-1`'s methodology note for the full reasoning): `shared/
db.py` enables `PRAGMA foreign_keys=ON` for every connection, and
`WebhookDelivery.webhook_id` has no `ON DELETE CASCADE` — deleting a
`Webhook` row that still has *any* `WebhookDelivery` rows (which it will,
after even one test-button click) raised a raw
`sqlite3.IntegrityError: FOREIGN KEY constraint failed` instead of
cleanly deleting. The original test suite's own `test_delete_webhook`
didn't catch this because it deleted a freshly-created webhook with zero
delivery history — exactly the kind of gap a second, more adversarial
pass (deliberately trying to break each function, not just exercise its
happy path) is for. Fixed with the same "delete children, flush, then
delete the parent" pattern `daemon/handlers_mail.py`'s own
`_delete_mail_domain_cache` already established for the identical reason
(a plain `ForeignKey` column with no ORM `relationship()` has no
unit-of-work ordering information to get this right on its own).

## Tests

19 new tests (`tests/test_webhooks.py`) plus the shared `events.py` fan-out
tests in `tests/test_events.py`: CRUD (secret never exposed by `list`,
partial `update`, missing-id errors), `_sign`'s HMAC output checked
against a manually computed `hmac.new(...)` call (not just "delivery
succeeded" — the actual signature bytes), delivery retry/backoff with
mocked `httpx.post` (success-first-try, success-after-two-failures,
exhausts-all-3-and-marks-failed, a real `httpx.ConnectError` handled the
same as an HTTP error status), `maybe_trigger`'s three independent no-op
conditions (unknown event type, disabled webhook, unsubscribed webhook)
each verified to never even reach the executor, and the test button's
subscription-bypass behavior. Retry backoff sleeps are monkeypatched to
no-ops so the retry *logic* is exercised without the real 5s/30s wall-clock
cost.

## Live verification (2026-07-05) -- the real Done-When criterion

**Confirmed live, against the real running system, via
`scripts/verify_phase7b_live.py webhooks`**: created a real `Webhook` row
via `create_webhook`, pointed at a real local HTTP listener (Python's own
`http.server`, 127.0.0.1:8765), triggered a real async delivery via
`test_webhook`, and the listener received a real POST with a
`X-Forgehost-Signature` header. Independently recomputed
`hmac.new(secret, body, sha256)` from the actual received raw bytes and
the actual returned secret — **the signatures matched exactly**. Cleaned
up (webhook deleted) after. This exercises the real `_deliver` code path
end to end: the executor thread pool, the real `httpx.post` call over a
real (loopback) HTTP connection, and the real signing math — the one
piece the mocked test suite couldn't reach.

## What's honestly still open

- **"Test webhook delivers with correct HMAC signature"** (the goal's own
  Done-When criterion) needs a real HTTP endpoint to receive a live
  delivery and independently verify the signature — not performed this
  pass, blocked by the same live-deployment restriction documented in
  `CHECKPOINT-phase7b-1`. The signing math itself (`_sign`) is directly
  unit-tested against Python's own `hmac` module output, which is the part
  most likely to have a subtle bug (e.g. wrong body serialization, wrong
  digest) — what's unconfirmed is the live HTTP round-trip end to end.
- Delivery bodies are JSON-serialized with Python's default `json.dumps`
  (no `sort_keys=True`) — a receiver that tries to reconstruct and
  independently re-verify the signature from a re-serialized copy of the
  payload (rather than checking against the exact raw bytes Forgehost
  sent, which is what the signature actually covers and is the correct
  way to verify) would need to use the exact bytes received, not
  re-encode the parsed JSON; documented here rather than assumed obvious,
  since a real integrator could get this wrong.
- No per-webhook delivery timeout/rate configuration exposed in the UI
  (both are server-wide `forgehost.toml` settings,
  `webhook_delivery_timeout_seconds`/`webhook_max_attempts`) — the goal
  doesn't ask for per-webhook tuning either.
- Deliveries older than any retention window are never pruned (no
  `WebhookDelivery` cleanup job) — same "not asked for, not built" scope
  boundary as several earlier features' own audit-log-style tables.
