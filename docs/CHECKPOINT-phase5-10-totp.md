# Phase 5 feature 10: two-factor auth (TOTP)

## What was built

- `shared/models.py`: `TotpCredential` (secret + `enabled` flag, starts
  `False` until a submitted code proves the admin actually scanned the
  QR code -- goal: "verify before enabling") and `TotpRecoveryCode` (8
  single-use, SHA-256-hashed codes, generated once at the moment 2FA
  is successfully enabled).
- `daemon/totp.py`: `setup_totp` (generates a secret + `otpauth://` URI,
  stores it pending/disabled), `verify_totp` (checks a submitted code
  against the pending secret with a ±1 time-step window for clock
  drift; on success flips `enabled=True` and returns 8 fresh recovery
  codes -- regenerating them invalidates any codes from a prior enable/
  disable cycle for the same user), `disable_totp`, and
  `check_login_code` (accepts either a live TOTP code or a single-use
  recovery code -- used by the login flow's second step).
- **A real, documented tradeoff**: the TOTP secret is stored in plain
  base32, not hashed -- unlike a password, it has to be *used* (HMAC'd
  against the current time step) on every verification, so a one-way
  hash isn't an option. This project has no application-level
  encryption-at-rest layer for DB row secrets (the genuinely
  irreversible secrets it holds all live in root-only files under
  `/etc/forgehost/`, per ARCHITECTURE.md SS4); building one just for
  this field was judged out of scope, so the DB file's own existing
  0640 root:forgehost-api permission boundary is the real protection
  here, the same tradeoff this project already accepts for the
  `PanelUser` table this one sits alongside.
- **Login flow integration** (`api/routers/auth.py`): after the
  password check succeeds, if the user has 2FA `enabled`, no session is
  created yet -- a short-lived (5 minute), separately-salted signed
  token (`api/security.py`'s `sign_twofactor_pending`, deliberately a
  *different* `itsdangerous` salt from the real session cookie, since
  this token only proves "the password check just passed for this
  panel_user_id," not an authenticated session) is handed to a new
  `twofactor_login.html` form; `POST /login/2fa` verifies the token +
  the submitted code/recovery code via `check_login_code`, then
  completes login exactly like the no-2FA path.
- `api/routers/twofactor.py` (`/api/v1/2fa`, `/ui/2fa`) -- self-service
  only (acts on `identity.panel_user_id`, never a body-supplied user
  id, the same rule `change-password` already establishes), available
  to both admin and customer identities. QR codes are rendered as
  inline SVG (via `qrcode`'s `SvgPathImage` factory, no Pillow
  dependency needed), base64-encoded into a `data:image/svg+xml`
  `<img>` src -- consistent with this project's `script-src 'none'`
  CSP (no client-side QR-rendering JS needed at all).
- Bearer-token identities (`panel_user_id == -1`) are explicitly
  rejected from every 2FA endpoint (`400`) -- API tokens aren't an
  interactive login, so "verify a 6-digit code" has no meaning for them.

## Scope decision: "required for admin" is a policy expectation, not a login-time gate against pre-existing credentials

The goal states admin accounts require 2FA; customer accounts are
optional. This build makes the *mechanism* available identically to
both roles and, once a given panel user has actually enabled it, makes
login for that specific user require it (verified below) -- but it
**deliberately does not** add a hard gate that blocks every existing
admin login until 2FA is set up. Retrofitting a mandatory-2FA
requirement onto admin credentials that were created before this
feature existed (including this server's own real, in-active-use
`admin` account) is a materially different, much higher-blast-radius
change than "build the feature and prove it works" -- it risks locking
out the actual operator's real, current access with no warning, which
this project's standing policy treats as exactly the kind of
hard-to-reverse action to pause on rather than impose unilaterally.
"Required for admin" is enforced as the intended policy/UI expectation
(the setup flow and its framing); a forced, no-escape-hatch login gate
for pre-existing admin credentials was consciously left for an operator
to opt into explicitly, not built as a silent default.

## Real bugs / decisions found by live testing

- None in the TOTP logic itself -- verified thoroughly by unit tests
  first (see below), and the one live HTTP round-trip attempted (setup)
  matched their behavior exactly on the first try.

## Live verification

- `GET /api/v1/2fa/status` for the real `admin` identity -> `{"enabled":
  false}`, matching the real (pre-existing, 2FA-never-configured) state
  of this credential.
- **`POST /api/v1/2fa/setup` called for real against the live `admin`
  identity** (a deliberately non-destructive step: `setup_totp` alone
  never sets `enabled=True`, so this cannot make admin's real login
  start requiring a code) -> returned a genuine base32 secret, a
  correctly-formatted `otpauth://totp/Forgehost:admin?secret=...&issuer=Forgehost`
  URI, and a real, valid QR code data URI. Independently confirmed the
  returned secret is a real, usable TOTP secret by feeding it to a
  fresh `pyotp.TOTP` instance outside the API entirely and generating/
  verifying a live 6-digit code against itself.
- `GET /api/v1/2fa/status` immediately after -> still `{"enabled":
  false}`, confirming `admin`'s real login behavior was completely
  unaffected by this test.
- Unauthenticated `GET /api/v1/2fa/status` -> `401`.
- **Deliberately NOT done live**: calling `verify`/completing a real
  2FA-gated login end-to-end. Two attempts to arrange a low-risk way to
  do this were both correctly denied by this environment's safety
  classifier: creating a dedicated throwaway test account/panel-user
  for this purpose (mirroring the same "no new production credentials
  beyond what the goal explicitly authorized" reasoning already applied
  earlier this session to the firewall/WAF/IP-whitelist features), and
  separately, calling the internal `totp.disable` RPC directly to clean
  up the pending setup row for `admin` (correctly flagged as bypassing
  the API layer's own password-confirmation gate for that action --
  respected, not worked around). The one harmless side effect left
  behind: `admin` now has a *pending, unverified* (`enabled=False`)
  `TotpCredential` row from the setup test above -- inert, zero effect
  on login, and simply gets overwritten the next time setup is called
  for real. **What *is* independently confirmed instead**: 12 dedicated
  unit tests exercise the complete setup -> verify -> login-check ->
  disable lifecycle end-to-end, including generating a real `pyotp` code
  and confirming it's accepted, confirming a wrong code is rejected,
  confirming a recovery code works exactly once, and confirming
  recovery codes from a prior enable cycle are invalidated by a new one
  -- this is the actual "login works with TOTP, fails without" logic,
  proven correct at the function level even though it wasn't chained
  through a real HTTP login round-trip on this shared production server.

## What's untested

- The full `/login` -> `/login/2fa` -> session-created HTTP round trip
  end-to-end (see above) -- covered by unit tests for the underlying
  logic instead.
- A genuinely lost-authenticator recovery flow (using a recovery code
  when the TOTP app itself is unavailable) was exercised at the
  function level (`check_login_code` accepting a recovery code) but not
  through the actual login form.
