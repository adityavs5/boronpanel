# CHECKPOINT phase8-3 — Parked (alias) domains

**Goal:** alias domains → same docroot + PHP context; OLS vhost alias, DNS A
record, SSL issuable per parked domain. CRUD `/accounts/{u}/parked-domains`.

## What was built

- **Model** `ParkedDomain` (parked_domain unique, target_domain, account_id).
- **Daemon** `daemon/parked.py` (ops `parked.add/list/remove`): a parked domain
  is created as an ordinary `Domain` row with **kind='parked'** whose docroot
  points at the target domain's docroot — so it reuses the entire
  one-vhost-per-domain + shared-account-extProcessor machinery. That gives
  "same docroot + PHP context" for free, and makes **SSL issuable per parked
  domain with zero extra code** (`ssl.issue` keys on the Domain row; the shared
  docroot's `.well-known` serves the HTTP-01 challenge). A `ParkedDomain`
  bookkeeping row records the target.
- **DNS**: an A record is auto-created in a Forgehost-managed parent zone (same
  as `handlers_domain.add_domain`), rolled back with the DB rows on OLS failure.
- **Lifecycle**: `parked.add` compensates (deletes the Domain + ParkedDomain
  rows + DNS) if `ols.provision_vhost` fails; `remove` deletes rows + vhost +
  DNS; a TERMINATE_HOOK sweeps `ParkedDomain` rows on account termination (the
  parked `Domain` rows/vhosts are already handled by `ols.terminate_vhost`).
- **Guards**: rejects a parked_domain already in use, and parking onto another
  parked domain.
- **API** `api/routers/parked.py` — GET/POST/DELETE
  `/api/v1/accounts/{u}/parked-domains[/{parked_domain}]`.
- **Frontend**: "Parked (alias) domains" card on the Domains page (add dialog
  with a target-domain picker defaulting to the primary; per-row remove).

## "serves same content, SSL issuable" (Done-When)

The parked domain's vhost renders with the *target's* docroot and the account's
extProcessor, so it serves byte-identical content. It is a real `Domain` row, so
`POST /accounts/{u}/domains/{parked}/ssl/issue` issues a cert for it.

## Tests

`tests/test_parked.py` — 8 tests: default-to-primary target, explicit target,
docroot sharing, duplicate/park-on-park rejection, list, remove, OLS-failure
rollback, terminate cleanup. All green (also ran the ols/domain suites clean).
