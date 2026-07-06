# CHECKPOINT phase8-1 — Login as user (admin impersonation)

**Goal:** 5-min single-use impersonation token → scoped customer session;
"Return to admin" always visible; every impersonation audit-logged.
API: `POST /admin/accounts/{u}/impersonate`. **Effort: xhigh.**

## What was built

- **Models** (`shared/models.py`): `ImpersonationToken` (hashed, single-use,
  5-min TTL, `used_at`), `ImpersonationSession` (links the customer session to
  the impersonated account + the admin's original session to restore).
- **Daemon** (`daemon/impersonation.py`, ops in `server.py`):
  - `impersonation.create_token` — admin mints a token (SHA-256-hashed at rest,
    raw returned once), 5-min expiry, only for active/suspended accounts.
  - `impersonation.redeem_token` — single-use (marked `used_at` in the same
    transaction that creates the session), rejects expired/reused/wrong-admin/
    non-admin-redeemer. Creates a `Session` owned by the **admin** panel user
    (so it stays revocable/attributable) + an `ImpersonationSession` row.
  - `impersonation.end` — revokes the impersonation session and returns the
    admin's original session id to restore. Idempotent.
- **Auth downscoping** (`api/security.py`): `get_identity` detects an active
  `ImpersonationSession` for the cookie's session and returns a **customer**
  identity scoped to the impersonated account — so an impersonation session
  **cannot reach any admin endpoint** even though its underlying `Session`
  belongs to an admin panel user. `Identity` gained `impersonator` /
  `impersonated_account` (+ `is_impersonating`). The audit actor for actions
  taken while impersonating is the **admin's** username (attributable).
- **API** (`api/routers/impersonation.py`): `POST /api/v1/admin/accounts/{u}/
  impersonate` (mint), `POST /api/v1/impersonate/redeem` (swap cookie to the
  customer session; admin session left intact for return), `POST /api/v1/
  impersonate/return` (end + restore admin cookie, or delete cookie if the
  admin session lapsed → SPA bounces to login).
- **whoami** now reports `impersonating`/`impersonator` (server-derived, never
  client-trusted) so the banner survives a hard refresh.
- **Frontend**: "Login as user" button on the admin account overview
  (`AccountDetail.jsx`); a persistent amber **Return-to-admin banner**
  (`ImpersonationBanner.jsx`) rendered above the whole shell; `AppShell`
  reconciles identity via `whoami` on mount; auth store gained
  `impersonating`/`syncIdentity`/`returnToAdmin`.

## Audit trail

`impersonation.create_token`, `impersonation.redeem_token`,
`impersonation.end`, and every RPC issued while impersonating are written to
the audit log by `server.py`'s `dispatch()` — actor = the admin's username in
all cases (the impersonation identity carries the admin username).

## Security properties

- Single-use enforced by `used_at` (a signed token alone can't guarantee this);
  5-min TTL; hashed at rest; only the issuing admin can redeem.
- Impersonation identity is customer-scoped to exactly one account
  (`require_admin` fails, `require_account_access` restricts) — verified by test.
- Ending impersonation revokes the session, so a stale cookie is dead
  immediately (verified by `test_ended_impersonation_session_is_dead`).

## Tests

`tests/test_impersonation.py` — 15 tests (create/redeem/end lifecycle,
single-use, expiry, wrong-admin, non-admin-redeemer, and the get_identity
downscoping + the dead-after-return property). All green.

## Honestly still open

- Not live-verified end-to-end against a running panel yet (done at phase end).
- The impersonation session TTL is 2h; there is no idle-timeout beyond that
  (an admin who walks away mid-impersonation keeps it until TTL or return).
