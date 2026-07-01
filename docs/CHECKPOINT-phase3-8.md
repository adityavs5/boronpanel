# Checkpoint: Phase 3, Feature 8 — SSL dashboard

## What was built

- **Real, on-disk certificate inspection via the `cryptography` library**
  (already a project dependency, used elsewhere for password/token
  generation contexts) -- not `openssl x509` text-parsing. Expiry date,
  issuer, and days-remaining come straight from the actual X.509
  certificate file, independent of `Domain.ssl_status` (which only
  records "did Forgehost's own issue flow report success", not the
  certificate's real, independently-verifiable state) -- the dashboard's
  entire value proposition is showing the ground truth, not repeating
  what the DB already believes.
- **Status classification**: `valid` / `expiring` (within 30 days) /
  `expired` / `missing` (no cert file on disk at all), per the goal's
  exact vocabulary.
- **Auto-renewal status** is a real, verifiable fact, not a guess:
  certbot's own stock `certbot.timer` (ARCHITECTURE.md SS8 -- Forgehost
  does not reimplement a renewal scheduler) is checked via `systemctl
  is-active`, combined with whether a renewal config exists for that
  specific domain under `/etc/letsencrypt/renewal/` (created
  automatically by certbot on successful issuance). Both conditions
  must hold for `auto_renew: true`.
- **One-click issue/renew**: `daemon/ssl.py`'s existing `issue_certificate`
  (Phase f, unchanged in its challenge-selection logic) gained a `force`
  parameter that adds certbot's own `--force-renewal` flag -- without
  it, certbot's default behavior is to silently skip reissuing a
  certificate that isn't yet near expiry, which would make a "renew now"
  button on a healthy cert appear to do nothing. The plain "Issue"
  button (shown only when a cert is genuinely missing) never passes
  `force`, since there's nothing to force for a domain with no cert yet.
- **API**: `GET /accounts/{u}/ssl` (dashboard), `POST /accounts/{u}/
  domains/{d}/ssl/issue` (per the goal's literal shape) -- added as a
  new `account_api_router` in `api/routers/ssl_router.py` alongside the
  existing domain-scoped routes from Phase f (kept as-is, still used
  elsewhere), registered as a third router object in `api/main.py`
  since it doesn't fit that module's usual single `api_router`/
  `ui_router` pair. **UI**: a dedicated SSL dashboard page per account
  (status badges, expiry, issuer, auto-renew column, issue/renew
  buttons), linked from the account detail page.

## Testing

`tests/test_ssl.py` extended (14 new tests): `force`/no-`force` certbot
argument passing, and -- the more substantial addition -- a real X.509
certificate generator (`cryptography.x509.CertificateBuilder`, the same
library `daemon/ssl.py` itself uses to parse) producing genuine
certificates with controllable validity windows, used to test the
valid/expiring/expired classification against **actual parsed
certificate data**, not a mocked return value. Also covers the
missing-file case, the auto-renew conditional (both the renewal-config
check and the timer-active check), and the full dashboard assembly for
both a no-cert and a valid-cert domain. 470 tests passing (up from 461).

## Live verification performed

1. Created a real account + a real `sslip.io` subdomain (this project's
   established real-Let's-Encrypt-testing pattern, since this sandbox
   has no owned domain -- ARCHITECTURE.md SS8), confirmed the dashboard
   correctly reports `missing` before any cert exists.
2. Issued a **real** Let's Encrypt certificate via `ssl.issue` (HTTP-01,
   since this subdomain's zone isn't Forgehost-managed) -- confirmed the
   dashboard immediately reflects `valid`, a real expiry date, and a
   real issuer string.
3. **Cross-checked the dashboard's reported expiry date and issuer
   against `certbot certificates` and `openssl x509 -noout -enddate
   -issuer` independently** -- exact match on all three (the goal's own
   DONE WHEN bar: "shows real expiry dates matching certbot
   certificates").
4. Clicked "Renew now" (the `force=True` path) through the real UI over
   HTTP -- confirmed via the certbot log and a **before/after serial
   number comparison** (`certbot certificates`) that a genuinely new
   certificate was issued, not a silent no-op (which is exactly what
   would have happened without the `--force-renewal` flag, since the
   existing cert had 89 days of validity left).
5. `account.terminate` confirmed to remove the certificate from disk
   (Phase f's existing `terminate_account_certs` hook, unmodified by
   this feature -- re-verified it still works correctly here).

## What's untested / explicitly out of scope

- A genuinely `expiring` or `expired` real Let's Encrypt certificate
  (Let's Encrypt only issues 90-day certs, so provoking a real
  near-expiry/expired cert live would mean waiting ~60-90 days or
  manipulating the system clock -- the classification logic itself is
  tested against real, controllable-expiry X.509 certificates
  generated locally instead, which exercises the identical parsing/
  classification code path).
- DNS-01-issued certificates on the dashboard specifically (Feature 8's
  live test used HTTP-01; Phase f's own STATUS.md already documents an
  open question about DNS-01 validation on this sandbox that predates
  this feature and isn't re-litigated here).
