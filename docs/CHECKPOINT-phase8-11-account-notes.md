# CHECKPOINT phase8-11 — Account notes (admin-only)

**Goal:** admin-only, append-only, timestamped, author recorded, never visible
to the customer. CRUD `/admin/accounts/{u}/notes`.

## What was built

- **Model** `AccountNote` (account_id, author, body, created_at).
- **Daemon** `daemon/handlers_notes.py` (ops `notes.add/list`): append-only —
  only add + list, **no update/delete op exists**, so the history is durable.
  `author` is recorded, body validated non-empty and length-capped.
- **API** `api/routers/notes.py` — GET/POST
  `/api/v1/admin/accounts/{u}/notes`, **both `require_admin`**. The author is
  taken from the acting admin's identity server-side, never from input.
- **Frontend**: admin AccountDetail **Notes** tab (`AccountNotes.jsx`) — add
  form + newest-first list with author + timestamp.

## "never visible to customer" (Done-When)

The notes are served **only** by an admin-only (`require_admin`) router; no
customer-scoped endpoint or dict anywhere includes notes. There is no
customer-facing route to `/admin/...`.

## Tests

`tests/test_notes.py` — 4 tests: add+list (author/body/timestamp), newest-first
order, empty-body rejection, missing-account. All green.
