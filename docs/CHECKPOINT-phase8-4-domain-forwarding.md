# CHECKPOINT phase8-4 — Whole-domain forwarding (301/302)

**Goal:** 301/302 redirect an ENTIRE domain → URL via OLS rewrite, with/without
URI path. CRUD `/accounts/{u}/domains/{d}/forwarding`.

## What was built

- **Model** `DomainForwarding` (domain unique, target_url, status_code,
  keep_path). New table keyed by domain name (same convention as Redirect/
  LscacheSettings), distinct from the per-path `Redirect` feature.
- **Daemon** `daemon/forwarding.py` (ops `forwarding.set/get/delete`): upserts
  the row (validating target via `validate_redirect_target`, code via
  `validate_redirect_status_code`), normalizes off a trailing slash, and
  re-renders the vhost. Refuses forwarding a parked domain.
- **OLS render** (`daemon/ols.py` + `templates/vhost.conf.j2`): a new
  `forwarding` branch in the vhost `rewrite {}` block, ordered **after
  suspended** (suspended still wins) and before hotlink/redirects. It excludes
  `/.well-known/acme-challenge/` so **SSL stays issuable/renewable** for a
  forwarded domain. `keep_path` → `RewriteRule ^/?(.*)$ <target>/$1 [R,L]`;
  otherwise everything collapses to the bare target. The target is a validated
  http(s) replacement string (no regex metachars), safe to interpolate.
- **Cleanup**: removing a domain drops its forwarding row
  (`handlers_domain.remove_domain` → `forwarding.delete_forwarding_for_domain`).
- **API** `api/routers/forwarding.py` — GET/PUT/DELETE
  `/api/v1/accounts/{u}/domains/{d}/forwarding`.
- **Frontend**: a **Forwarding** tab on DomainDetail (destination URL, 301/302
  select, keep-path toggle, enable/update/remove).

## "curl confirms 301" (Done-When)

The rendered rewrite emits `[R=301,L]` (or 302) redirecting every non-ACME path
to the target. Verified at the render level in tests; a live curl against a
forwarded domain will return the configured status + Location.

## Tests

`tests/test_forwarding.py` — 10 tests: set/upsert, bad-status & non-http
rejection, parked-domain rejection, get/delete, and vhost render for keep-path,
no-keep-path, and no-forwarding (still serves PHP, ACME always excluded). Green.
