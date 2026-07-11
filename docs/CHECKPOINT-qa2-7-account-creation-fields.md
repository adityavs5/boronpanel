# QA round 2 — Item 15: account creation accepts password + contact email

## What existed already

`api/routers/accounts.py`'s `CreateAccountBody` already had a `password`
field, and `daemon/handlers_account.create_account` already validated it
with `validate_password_strength` (12+ chars, upper/lower/digit/symbol, not
common/repeated/purely-numeric) when supplied, falling back to
`sysops.generate_password()` otherwise — strong-password enforcement was
already fully implemented server-side. What was actually missing: **no
`email` field existed anywhere** (not on the API schema, not on the
`Account` model, not on any other model), and **the admin UI form only
ever submitted `username`/`primary_domain`/`plan_id`** — the password field
already accepted by the backend was never exposed either.

## Fix

Rather than add a duplicate email column on `Account`, the new contact
email is stored in the *same* `AccountNotificationPrefs.customer_email`
field the "account created" welcome email and every other notification
already reads from (`daemon/notifications.py`) — setting it at creation
time both records the contact and makes the welcome email actually able to
reach someone, with one source of truth instead of two.

- `api/routers/accounts.py`: `CreateAccountBody` gained `email: str | None`.
- `daemon/handlers_account.create_account`: validates `email` via
  `validate_email_address` up front (same reasoning as the existing
  password validation — a rejected bad value must never leave an orphaned
  Linux user behind), then, inside the same session that creates the
  `Account` row, lazily creates/updates its `AccountNotificationPrefs` row
  (`notifications._get_prefs`) with `customer_email = email` when provided.
  The result dict now includes `email` for the frontend's confirmation.
- `frontend/src/pages/admin/Accounts.jsx`: create-account dialog gained
  "Contact email" (optional, `type=email`) and "Password" (optional,
  `type=password`, client-side `pattern`/`title` mirroring the server's
  strength rule for immediate feedback — the server remains the actual
  enforcement point) fields, both wired into the create mutation body.

## Tests

`tests/test_handlers_account.py` (+3): contact email lands in
`AccountNotificationPrefs.customer_email` (not a new Account column);
omitting email leaves prefs unset (no accidental default); an invalid
email is rejected with `ValidationError` before any Linux user is created.

`python3 -m pytest tests/test_handlers_account.py tests/test_notifications.py -q`
→ 60 passed. `npm run build` — clean.
