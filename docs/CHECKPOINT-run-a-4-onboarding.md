# CHECKPOINT run-a-4 — Client onboarding wizard

**Goal:** first customer login triggers a 3-step wizard (once only,
skippable). Step 1: account details. Step 2: DNS/NS setup with copy
buttons. Step 3: quick-start actions (add domain, create email, install
WordPress). API: `GET/PATCH /accounts/{u}/onboarding`.

## What was built

- **Model** `OnboardingState`: one row per account (lazy-created, no row =
  wizard not completed → shows on first login), `completed`/`skipped`
  bools + `completed_at`. `skipped` distinguishes "clicked through" from
  "dismissed" for operator visibility; either way the wizard never
  re-triggers.
- **Daemon** `daemon/onboarding.py` (ops `onboarding.get/set`):
  - `get` returns the gate state PLUS the server facts step 2 displays
    (`server_ip` from settings, the account's vanity `ns1/ns2.<primary>`
    pair matching what `handlers_dns.create_zone` actually provisions) —
    one API call powers the whole wizard, and the frontend never
    hardcodes the server IP.
  - `set` is **one-way and first-write-wins**: once completed, a second
    PATCH can't flip `skipped` or refresh `completed_at` (the wizard is
    once-only by spec; there is deliberately no un-complete path).
- **API** `api/routers/onboarding.py`: `GET/PATCH
  /api/v1/accounts/{username}/onboarding`, both `require_account_access`
  (customer manages their own; admin can read any).
- **Frontend** `components/onboarding/OnboardingWizard.jsx`, mounted in
  the customer Dashboard:
  - Renders nothing unless: onboarding GET says not completed AND role is
    `customer` AND **not impersonating** — an admin using login-as-user
    must not consume the real customer's one-time onboarding.
  - Step 1: username/primary domain (copy buttons), PHP version, disk
    quota, password-change tip. Step 2: server IP + vanity NS pair as
    copy rows (clipboard + toast, the existing `copyToClipboard` util),
    with registrar guidance that adapts when no domain exists yet.
    Step 3: three quick-action cards navigating to Domains/Email/Domains
    (WP installer lives in the domain detail) — navigating marks the
    wizard complete first, so it doesn't re-open on return.
  - Skip (any step) → `PATCH {completed:true, skipped:true}`; Finish →
    `{completed:true, skipped:false}`; closing the dialog counts as skip.
  - Panel name in the welcome heading comes from feature 3's branding.

## Verified live (isolated scratch stack, same rig as feature 3)

The live daemon lacks the new ops, so the scratch stack grew a **scratch
RPC daemon** — the repo's real `daemon/server.py` `OP_TABLE`/`handle_client`
served on a scratch Unix socket (none of `amain()`'s host-touching
bootstraps), scratch DB/config via `FORGEHOST_CONFIG`. Puppeteer, as a
seeded customer (`wizqa`, primary domain example.com), with screenshots:
- First dashboard load → **step 1 appears** (account details + copy rows).
- Next → **step 2** shows server IP `104.234.179.64` + `ns1/ns2.example.com`
  copy rows.
- Next → step 3 quick-start cards render; **Finish** → wizard closes.
- **Hard reload → wizard does not reappear** (server-side once-only, not
  localStorage).

## Tests

`tests/test_onboarding.py` — 9 tests: fresh account not-completed, server
facts in GET (ip + vanity NS derived from primary domain), no-domain →
no NS, complete-once-only (second write can't alter the first),
skip-records-skipped, completed:false no-op, unknown account,
cross-account 403 via the API (customer B can't read/complete customer
A's wizard), unauthenticated 401. Full suite green (see the run recorded
with this feature's commit).

## What's honestly still open

- Not deployed (`/opt/forgehost` needs user-approved deploy); the
  `onboarding_states` table lands automatically at next daemon start.
- "First login triggers": implemented as "first customer dashboard visit"
  — the dashboard is the customer landing page after login, so these are
  the same moment in practice; a customer deep-linking straight to
  another page on their literal first session would see it on their first
  dashboard visit instead.
